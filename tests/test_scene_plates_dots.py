"""Printer-dot decoding, license plates, sun geolocation, AI scene pass (mocked)."""
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

vision = pytest.importorskip("unredact_tools.vision")
if not vision.available():
    pytest.skip("vision extra not installed", allow_module_level=True)

from unredact_tools import printerdots as pd  # noqa: E402
from unredact_tools import scene, sun  # noqa: E402
from unredact_tools.image import VisualOpts  # noqa: E402
from unredact_tools.model import Report  # noqa: E402
from unredact_tools.scanner import sanitize, scan  # noqa: E402

HERE = Path(__file__).parent
needs_ocr = pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")


@pytest.fixture(scope="session")
def vf(tmp_path_factory):
    d = tmp_path_factory.mktemp("vf2")
    subprocess.run([sys.executable, str(HERE / "make_vision_fixtures.py"), str(d)], check=True)
    return d


# ------------------------------------------------------------- printer dots
def test_docucolor_codec_roundtrip_and_single_error_correction():
    m = pd.encode_docucolor(7, 23, 31, 12, 2029, "00654321", col15=5)
    d = pd.decode_docucolor(m)
    assert d["parity"] == "parity OK" and d["serial"] == "654321"
    assert d["printed"] == "2029-12-31 23:07" and d["column15"] == 5
    m[4, 9] ^= True
    d = pd.decode_docucolor(m)
    assert d["parity"].startswith("corrected") and d["printed"] == "2029-12-31 23:07"


def test_printer_dots_decoded_from_scan(vf):
    r = scan(str(vf / "scan_with_dots.png"), ocr=False)
    hit = [f for f in r.findings if "DECODED" in f.title]
    assert hit and "21052857" in hit[0].detail and "2026-11-12 13:45" in hit[0].detail


def test_printer_dots_decode_survives_rotation(vf):
    import cv2
    img = vision.load((vf / "scan_with_dots.png").read_bytes())
    for rot in (cv2.ROTATE_180, cv2.ROTATE_90_CLOCKWISE):
        dec = pd.decode_page(cv2.rotate(img, rot), vision.yellow_dot_mask)
        assert dec["decoded"] and dec["serial"] == "21052857"


# ------------------------------------------------------------------ plates
@needs_ocr
def test_license_plates_found_and_redacted(vf):
    r = scan(str(vf / "street.jpg"))
    plates = sorted(f.title for f in r.findings if f.title.startswith("License plate"))
    assert any("JKL4821" in p for p in plates) and any("BX52KRT" in p for p in plates)
    out = sanitize(str(vf / "street.jpg"), str(vf / "street_clean.jpg"),
                   visual=VisualOpts(redact_plates=True))
    assert not any(f.title.startswith("License plate") for f in scan(out).findings)


@needs_ocr
def test_no_plate_false_positives(vf):
    for name in ("screenshot.png", "qr_photo.jpg", "scan_with_dots.png", "camA_0.jpg"):
        assert not vision.detect_plates(vision.load((vf / name).read_bytes())), name


# --------------------------------------------------------------------- sun
def test_solar_position_reference_values():
    # summer solstice, solar noon at Youngstown OH: max elevation = 90 - 41.1 + 23.44
    el, az = sun.solar_position(datetime(2026, 6, 21, 17, 23, tzinfo=timezone.utc), 41.1, -80.65)
    assert abs(el - 72.3) < 0.3 and abs(az - 180) < 3


def test_shadow_geolocation_round_trip():
    when = datetime(2026, 11, 10, 14, 30, tzinfo=timezone(timedelta(hours=-5)))
    el, az = sun.solar_position(when, 41.1, -80.65)
    pts, _ = sun.locate(when, el, shadow_bearing=(az + 180) % 360)
    assert abs(pts[0][0] - 41.1) < 1.0 and abs(pts[0][1] + 80.65) < 1.0


def test_sun_cli(capsys):
    from unredact_tools.cli import main
    with pytest.raises(SystemExit):
        main(["sun", "--time", "2026-11-10T14:30-05:00", "--shadow-ratio", "1:2.38",
              "--shadow-bearing", "37", "--no-color"])
    out = capsys.readouterr().out
    assert "best match: 41." in out and "-80." in out


# ------------------------------------------------------------------- scene
REPLY = {"clues": [{"type": "reflection", "what": "photographer visible in sunglasses",
                    "reveals": "face of the person taking the photo", "severity": "high",
                    "box": [100, 50, 40, 20]},
                   {"type": "landmark", "what": "Mahoning River bridge", "reveals": "city",
                    "severity": "high"}],
         "location_guess": {"place": "Youngstown, OH", "confidence": "medium", "why": "bridge"},
         "time_guess": "late afternoon, autumn"}


def test_scene_ollama_mocked(vf, monkeypatch):
    seen = {}

    def fake_post(url, payload, headers, timeout=180):
        seen["url"], seen["payload"] = url, payload
        return {"message": {"content": json.dumps(REPLY)}}

    monkeypatch.setattr(scene, "_post", fake_post)
    data = scene.analyze((vf / "street.jpg").read_bytes(), "ollama")
    assert seen["url"].endswith("/api/chat") and seen["payload"]["messages"][0]["images"]
    r = Report("x", "image/jpeg")
    scene.add_findings(r, data)
    titles = [f.title for f in r.findings]
    assert any("reflection" in t for t in titles) and any("Youngstown" in t for t in titles)


def test_scene_anthropic_mocked_and_box_scaling(monkeypatch):
    import io

    from PIL import Image
    big = Image.new("RGB", (3136, 2000), "gray")
    buf = io.BytesIO()
    big.save(buf, "JPEG")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def fake_post(url, payload, headers, timeout=180):
        assert url == "https://api.anthropic.com/v1/messages" and headers["x-api-key"] == "test-key"
        assert payload["messages"][0]["content"][0]["type"] == "image"
        return {"content": [{"type": "text", "text": "```json\n" + json.dumps(REPLY) + "\n```"}]}

    monkeypatch.setattr(scene, "_post", fake_post)
    data = scene.analyze(buf.getvalue(), "anthropic")
    assert data["clues"][0]["box"] == [200, 100, 80, 40]  # image was downscaled 2x for the model


def test_scene_requires_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(scene.SceneError):
        scene._anthropic("x", "m")
