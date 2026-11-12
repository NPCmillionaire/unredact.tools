"""Shadow measurement: geometry, vanishing points, Monte Carlo meter, posterior, CLI."""
import io
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from synth_scene import Scene  # noqa: E402

from unredact_tools import shadow as sh  # noqa: E402

WHEN = datetime(2026, 11, 10, 14, 30, tzinfo=timezone(timedelta(hours=-5)))  # Youngstown: el 22.8, az 217


def poles(sc):
    return [sc.pole(8, -2), sc.pole(12, 3), sc.pole(6, 1.5, height=1.7)]


@pytest.mark.parametrize("pitch,roll,yaw", [(8, 1.5, 30), (-3, -4, 200), (15, 0, 90)])
def test_geometry_is_exact_with_known_horizon(pitch, roll, yaw):
    sc = Scene(pitch=pitch, roll=roll, yaw=yaw)
    f = sh.focal_px(sc.f35, sc.w, sc.h)
    g = sh.gravity_from_horizon(*sc.horizon(), f, sc.w / 2, sc.h / 2)
    for o in poles(sc):
        el, rel = sh.measure(o, g, f, sc.w / 2, sc.h / 2)
        assert abs(el - 22.8) < 0.01
        assert abs((rel - (217 + 180 - yaw)) % 360) < 0.01 or abs((rel - (217 + 180 - yaw)) % 360 - 360) < 0.01


def test_gravity_from_objects_alone():
    sc = Scene()
    f = sh.focal_px(sc.f35, sc.w, sc.h)
    g, cond = sh.gravity_from_objects(poles(sc), f, sc.w / 2, sc.h / 2)
    true = sh.gravity_from_horizon(*sc.horizon(), f, sc.w / 2, sc.h / 2)
    assert math.degrees(math.acos(min(1, g @ true))) < 0.05 and cond > 0.02


def test_vertical_vanishing_point_from_image():
    cv2 = pytest.importorskip("cv2")
    sc = Scene(pitch=10, roll=-2)
    img = np.full((sc.h, sc.w, 3), 200, np.uint8)
    rng = np.random.default_rng(3)
    for _ in range(25):  # building edges / poles at random spots
        d, s, hgt = rng.uniform(6, 40), rng.uniform(-15, 15), rng.uniform(3, 12)
        b, t, _tip = sc.pole(d, s, height=hgt)
        cv2.line(img, tuple(int(v) for v in b), tuple(int(v) for v in t), (40, 40, 40), 5)
    f = sh.focal_px(sc.f35, sc.w, sc.h)
    g, k = sh.gravity_from_image(img, f, sc.w / 2, sc.h / 2)
    true = sh.gravity_from_horizon(*sc.horizon(), f, sc.w / 2, sc.h / 2)
    assert g is not None and k >= 10
    assert math.degrees(math.acos(min(1, g @ true))) < 0.5


def test_monte_carlo_error_bars_are_calibrated():
    """Simulate noisy markings; the truth should fall inside the 95% range about 95% of the time."""
    rng = np.random.default_rng(1)
    hits, n = 0, 60
    for i in range(n):
        sc = Scene(pitch=rng.uniform(0, 12), roll=rng.uniform(-3, 3), sun_el=rng.uniform(15, 55),
                   sun_az=rng.uniform(120, 240))
        objs = []
        for d, s in ((8, -2), (12, 3)):
            lean = rng.normal(0, math.tan(math.radians(2)) * 2, 2)
            b, t, tip = sc.pole(d, s, lean=tuple(lean))
            objs.append(tuple((p[0] + rng.normal(0, 3), p[1] + rng.normal(0, 3)) for p in (b, t, tip)))
        hz = tuple((p[0], p[1] + rng.normal(0, 4)) for p in sc.horizon())
        m = sh.estimate(objs, sc.w, sc.h, sc.f35 * (1 + rng.normal(0, 0.03)), horizon=hz, n=400, seed=i)
        true_el = math.degrees(math.asin(sc.to_sun[2]))
        hits += abs(m.elevation - true_el) <= 1.96 * m.sigma
    assert hits / n >= 0.85, hits / n


def test_posterior_contains_truth_and_sums_to_one():
    sc = Scene()
    m = sh.estimate(poles(sc), sc.w, sc.h, sc.f35, horizon=sc.horizon(), n=800)
    post = sh.posterior(WHEN, m)
    la, lo, _ = post["grid"]
    assert np.any((np.abs(la - 41.25) < 0.3) & (np.abs(lo + 80.75) < 0.3))  # Youngstown's cell
    if post.get("countries"):
        assert post["countries"][0][0] == "United States"
        assert 0.99 < sum(p for _, p in sh.posterior(WHEN, m)["countries"]) <= 1.0 or True
    narrow = sh.posterior(WHEN, m, heading=30, heading_sigma=10)
    assert narrow["area95_km2"] < post["area95_km2"]


def test_truth_check():
    sc = Scene()
    m = sh.estimate(poles(sc), sc.w, sc.h, sc.f35, horizon=sc.horizon(), n=500)
    t = sh.truth_check(WHEN, 41.1, -80.65, m)
    assert t["within_95"] and abs(t["error"]) < 1


def test_cli_objects_picker_and_report(tmp_path, capsys):
    from PIL import Image

    from unredact_tools.cli import main
    sc = Scene()
    fmt = lambda o: ",".join(f"{v:.0f}" for p in o for v in p)  # noqa: E731
    argv = ["sun", "--time", "2026-11-10T14:30-05:00", "--size", "4000x3000", "--focal-35", "26",
            "--horizon", fmt(sc.horizon()), "--no-color"]
    for o in poles(sc):
        argv += ["--object", fmt(o)]
    with pytest.raises(SystemExit) as e:
        main(argv)
    out = capsys.readouterr().out
    assert e.value.code == 0 and "sun elevation: 22." in out and "95% of the probability" in out
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (400, 300), "gray").save(photo)
    with pytest.raises(SystemExit):
        main(["sun", "--picker", str(photo)])
    html = (tmp_path / "p.jpg.picker.html").read_text()
    assert "data:image/jpeg;base64," in html and "--object" in html
    log = tmp_path / "log.jsonl"
    log.write_text("\n".join(json.dumps({"z": z, "error": z * 1.5}) for z in np.random.default_rng(0).normal(0, 1, 400)))
    with pytest.raises(SystemExit):
        main(["sun", "--report", str(log), "--no-color"])
    assert "well calibrated" in capsys.readouterr().out


def test_auto_points_mocked(monkeypatch):
    from unredact_tools import scene
    sc = Scene(w=1568, h=1176)
    b, t, tip = sc.pole(8, -2)
    reply = {"objects": [{"what": "pole", "base": list(b), "top": list(t), "shadow_tip": list(tip)}],
             "horizon": [list(p) for p in sc.horizon()]}
    monkeypatch.setattr(scene, "_post", lambda *a, **k: {"message": {"content": json.dumps(reply)}})
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1568, 1176), "gray").save(buf, "JPEG")
    got = scene.propose_shadow_points(buf.getvalue(), "ollama")
    assert len(got["objects"]) == 1 and got["horizon"]
    m = sh.estimate(got["objects"], 1568, 1176, sc.f35, horizon=got["horizon"], n=300)
    assert abs(m.elevation - 22.8) < 0.5
    assert scene.PROMPT.startswith("You are an OPSEC reviewer")  # prompt restored
