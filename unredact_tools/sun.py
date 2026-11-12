"""Sun position and shadow geolocation.

The sun's elevation and compass bearing at a given instant depend only on where you
are, so a photo's timestamp plus a measurable shadow pins down where it was taken.
OSINT investigators (Bellingcat, SunCalc) do this by hand; this does it numerically.

  * solar_position(): NOAA solar-position equations (accurate to well under 1 degree
    for 1900-2100, which is far better than you can measure a shadow)
  * locate(): every place on Earth where the sun matched what the shadow shows
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np


def solar_position(dt: datetime, lat, lon):
    """(elevation_deg, azimuth_deg clockwise from north) for a UTC datetime.
    lat/lon may be scalars or numpy arrays."""
    if dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    dt = dt.astimezone(timezone.utc)
    jd = dt.timestamp() / 86400.0 + 2440587.5
    jc = (jd - 2451545.0) / 36525.0
    l0 = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360
    m = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    e = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    mr = math.radians(m)
    c = (math.sin(mr) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
         + math.sin(2 * mr) * (0.019993 - 0.000101 * jc) + math.sin(3 * mr) * 0.000289)
    true_long = l0 + c
    omega = 125.04 - 1934.136 * jc
    app_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))
    eps0 = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60
    eps = eps0 + 0.00256 * math.cos(math.radians(omega))
    decl = math.degrees(math.asin(math.sin(math.radians(eps)) * math.sin(math.radians(app_long))))
    y = math.tan(math.radians(eps / 2)) ** 2
    l0r = math.radians(l0)
    eot = 4 * math.degrees(y * math.sin(2 * l0r) - 2 * e * math.sin(mr)
                           + 4 * e * y * math.sin(mr) * math.cos(2 * l0r)
                           - 0.5 * y * y * math.sin(4 * l0r) - 1.25 * e * e * math.sin(2 * mr))
    minutes = dt.hour * 60 + dt.minute + dt.second / 60
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    tst = (minutes + eot + 4 * lon) % 1440
    ha = np.where(tst / 4 < 0, tst / 4 + 180, tst / 4 - 180)
    latr, declr, har = np.radians(lat), math.radians(decl), np.radians(ha)
    cosz = np.clip(np.sin(latr) * math.sin(declr) + np.cos(latr) * math.cos(declr) * np.cos(har), -1, 1)
    zen = np.degrees(np.arccos(cosz))
    elev = 90 - zen
    # atmospheric refraction (matters near the horizon)
    with np.errstate(divide="ignore", invalid="ignore"):
        te = np.tan(np.radians(elev))
        refr = np.where(elev > 85, 0,
                np.where(elev > 5, 58.1 / te - 0.07 / te ** 3 + 0.000086 / te ** 5,
                np.where(elev > -0.575, 1735 + elev * (-518.2 + elev * (103.4 + elev * (-12.79 + elev * 0.711))),
                         -20.772 / te))) / 3600
    elev = elev + refr
    sz = np.sin(np.radians(zen))
    with np.errstate(divide="ignore", invalid="ignore"):
        cos_az = (np.sin(latr) * np.cos(np.radians(zen)) - math.sin(declr)) / (np.cos(latr) * sz)
    az = np.degrees(np.arccos(np.clip(cos_az, -1, 1)))
    az = np.where(ha > 0, (az + 180) % 360, (540 - az) % 360)
    if elev.ndim == 0:
        return float(elev), float(az)
    return elev, az


def elevation_from_shadow(object_height: float, shadow_length: float) -> float:
    return math.degrees(math.atan2(object_height, shadow_length))


def compass(deg: float) -> str:
    pts = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return pts[int((deg % 360) / 22.5 + 0.5) % 16]


def locate(dt: datetime, elevation: float, shadow_bearing: float | None = None,
           elev_tol: float = 1.5, az_tol: float = 6.0, step: float = 0.5):
    """All lat/lon (grid of `step` degrees) where the sun was at `elevation` (and, if given,
    where shadows pointed toward `shadow_bearing`) at instant `dt`.
    Returns (points[N,2] lat/lon, errors[N]) sorted best-first."""
    lats = np.arange(-89.75, 90, step)
    lons = np.arange(-179.75, 180, step)
    LA, LO = np.meshgrid(lats, lons, indexing="ij")
    el, az = solar_position(dt, LA, LO)
    err = np.abs(el - elevation) / elev_tol
    ok = np.abs(el - elevation) <= elev_tol
    if shadow_bearing is not None:
        sun_az = (shadow_bearing + 180) % 360
        daz = np.abs((az - sun_az + 180) % 360 - 180)
        ok &= daz <= az_tol
        err = err + daz / az_tol
    pts = np.stack([LA[ok], LO[ok]], axis=1)
    e = err[ok]
    order = np.argsort(e)
    return pts[order], e[order]


def subsolar_point(dt: datetime):
    """Where the sun is directly overhead at `dt` (lat, lon)."""
    lats = np.arange(-24, 24.01, 0.1)
    lons = np.arange(-180, 180, 0.25)
    LA, LO = np.meshgrid(lats, lons, indexing="ij")
    el, _ = solar_position(dt, LA, LO)
    i = np.unravel_index(np.argmax(el), el.shape)
    return float(LA[i]), float(LO[i])


def tz_band(points, utc_offset_hours: float, slack_deg: float = 22.5):
    """Keep points whose longitude is plausible for a clock set to this UTC offset."""
    centre = utc_offset_hours * 15
    d = np.abs((points[:, 1] - centre + 180) % 360 - 180)
    return d <= slack_deg


def summarize(points, max_lines=8):
    """Human summary of a match set: lat range per longitude band."""
    if len(points) == 0:
        return []
    lines = []
    bands = np.arange(-180, 181, 30)
    for a, b in zip(bands[:-1], bands[1:]):
        sel = points[(points[:, 1] >= a) & (points[:, 1] < b)]
        if len(sel):
            lines.append((a, b, float(sel[:, 0].min()), float(sel[:, 0].max())))
    return lines[:max_lines] if len(lines) <= max_lines else lines


def to_geojson(points, errors, limit=5000) -> dict:
    feats = [{"type": "Feature", "properties": {"score": round(float(e), 3)},
              "geometry": {"type": "Point", "coordinates": [round(float(lo), 3), round(float(la), 3)]}}
             for (la, lo), e in zip(points[:limit], errors[:limit])]
    return {"type": "FeatureCollection", "features": feats}
