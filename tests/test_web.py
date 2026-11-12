"""pytest: the web app scans, sanitizes, and leaves nothing on disk."""
import base64
import glob
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

from unredact_tools.scanner import scan  # noqa: E402
from unredact_tools.web import app as web  # noqa: E402

HERE = Path(__file__).parent
client = TestClient(web.app)


@pytest.fixture(scope="module")
def fx(tmp_path_factory):
    d = tmp_path_factory.mktemp("webfx")
    subprocess.run([sys.executable, str(HERE / "make_fixtures.py"), str(d)], check=True)
    return d


def leftovers():
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "unredact-*")))


def post(path, fx, name, **form):
    with open(fx / name, "rb") as f:
        return client.post(path, files={"file": (name, f)},
                           data={k: str(v).lower() for k, v in form.items()})


def test_index_and_headers():
    r = client.get("/")
    assert r.status_code == 200 and "unredact" in r.text
    assert r.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in r.headers["content-security-policy"]
    for asset in ("app.js", "app.css", "favicon.svg"):
        assert client.get(f"/static/{asset}").status_code == 200


def test_info_and_tip():
    assert client.get("/api/info").json()["max_mb"] == web.MAX_MB
    assert client.get("/api/tip").json()["tip"]


@pytest.mark.parametrize("name", ["photo.jpg", "filing.pdf", "memo.docx"])
def test_scan_reports_leaks_and_cleans_up(fx, name):
    before = leftovers()
    d = post("/api/scan", fx, name, extract=True).json()
    assert d["path"] == name and d["kind"] in ("image", "pdf", "office")
    assert sum(d["counts"][s] for s in ("critical", "high", "medium", "low")) > 0
    assert all("/tmp" not in f["source"] and "/tmp" not in f["detail"] for f in d["findings"])
    assert leftovers() == before


def test_scan_returns_recoverable_artifacts(fx):
    d = post("/api/scan", fx, "filing.pdf", extract=True).json()
    assert d["artifacts"] and all(base64.b64decode(a["data"]) for a in d["artifacts"])


@pytest.mark.parametrize("name", ["photo.jpg", "filing.pdf", "memo.docx", "leak.txt"])
def test_sanitize_returns_clean_file(fx, name, tmp_path):
    before = leftovers()
    d = post("/api/sanitize", fx, name).json()
    assert d["before"] > 0 and d["after"] == 0, d["remaining"]
    assert ".clean" in d["filename"]
    out = tmp_path / d["filename"]
    out.write_bytes(base64.b64decode(d["data"]))
    left = [f for f in scan(str(out)).findings
            if f.severity != "info" and f.category != "structure"]
    assert left == []
    assert leftovers() == before


def test_rejects_bad_input(fx):
    assert client.post("/api/scan", files={"file": ("x.bin", io.BytesIO(b""))}).status_code == 400
    assert post("/api/sanitize", fx, "photo.jpg", pdf_mode="nope").status_code == 400
    assert post("/api/sanitize", fx, "photo.jpg", regions="1,2,3").status_code == 400


def test_size_limit(fx, monkeypatch):
    before = leftovers()
    monkeypatch.setattr(web, "MAX_MB", 0)
    assert post("/api/scan", fx, "photo.jpg").status_code == 413
    assert client.post("/api/scan", files={"file": ("x.bin", io.BytesIO(b""))}).status_code == 400
    assert leftovers() == before


def test_hostile_filename_stays_in_tempdir():
    assert web._safe_name("../../etc/passwd") == "passwd"
    assert web._safe_name("..\\..\\win.ini") == "win.ini"
    assert web._safe_name("") == "upload"
    assert "/" not in web._safe_name("a/b<script>.jpg")
