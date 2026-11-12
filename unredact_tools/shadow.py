"""Shadow measurement from a single photo, with honest error bars.

Given pixel positions of a vertical object's base, its top, and the tip of its shadow,
recover the sun's elevation with full perspective correction, then turn measurement
uncertainty into a probability map of where the photo was taken.

Geometry (pinhole camera, x right / y down / z forward):
  * focal length comes from EXIF (35 mm equivalent), principal point = image centre
  * gravity direction g comes from, in order of preference: a horizon the user marked,
    the vertical vanishing point of straight edges in the image, the objects themselves
    (two or more), or an assumed level camera (with a wide error bar)
  * the ground is the plane g.X = 1; base and shadow tip are back-projected onto it,
    the object's top is found along the vertical through the base, and the sun's
    elevation is atan2(top height, distance from below-the-top to the shadow tip)

Uncertainty: Monte Carlo over pixel noise, focal length, gravity, object lean and
ground slope. The resulting elevation distribution becomes a likelihood over the
globe (sun.py), multiplied by priors (time zone, where people live).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import sun as S


# ============================================================== camera basics
def focal_px(f35: float, width: int, height: int) -> float:
    """35 mm-equivalent focal length -> pixels (35 mm frame diagonal = 43.27 mm)."""
    return f35 / 43.27 * math.hypot(width, height)


def _ray(pt, f, cx, cy):
    return np.array([(pt[0] - cx) / f, (pt[1] - cy) / f, 1.0])


def _unit(v):
    return v / (np.linalg.norm(v) + 1e-12)


def gravity_from_horizon(p1, p2, f, cx, cy):
    g = _unit(np.cross(_ray(p1, f, cx, cy), _ray(p2, f, cx, cy)))
    return g if g[1] > 0 else -g


def gravity_from_objects(objects, f, cx, cy):
    """Vertical objects all point at the vertical vanishing point. Needs >= 2 objects
    that are not parallel in the image; returns (g, conditioning) or (None, 0)."""
    n = np.array([_unit(np.cross(_ray(o[0], f, cx, cy), _ray(o[1], f, cx, cy))) for o in objects])
    if len(n) < 2:
        return None, 0.0
    _, sv, vt = np.linalg.svd(n)
    g = vt[-1]
    g = g if g[1] > 0 else -g
    cond = float(sv[-2] / (sv[0] + 1e-12)) if len(sv) >= 2 else 0.0
    return g, cond


def gravity_from_image(img_bgr, f, cx, cy, min_inliers=10):
    """Vertical vanishing point from straight near-vertical edges (RANSAC).
    Returns (g, n_inliers) or (None, 0)."""
    import cv2
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    scale = min(1.0, 1400 / max(h, w))
    if scale < 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale)
    edges = cv2.Canny(gray, 60, 160)
    segs = cv2.HoughLinesP(edges, 1, np.pi / 360, 60, minLineLength=int(0.08 * max(gray.shape)),
                           maxLineGap=6)
    if segs is None:
        return None, 0
    normals = []
    for x1, y1, x2, y2 in np.asarray(segs, float).reshape(-1, 4) / scale:
        ang = abs(math.degrees(math.atan2(x2 - x1, y2 - y1)))  # 0 = vertical in image
        ang = min(ang, 180 - ang)
        if ang > 25:
            continue
        normals.append(_unit(np.cross(_ray((x1, y1), f, cx, cy), _ray((x2, y2), f, cx, cy))))
    if len(normals) < min_inliers:
        return None, 0
    N = np.array(normals)
    rng = np.random.default_rng(0)
    best, best_in = None, 0
    thr = math.sin(math.radians(0.7))
    for _ in range(300):
        i, j = rng.choice(len(N), 2, replace=False)
        g = np.cross(N[i], N[j])
        if np.linalg.norm(g) < 1e-6:
            continue
        g = _unit(g)
        inl = np.abs(N @ g) < thr
        if inl.sum() > best_in:
            best, best_in = inl, int(inl.sum())
    if best is None or best_in < min_inliers:
        return None, 0
    g = np.linalg.svd(N[best])[2][-1]
    g = g if g[1] > 0 else -g
    if g[1] < 0.5:  # that's not "down": the camera isn't pointed more than 60 deg off level
        return None, 0
    return g, best_in


# ============================================================ one measurement
def measure(obj, g, f, cx, cy, axis=None):
    """obj = (base, top, tip) pixels. Returns (elevation_deg, relative_bearing_deg) where the
    bearing is the shadow direction clockwise from the camera's heading; None if invalid."""
    rb, rt, rs = (_ray(p, f, cx, cy) for p in obj)
    db, ds = g @ rb, g @ rs
    if db <= 1e-6 or ds <= 1e-6:  # base or shadow tip is above the horizon
        return None
    pb, ps = rb / db, rs / ds
    a = -g if axis is None else axis
    A = np.stack([a, -rt], axis=1)
    (s, t), *_ = np.linalg.lstsq(A, -pb, rcond=None)
    if s <= 0 or t <= 0:
        return None
    top = pb + s * a
    hgt = 1.0 - g @ top
    if hgt <= 0:
        return None
    foot = top + hgt * g
    v = ps - foot
    length = np.linalg.norm(v)
    if length < 1e-9:
        return None
    elev = math.degrees(math.atan2(hgt, length))
    z = np.array([0.0, 0.0, 1.0])
    fwd = _unit(z - (z @ g) * g)
    right = np.cross(g, fwd)
    rel = math.degrees(math.atan2(v @ right, v @ fwd)) % 360
    return elev, rel


def _perturb(v, sigma_deg, rng):
    """Rotate unit vector v by a random small angle (isotropic, sigma in degrees)."""
    if sigma_deg <= 0:
        return v
    d = rng.normal(0, math.radians(sigma_deg), 3)
    d -= (d @ v) * v
    return _unit(v + d)


@dataclass
class Noise:
    px: float = 3.0          # how precisely the three points are placed (pixels)
    focal: float = 0.03      # relative focal-length error
    gravity: float = 1.0     # degrees; set from the gravity source
    lean: float = 2.0        # degrees the "vertical" object may lean
    slope: float = 1.0       # degrees the ground may tilt along the shadow
    heading: float = 10.0    # degrees; phone compasses are poor


@dataclass
class Measurement:
    elevation: float
    sigma: float
    samples: np.ndarray
    per_object: list
    gravity_source: str
    pitch: float
    roll: float
    rel_bearing: float | None = None
    bearing_sigma: float | None = None
    warnings: list = field(default_factory=list)


def _circ_mean_std(deg):
    r = np.radians(deg)
    c, s = np.cos(r).mean(), np.sin(r).mean()
    R = min(1.0, math.hypot(c, s))
    return math.degrees(math.atan2(s, c)) % 360, math.degrees(math.sqrt(max(0.0, -2 * math.log(R + 1e-12))))


def estimate(objects, width, height, f35, horizon=None, image=None, noise: Noise | None = None,
             n=3000, seed=0) -> Measurement:
    """Monte Carlo shadow measurement. objects = [(base, top, tip), ...] in pixels."""
    noise = noise or Noise()
    rng = np.random.default_rng(seed)
    f0, cx, cy = focal_px(f35, width, height), width / 2, height / 2
    warnings = []

    def solve_gravity(objs, f, hz):
        if hz is not None:
            return gravity_from_horizon(hz[0], hz[1], f, cx, cy), "horizon you marked", 0.5
        if image is not None:
            g, k = gravity_from_image(image, f, cx, cy)
            if g is not None:
                return g, f"vertical edges in the photo ({k} lines)", 1.0
        g, cond = gravity_from_objects(objs, f, cx, cy)
        if g is not None and cond > 0.02 and g[1] > 0.5:
            return g, "the objects themselves", 2.0
        return np.array([0.0, 1.0, 0.0]), "assumed level camera", 4.0

    g0, source, gsig = solve_gravity(objects, f0, horizon)
    if source == "assumed level camera":
        warnings.append("no horizon reference: assuming the camera was held level (wide error bar). "
                        "Mark the horizon with --horizon for a tighter result.")
    noise.gravity = gsig
    nominal = [measure(o, g0, f0, cx, cy) for o in objects]
    if not any(nominal):
        hint = (" The camera probably wasn't level: mark the horizon with --horizon."
                if source == "assumed level camera" else "")
        raise ValueError("couldn't measure any object: the base and shadow tip must be on the "
                         "ground (below the horizon) and the top above the base." + hint)
    for i, m in enumerate(nominal):
        if m is None:
            warnings.append(f"object {i + 1} skipped (geometry doesn't work out)")
    vals = [m[0] for m in nominal if m]
    if len(vals) >= 2 and max(vals) - min(vals) > 8:
        warnings.append(f"objects disagree by {max(vals) - min(vals):.0f}°: one may be leaning, "
                        "on a slope, or mis-marked")

    el, rel = [], []
    for _ in range(n):
        f = f0 * (1 + rng.normal(0, noise.focal))
        g = _perturb(g0, noise.gravity, rng)
        es, rs = [], []
        for o in objects:
            pts = [(p[0] + rng.normal(0, noise.px), p[1] + rng.normal(0, noise.px)) for p in o]
            m = measure(pts, g, f, cx, cy, axis=_perturb(-g, noise.lean, rng))
            if m:
                es.append(m[0] + rng.normal(0, noise.slope))
                rs.append(m[1])
        if es:
            el.append(float(np.median(es)))
            rel.append(_circ_mean_std(np.array(rs))[0])
    el = np.array(el)
    if len(el) < n * 0.5:
        warnings.append("geometry is fragile: over half the simulated measurements failed")
    med = float(np.median(el))
    sigma = max(0.4, float(1.4826 * np.median(np.abs(el - med))))
    rb, rbs = _circ_mean_std(np.array(rel))
    return Measurement(med, sigma, el, [m[0] if m else None for m in nominal], source,
                       math.degrees(math.asin(np.clip(g0[2], -1, 1))),
                       math.degrees(math.atan2(g0[0], g0[1])), rb, rbs, warnings)


# ================================================================= posterior
def tz_weight(lon, utc_offset_hours):
    """Soft time-zone prior: full weight within 15 deg of the zone's meridian, zero past 30."""
    d = np.abs((np.asarray(lon) - utc_offset_hours * 15 + 180) % 360 - 180)
    return np.clip((30 - d) / 15, 0, 1)


def likelihood(when, lat, lon, m: Measurement, heading=None, heading_sigma=10.0):
    el, az = S.solar_position(when, lat, lon)
    L = np.exp(-0.5 * ((el - m.elevation) / m.sigma) ** 2)
    if heading is not None and m.rel_bearing is not None:
        sun_az = (heading + m.rel_bearing + 180) % 360
        d = (np.asarray(az) - sun_az + 180) % 360 - 180
        sig = math.hypot(heading_sigma, m.bearing_sigma or 0)
        L = L * np.exp(-0.5 * (d / sig) ** 2)
    return np.where(np.asarray(el) > 0, L, 0.0)


_cities = None


def cities():
    """(lat, lon, population, name, country, admin1) arrays from geonamescache (offline)."""
    global _cities
    if _cities is None:
        import geonamescache
        gc = geonamescache.GeonamesCache()
        names = {k: v["name"] for k, v in gc.get_countries().items()}
        rows = [(c["latitude"], c["longitude"], c["population"], c["name"],
                 names.get(c["countrycode"], c["countrycode"]),
                 c.get("admin1code", "") if c["countrycode"] == "US" else "")
                for c in gc.get_cities().values() if c["population"] > 0]
        _cities = (np.array([r[0] for r in rows]), np.array([r[1] for r in rows]),
                   np.array([r[2] for r in rows], float), [r[3] for r in rows],
                   [r[4] for r in rows], [r[5] for r in rows])
    return _cities


def posterior(when, m: Measurement, heading=None, heading_sigma=10.0, use_tz=True) -> dict:
    """Where was this taken? Ring geometry plus (if geonamescache is installed) a
    population-weighted breakdown by country, US state and metro area."""
    off = when.utcoffset().total_seconds() / 3600
    out = {"ring_width_km": 2 * 1.96 * m.sigma * 111.2,
           "ring_radius_km": (90 - m.elevation) * 111.2, "subsolar": S.subsolar_point(when)}
    # area-weighted grid: how much of the planet is still possible
    lats, lons = np.arange(-89.75, 90, 0.5), np.arange(-179.75, 180, 0.5)
    LA, LO = np.meshgrid(lats, lons, indexing="ij")
    w = likelihood(when, LA, LO, m, heading, heading_sigma) * np.cos(np.radians(LA))
    if use_tz:
        w = w * tz_weight(LO, off)
    tot = w.sum()
    if tot <= 0:
        out["impossible"] = True
        return out
    order = np.argsort(w.ravel())[::-1]
    cum = np.cumsum(w.ravel()[order]) / tot
    k = int(np.searchsorted(cum, 0.95)) + 1
    cell_km2 = (0.5 * 111.2) ** 2 * np.cos(np.radians(LA.ravel()[order[:k]]))
    out["area95_km2"] = float(cell_km2.sum())
    out["grid"] = (LA.ravel()[order[:k]], LO.ravel()[order[:k]], w.ravel()[order[:k]] / tot)
    i = order[0]
    out["peak"] = (float(LA.ravel()[i]), float(LO.ravel()[i]))
    try:
        clat, clon, pop, name, country, admin = cities()
    except ImportError:
        out["cities"] = None
        return out
    p = pop * likelihood(when, clat, clon, m, heading, heading_sigma)
    if use_tz:
        p = p * tz_weight(clon, off)
    if p.sum() <= 0:
        out["cities"] = []
        return out
    p = p / p.sum()
    by_country, by_state = {}, {}
    for pi, c, a in zip(p, country, admin):
        by_country[c] = by_country.get(c, 0) + pi
        if a:
            by_state[a] = by_state.get(a, 0) + pi
    out["countries"] = sorted(by_country.items(), key=lambda kv: -kv[1])[:6]
    out["states"] = sorted(by_state.items(), key=lambda kv: -kv[1])[:5]
    # greedy metro clusters: most likely city, plus everything within 150 km of it
    left = p.copy()
    metros = []
    for _ in range(5):
        j = int(np.argmax(left))
        if left[j] <= 0:
            break
        d = np.hypot(clat - clat[j], (clon - clon[j]) * np.cos(np.radians(clat[j]))) * 111.2
        near = d <= 150
        metros.append((name[j], country[j], admin[j], float(left[near].sum()),
                       float(clat[j]), float(clon[j])))
        left[near] = 0
    out["metros"] = sorted(metros, key=lambda t: -t[3])
    return out


def truth_check(when, lat, lon, m: Measurement) -> dict:
    """For photos that still have GPS: how close was the estimate to reality?"""
    el, az = S.solar_position(when, lat, lon)
    z = (m.elevation - el) / m.sigma
    return {"true_elevation": el, "error": m.elevation - el, "z": z,
            "within_68": abs(z) <= 1.0, "within_95": abs(z) <= 1.96,
            "miss_km": abs(m.elevation - el) * 111.2}


# ==================================================================== picker
PICKER_HTML = """<!doctype html><meta charset=utf-8><title>unredact.tools shadow picker</title>
<style>body{margin:0;background:#0d1117;color:#c9d1d9;font:14px system-ui}#bar{padding:10px 14px;
position:sticky;top:0;background:#161b22;z-index:2}#wrap{position:relative;display:inline-block}
img{max-width:100vw;display:block;cursor:crosshair}svg{position:absolute;inset:0;width:100%;height:100%;
pointer-events:none}code{display:block;margin-top:8px;padding:8px;background:#0d1117;border:1px solid #30363d;
border-radius:6px;user-select:all;word-break:break-all}button{margin-left:8px}b{color:#ffa657}</style>
<div id=bar><span id=msg></span><button onclick="mode='horizon';hz=[];say()">mark horizon (optional)</button>
<button onclick="objs=[];cur=[];hz=[];mode='obj';draw();say()">reset</button><code id=cmd></code></div>
<div id=wrap><img id=im src="data:__MIME__;base64,__B64__"><svg id=sv></svg></div>
<script>
let objs=[],cur=[],hz=[],mode='obj';const im=document.getElementById('im'),sv=document.getElementById('sv');
const steps=['the BASE of a vertical object (where it meets the ground)','the TOP of that object','the TIP of its shadow'];
function say(){document.getElementById('msg').innerHTML=mode=='horizon'?'Click <b>two points on the horizon</b> (far apart)':
'Click <b>'+steps[cur.length]+'</b>'+(objs.length?' &nbsp;('+objs.length+' object(s) marked; add more for accuracy)':'');
let c='unredact.tools sun --photo __NAME__';objs.forEach(o=>c+=' --object '+o.flat().join(','));
if(hz.length==2)c+=' --horizon '+hz.flat().join(',');document.getElementById('cmd').textContent=objs.length?c:'';}
function draw(){const k=im.clientWidth/im.naturalWidth;let h='';const L=(a,b,col)=>h+=`<line x1=${a[0]*k} y1=${a[1]*k} x2=${b[0]*k} y2=${b[1]*k} stroke=${col} stroke-width=2 />`;
const D=(p,col)=>h+=`<circle cx=${p[0]*k} cy=${p[1]*k} r=5 fill=${col} stroke=#000 />`;
objs.concat([cur]).forEach(o=>{if(o[1])L(o[0],o[1],'#3fb950');if(o[2]){L(o[0],o[2],'#ffa657');L(o[1],o[2],'#f85149')}o.forEach(p=>D(p,'#fff'))});
if(hz.length==2)L(hz[0],hz[1],'#58a6ff');hz.forEach(p=>D(p,'#58a6ff'));sv.innerHTML=h;}
im.onclick=e=>{const r=im.getBoundingClientRect(),k=im.naturalWidth/im.clientWidth;
const p=[Math.round((e.clientX-r.left)*k),Math.round((e.clientY-r.top)*k)];
if(mode=='horizon'){hz.push(p);if(hz.length==2)mode='obj';}else{cur.push(p);if(cur.length==3){objs.push(cur);cur=[];}}draw();say();};
window.onresize=draw;say();
</script>"""


def write_picker(photo_path, out_path):
    import base64
    import io
    import os

    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(photo_path)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)  # same pixel grid as the original, metadata dropped
    html = (PICKER_HTML.replace("__MIME__", "image/jpeg")
            .replace("__B64__", base64.b64encode(buf.getvalue()).decode())
            .replace("__NAME__", os.path.basename(photo_path)))
    with open(out_path, "w") as f:
        f.write(html)
    return out_path
