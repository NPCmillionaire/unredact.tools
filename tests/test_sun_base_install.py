"""The sun-position note must work on a base install (numpy only ships with [vision])."""
import io
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone

import pytest

from unredact_tools import sun


def test_scalar_path_matches_numpy():
    np = pytest.importorskip("numpy")
    when = datetime(2026, 11, 10, 14, 30, tzinfo=timezone(timedelta(hours=-5)))
    lats, lons = np.meshgrid(np.arange(-80, 81, 10.0), np.arange(-170, 171, 20.0), indexing="ij")
    el, az = sun.solar_position(when, lats, lons)
    for i in range(lats.shape[0]):
        for j in range(lats.shape[1]):
            e, a = sun.solar_position(when, float(lats[i, j]), float(lons[i, j]))
            assert abs(e - el[i, j]) < 1e-9
            assert abs((a - az[i, j] + 180) % 360 - 180) < 1e-6


def test_photo_with_gps_and_timezone_scans_without_numpy(tmp_path):
    piexif = pytest.importorskip("piexif")
    from PIL import Image
    exif = piexif.dump({
        "Exif": {piexif.ExifIFD.DateTimeOriginal: b"2026:06:21 13:23:00", piexif.ExifIFD.OffsetTimeOriginal: b"-04:00"},
        "GPS": {piexif.GPSIFD.GPSLatitudeRef: b"N", piexif.GPSIFD.GPSLatitude: ((41, 1), (6, 1), (0, 1)),
                piexif.GPSIFD.GPSLongitudeRef: b"W", piexif.GPSIFD.GPSLongitude: ((80, 1), (39, 1), (0, 1))},
    })
    photo = tmp_path / "p.jpg"
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (9, 99, 199)).save(buf, "JPEG", exif=exif)
    photo.write_bytes(buf.getvalue())
    code = textwrap.dedent(f"""
        import sys
        sys.modules["numpy"] = None  # what a base install looks like
        from unredact_tools.scanner import scan
        r = scan({str(photo)!r}, visual=False)
        assert any(f.title == "Sun position at capture" for f in r.findings), [f.title for f in r.findings]
    """)
    subprocess.run([sys.executable, "-c", code], check=True)
