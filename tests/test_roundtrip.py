"""pytest: every fixture leaks before sanitizing and leaks nothing after."""
import subprocess
import sys
from pathlib import Path

import pytest

from unredact_file.scanner import sanitize, scan

HERE = Path(__file__).parent
LEAKY = ["photo.jpg", "ai.png", "filing.pdf", "memo.docx", "budget.xlsx", "deck.pptx",
         "leak.txt", "clip.mp4"]


@pytest.fixture(scope="session")
def fx(tmp_path_factory):
    d = tmp_path_factory.mktemp("fx")
    subprocess.run([sys.executable, str(HERE / "make_fixtures.py"), str(d)], check=True)
    return d


def leaks(r):
    return [f for f in r.findings if f.severity not in ("info",) and f.category != "structure"]


@pytest.mark.parametrize("name", LEAKY)
def test_scan_finds_and_sanitize_removes(fx, name):
    src = fx / name
    assert leaks(scan(str(src))), f"{name}: expected leaks"
    out = sanitize(str(src), str(fx / f"clean_{name}"))
    assert leaks(scan(out)) == [], f"{name}: leaks remain after sanitize"


def test_pdf_text_mode_keeps_visible_text(fx):
    out = sanitize(str(fx / "filing.pdf"), str(fx / "t.pdf"), pdf_mode="text")
    assert leaks(scan(out)) == []
    r = scan(str(fx / "filing.pdf"))
    assert any("FAILED REDACTION" in f.title for f in r.findings)
    assert any("previous versions" in f.title for f in r.findings)


def test_docx_tracked_delete_recovered(fx):
    r = scan(str(fx / "memo.docx"))
    assert any("$4.2M" in f.detail for f in r.findings)


def test_help_banner_and_tip(capsys):
    from unredact_file.cli import main
    from unredact_file.tips import TIPS
    with pytest.raises(SystemExit) as e:
        main([])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "OPSEC TIP" in out and "commands:" in out and "examples:" in out
    assert any(t.split()[0] in out for t in TIPS)
    with pytest.raises(SystemExit):
        main(["tip", "--all", "--no-color"])
    assert len(capsys.readouterr().out.strip().splitlines()) == len(TIPS)
