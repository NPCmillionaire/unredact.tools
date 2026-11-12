"""Visual-layer tests: OCR leaks, codes/boarding passes, pixelation, printer dots, PRNU."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

vision = pytest.importorskip("unredact_tools.vision")
if not vision.available():
    pytest.skip("vision extra not installed", allow_module_level=True)

from unredact_tools.image import VisualOpts  # noqa: E402
from unredact_tools.scanner import sanitize, scan  # noqa: E402

HERE = Path(__file__).parent
needs_ocr = pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")


@pytest.fixture(scope="session")
def vf(tmp_path_factory):
    d = tmp_path_factory.mktemp("vf")
    subprocess.run([sys.executable, str(HERE / "make_vision_fixtures.py"), str(d)], check=True)
    return d


def titles(r):
    return [f.title for f in r.findings]


def test_boarding_pass_decoded(vf):
    r = scan(str(vf / "screenshot.png"), ocr=False)
    bp = [f for f in r.findings if f.title.startswith("Boarding pass")]
    assert bp and bp[0].severity == "critical"
    assert "ABC123" in bp[0].detail and "CLE->PHX" in bp[0].detail


@needs_ocr
def test_ocr_finds_sensitive_text(vf):
    t = " ".join(titles(scan(str(vf / "screenshot.png"))))
    for label in ("Social Security", "Payment card", "Email address", "Phone number",
                  "Street address", "API key"):
        assert label in t, label


@needs_ocr
def test_visual_sanitize_clears_screenshot(vf):
    out = sanitize(str(vf / "screenshot.png"), str(vf / "shot_clean.png"),
                   visual=VisualOpts(redact_codes=True, redact_text=True))
    left = [f for f in scan(out).findings if f.severity in ("critical", "high")]
    assert left == [], [f.title for f in left]


def test_qr_code(vf):
    r = scan(str(vf / "qr_photo.jpg"), ocr=False)
    assert any("wa.me" in f.detail for f in r.findings)
    out = sanitize(str(vf / "qr_photo.jpg"), str(vf / "qr_clean.jpg"),
                   visual=VisualOpts(redact_codes=True))
    assert not any("wa.me" in f.detail for f in scan(out, ocr=False).findings)


def test_pixelation_flagged_and_region_redaction(vf):
    r = scan(str(vf / "pixelated.jpg"), ocr=False)
    assert any("Pixelated" in t for t in titles(r))
    out = sanitize(str(vf / "pixelated.jpg"), str(vf / "pix_clean.jpg"),
                   visual=VisualOpts(regions=[(0, 20, 640, 220)]))
    assert not any("Pixelated" in t for t in titles(scan(out, ocr=False)))


def test_no_false_pixelation_on_clean_images(vf):
    for name in ("screenshot.png", "qr_photo.jpg", "camA_0.jpg"):
        assert not vision.detect_pixelation(vision.load((vf / name).read_bytes())), name


def test_printer_dots_detected_and_removed(vf):
    r = scan(str(vf / "scan_with_dots.png"), ocr=False)
    assert any("tracking dots" in t for t in titles(r))
    out = sanitize(str(vf / "scan_with_dots.png"), str(vf / "dots_clean.png"))
    assert not any("tracking dots" in t for t in titles(scan(out, ocr=False)))


def test_camera_fingerprint_and_desync(vf):
    refs = [vision.load((vf / f"camA_{i}.jpg").read_bytes()) for i in range(4)]
    K = vision.build_fingerprint(refs)
    a = vision.load((vf / "camA_test.jpg").read_bytes())
    b = vision.load((vf / "camB_test.jpg").read_bytes())
    assert vision.match_fingerprint(K, a)["z"] > 8
    assert vision.match_fingerprint(K, b)["z"] < 4
    assert vision.match_fingerprint(K, vision.desync(a))["z"] < 4
