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



def test_about_and_contact():
    for path, text in (("/about", "What it does"), ("/contact", "mailto:"), ("/donate", "bc1q8hdtgw3f8fan587ey3jqy073d0wv2k07vu98d9")):
        r = client.get(path)
        assert r.status_code == 200 and text in r.text
        assert r.headers["cache-control"] == "no-store"


def test_caching_compression_and_www_redirect():
    assert client.get("/static/app.css").headers["cache-control"].startswith("public")
    assert client.get("/").headers["cache-control"] == "no-store"
    assert client.get("/", headers={"accept-encoding": "gzip"}).headers.get("content-encoding") == "gzip"
    assert "content-encoding" not in client.get("/api/tip", headers={"accept-encoding": "gzip"}).headers
    r = client.get("/about?x=1", headers={"host": "www.unredact.tools"}, follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "https://unredact.tools/about?x=1"


def test_head_icons_404_and_hsts():
    head = client.head("/")
    assert head.status_code == 200 and head.content == b"" and "strict-transport-security" in head.headers
    for path in ("/favicon.ico", "/apple-touch-icon.png"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["content-type"].startswith(("image/", "application/"))
    page = client.get("/no-such-page")
    assert page.status_code == 404 and "text/html" in page.headers["content-type"] and "noindex" in page.text
    assert client.get("/static/nope.css").status_code == 404
    api = client.get("/api/nope")
    assert api.status_code == 404 and api.json() == {"detail": "Not Found"}


def test_indexnow_key_file():
    r = client.get(f"/{web.INDEXNOW_KEY}.txt")
    assert r.status_code == 200 and r.text == web.INDEXNOW_KEY


def test_llms_txt():
    r = client.get("/llms.txt")
    assert r.status_code == 200 and r.text.startswith("# unredact.tools")
    assert all(f"https://unredact.tools/{slug})" in r.text for slug in web.GUIDES)


def test_robots_and_sitemap():
    r = client.get("/robots.txt")
    assert r.status_code == 200 and "Disallow: /api/" in r.text and "/sitemap.xml" in r.text
    assert "User-agent: GPTBot" in r.text and "User-agent: ClaudeBot" in r.text
    sm = client.get("/sitemap.xml")
    assert sm.status_code == 200 and sm.headers["content-type"].startswith("application/xml")
    for slug in ("guides", *web.GUIDES):
        assert f"<loc>https://unredact.tools/{slug}</loc>" in sm.text


def test_guides_reached_only_through_the_hub():
    for slug, g in web.GUIDES.items():
        r = client.get(f"/{slug}")
        assert r.status_code == 200 and "{{" not in r.text
        assert f'<link rel="canonical" href="https://unredact.tools/{slug}">' in r.text
        assert 'id="drop"' in r.text and 'id="card-tpl"' in r.text
    hub = client.get("/guides")
    assert hub.status_code == 200 and "{{" not in hub.text
    assert all(f'href="/{slug}"' in hub.text for slug in web.GUIDES)
    for path in ("/", "/about", "/contact", "/donate"):
        page = client.get(path).text
        assert 'href="/guides"' in page
        assert not any(f'href="/{slug}"' in page for slug in web.GUIDES)
    assert client.get("/no-such-page").status_code == 404


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


def contact(**form):
    data = {"name": "Ann", "email": "ann@example.com", "topic": "leak", "message": "hi"}
    return client.post("/api/contact", data={**data, **form}, follow_redirects=False)


def test_contact_form(monkeypatch):
    sent = []
    monkeypatch.setattr(web, "_send", sent.append)
    monkeypatch.setattr(web, "_recent", {})
    monkeypatch.setattr(web, "CONTACT_TO", "")
    assert contact().headers["location"] == "/contact?error=off#form"

    monkeypatch.setattr(web, "CONTACT_TO", "owner@example.com")
    r = contact(name="Ann\r\nBcc: x@evil.test")
    assert r.status_code == 303 and r.headers["location"] == "/contact?sent#form"
    msg = sent.pop()
    assert msg["To"] == "owner@example.com" and msg["Reply-To"] == "ann@example.com"
    assert "Missed leak" in msg["Subject"] and "\n" not in msg["Subject"] and msg["Bcc"] is None

    assert contact(website="spam").headers["location"] == "/contact?sent#form" and not sent
    assert "error=invalid" in contact(message="  ").headers["location"]
    assert "error=invalid" in contact(email="nope").headers["location"]
    assert contact(email="", name="").status_code == 303 and sent.pop()["Reply-To"] is None
    for _ in range(web.CONTACT_LIMIT):
        contact()
    assert "error=busy" in contact().headers["location"]



def test_counters(fx, tmp_path, monkeypatch):
    monkeypatch.setattr(web, "COUNTER", "")
    assert client.get("/api/info").json()["totals"] is None
    assert post("/api/sanitize", fx, "leak.txt").json()["totals"] is None

    path = tmp_path / "counts"
    path.write_text("7")  # older single-total file
    monkeypatch.setattr(web, "COUNTER", str(path))
    assert client.get("/api/info").json()["totals"] == {"checked": 7, "scrubbed": 7}
    assert post("/api/scan", fx, "leak.txt", fast=True).json()["totals"]["checked"] == 8
    assert post("/api/sanitize", fx, "photo.jpg").json()["totals"] == {"checked": 8, "scrubbed": 8}
    assert path.read_text() == "8 8"  # two integers, nothing else
    client.post("/api/sanitize", data={"pdf_mode": "bogus"},
                files={"file": ("a.txt", b"hi")})  # rejected requests don't count
    assert client.get("/api/info").json()["totals"] == {"checked": 8, "scrubbed": 8}


def test_relay_bumps_totals_and_skips_contact_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "COUNTER", str(tmp_path / "counts"))
    monkeypatch.setattr(web, "RELAY_TOKEN", "")
    assert client.post("/api/totals/checked").status_code == 403  # off when unset
    monkeypatch.setattr(web, "RELAY_TOKEN", "s3cret")
    auth = {"Authorization": "Bearer s3cret"}
    assert client.post("/api/totals/checked", headers={"Authorization": "Bearer nope"}).status_code == 403
    assert client.post("/api/totals/bogus", headers=auth).status_code == 404
    assert client.post("/api/totals/checked", headers=auth).json()["totals"] == {"checked": 1, "scrubbed": 0}
    assert client.post("/api/totals/scrubbed", headers=auth).json()["totals"] == {"checked": 1, "scrubbed": 1}

    monkeypatch.setattr(web, "_send", lambda msg: None)
    monkeypatch.setattr(web, "_recent", {})
    monkeypatch.setattr(web, "CONTACT_TO", "owner@example.com")
    for _ in range(web.CONTACT_LIMIT + 2):
        r = client.post("/api/contact", data={"message": "hi"}, headers=auth, follow_redirects=False)
        assert r.headers["location"] == "/contact?sent#form"


def test_trusted_test_traffic_is_not_counted(fx, tmp_path, monkeypatch):
    monkeypatch.setattr(web, "COUNTER", str(tmp_path / "counts"))
    monkeypatch.setattr(web, "RELAY_TOKEN", "s3cret")
    auth = {"Authorization": "Bearer s3cret"}
    for path in ("/api/scan", "/api/sanitize"):
        with open(fx / "leak.txt", "rb") as f:
            r = client.post(path, files={"file": ("leak.txt", f)}, headers=auth)
        assert r.status_code == 200 and r.json()["totals"] == {"checked": 0, "scrubbed": 0}


def test_security_txt():
    from datetime import datetime, timezone
    for path in ("/.well-known/security.txt", "/security.txt"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
        fields = dict(line.split(": ", 1) for line in r.text.splitlines() if ": " in line)
        assert "Contact" in fields and fields["Canonical"].endswith("/.well-known/security.txt")
        assert datetime.fromisoformat(fields["Expires"].replace("Z", "+00:00")) > datetime.now(timezone.utc)
    assert "BEGIN PGP PUBLIC KEY BLOCK" in client.get("/pgp.txt").text
