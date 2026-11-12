"""Visual-content analysis: what the pixels themselves give away.

Layer 2: readable sensitive text (OCR), reversible pixelation
Layer 3: faces, QR codes / barcodes (incl. boarding passes)
Layer 4: printer tracking dots (MIC), camera sensor fingerprint (PRNU)

Needs the [vision] extra: opencv-python-headless, zxing-cpp, numpy.
OCR additionally needs the `tesseract` binary on PATH.
Everything runs locally.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field

from PIL import Image, ImageOps

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")  # silence OpenCV backend warnings
try:
    import cv2
    import numpy as np
    HAVE_CV = True
except ImportError:  # pragma: no cover
    HAVE_CV = False
try:
    import zxingcpp
    HAVE_ZX = True
except ImportError:  # pragma: no cover
    HAVE_ZX = False

HAVE_OCR = bool(shutil.which("tesseract"))
MODEL = os.path.join(os.path.dirname(__file__), "models", "face_detection_yunet_2023mar.onnx")


def available() -> bool:
    return HAVE_CV


# ===================================================================== loading
def load(raw_or_img) -> "np.ndarray":
    """Bytes or PIL image -> BGR uint8 array, with EXIF rotation applied."""
    img = raw_or_img if isinstance(raw_or_img, Image.Image) else Image.open(io.BytesIO(raw_or_img))
    img.seek(0) if hasattr(img, "seek") else None
    img = ImageOps.exif_transpose(img).convert("RGB")
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int
    label: str = ""
    detail: str = ""
    kind: str = ""  # face | code | text | pixelation | dots
    meta: dict = field(default_factory=dict)

    @property
    def xywh(self):
        return f"{self.x},{self.y},{self.w},{self.h}"


# ======================================================================= faces
_face_net = None


def detect_faces(img, min_score=0.85) -> list[Box]:
    global _face_net
    h, w = img.shape[:2]
    scale = min(1.0, 1600 / max(h, w))
    small = cv2.resize(img, (int(w * scale), int(h * scale))) if scale < 1 else img
    if _face_net is None:
        _face_net = cv2.FaceDetectorYN.create(MODEL, "", (small.shape[1], small.shape[0]),
                                             score_threshold=min_score)
    _face_net.setInputSize((small.shape[1], small.shape[0]))
    _, faces = _face_net.detect(small)
    out = []
    for f in faces if faces is not None else []:
        x, y, fw, fh = (int(v / scale) for v in f[:4])
        out.append(Box(max(0, x), max(0, y), fw, fh, "face", f"confidence {f[-1]:.2f}", "face"))
    return out


# ================================================================ codes / BCBP
def parse_bcbp(text: str) -> dict | None:
    """IATA Bar Coded Boarding Pass (Resolution 792), mandatory fields."""
    if not (len(text) >= 58 and text[0] == "M" and text[1].isdigit()):
        return None
    try:
        return {
            "name": text[2:22].strip().replace("/", ", "),
            "booking_ref": text[23:30].strip(),
            "from": text[30:33], "to": text[33:36],
            "airline": text[36:39].strip(), "flight": text[39:44].strip().lstrip("0"),
            "day_of_year": text[44:47], "seat": text[48:52].strip().lstrip("0"),
            "sequence": text[52:57].strip().lstrip("0"),
        }
    except Exception:  # noqa: BLE001
        return None


def detect_codes(img) -> list[Box]:
    if not HAVE_ZX:
        return []
    out = []
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    for r in zxingcpp.read_barcodes(rgb):
        p = r.position
        xs = [p.top_left.x, p.top_right.x, p.bottom_left.x, p.bottom_right.x]
        ys = [p.top_left.y, p.top_right.y, p.bottom_left.y, p.bottom_right.y]
        x0, y0 = max(0, min(xs)), max(0, min(ys))
        fmt = str(r.format).split(".")[-1]
        box = Box(x0, y0, max(xs) - x0 + 1, max(ys) - y0 + 1, fmt, r.text, "code")
        bp = parse_bcbp(r.text)
        if bp:
            box.meta["boarding_pass"] = bp
        out.append(box)
    return out


# ========================================================================= OCR
SENSITIVE = [
    ("critical", "identity", "US Social Security number", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("critical", "identity", "Payment card number", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("critical", "identity", "API key / token",
     re.compile(r"\b(?:AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|"
                r"xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35})\b")),
    ("high", "identity", "Email address", re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z][\w.-]*")),
    ("high", "identity", "Phone number",
     re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)")),
    ("high", "location", "Street address",
     re.compile(r"\b\d{1,6}\s+(?:[NSEW]\.?\s+)?(?:[A-Z][a-z]+\s+){1,3}"
                r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Ct|Court|"
                r"Way|Pl|Place|Ter|Terrace|Pkwy|Parkway|Hwy|Highway|Cir|Circle)\b\.?")),
    ("high", "identity", "Username in file path",
     re.compile(r"(?:[A-Za-z]:\\Users\\|/Users/|/home/)[A-Za-z0-9._-]+")),
    ("medium", "network", "IP address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("medium", "network", "URL", re.compile(r"\bhttps?://[^\s\"'<>]+|\b(?:www\.)[\w-]+\.[a-z]{2,}[^\s]*")),
]


def _luhn(digits: str) -> bool:
    d = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(d) <= 19:
        return False
    s = sum(d[-1::-2]) + sum(sum(divmod(2 * x, 10)) for x in d[-2::-2])
    return s % 10 == 0


@dataclass
class OcrWord:
    text: str
    conf: float
    x: int
    y: int
    w: int
    h: int


def ocr_lines(img, min_conf=55) -> list[list[OcrWord]]:
    """Run tesseract; return words grouped into lines (with boxes)."""
    if not HAVE_OCR:
        return []
    h, w = img.shape[:2]
    scale = 2.0 if max(h, w) < 1200 else 1.0  # small screenshots OCR much better upscaled
    src = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) if scale != 1 else img
    ok, png = cv2.imencode(".png", src)
    try:
        res = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "11", "tsv"],
                             input=png.tobytes(), capture_output=True, timeout=120)
    except (subprocess.TimeoutExpired, OSError):
        return []
    lines: dict[tuple, list[OcrWord]] = {}
    for row in res.stdout.decode("utf-8", "replace").splitlines()[1:]:
        f = row.split("\t")
        if len(f) < 12 or not f[11].strip():
            continue
        conf = float(f[10])
        if conf < min_conf:
            continue
        key = (f[2], f[3], f[4])  # block, paragraph, line
        lines.setdefault(key, []).append(OcrWord(
            f[11], conf, int(int(f[6]) / scale), int(int(f[7]) / scale),
            int(int(f[8]) / scale) + 1, int(int(f[9]) / scale) + 1))
    # psm 11 puts each word in its own "line"; regroup by vertical overlap
    words = sorted((wd for ln in lines.values() for wd in ln), key=lambda a: (a.y, a.x))
    grouped: list[list[OcrWord]] = []
    for wd in words:
        for g in grouped:
            ref = g[-1]
            if abs((wd.y + wd.h / 2) - (ref.y + ref.h / 2)) < max(wd.h, ref.h) * 0.6 \
                    and 0 <= wd.x - (ref.x + ref.w) < ref.h * 3:
                g.append(wd)
                break
        else:
            grouped.append([wd])
    return grouped


def detect_sensitive_text(img) -> list[Box]:
    out = []
    for line in ocr_lines(img):
        text, spans, pos = "", [], 0
        for wd in line:
            if text:
                text += " "
            spans.append((len(text), len(text) + len(wd.text), wd))
            text += wd.text
        for sev, bucket, label, rx in SENSITIVE:
            for m in rx.finditer(text):
                val = m.group(0).rstrip(".,;:)")
                if label == "Payment card number" and not _luhn(val):
                    continue
                if label == "IP address" and not all(0 <= int(p) <= 255 for p in val.split(".")):
                    continue
                if label == "Phone number" and len(re.sub(r"\D", "", val)) < 10:
                    continue
                hit = [wd for a, b, wd in spans if a < m.end() and b > m.start()]
                if not hit:
                    continue
                x0 = min(w.x for w in hit)
                y0 = min(w.y for w in hit)
                x1 = max(w.x + w.w for w in hit)
                y1 = max(w.y + w.h for w in hit)
                out.append(Box(x0, y0, x1 - x0, y1 - y0, label, val, "text",
                               {"severity": sev, "bucket": bucket}))
    # dedupe (an email also matches URL-ish patterns, etc.)
    seen, uniq = set(), []
    for b in out:
        k = (b.label, b.detail)
        if k not in seen:
            seen.add(k)
            uniq.append(b)
    return uniq


# ================================================================ pixelation
def detect_pixelation(img, tiles=(96, 192, 288)) -> list[Box]:
    """Find mosaic-pixelated regions (the reversible kind - see Depix).

    A mosaic is piecewise-constant on a regular grid. For each tile we (1) find a block
    size + phase where nearly all edges sit on lattice lines, then (2) cut the tile into
    those cells and require that the cells carrying content are internally flat and take
    many different tones (averaged detail). Text fails (2) because strokes cross cells;
    solid black redaction boxes and UI tables fail it because they only have 2-3 tones.
    """
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    H, W = g.shape
    dx = np.abs(np.diff(g, axis=1)) > 10
    dy = np.abs(np.diff(g, axis=0)) > 10
    hits = []  # (x, y, tile, block); larger tiles catch larger blocks
    for tile in tiles:
        if tile > min(H, W):
            continue
        hits += _pixel_hits(g, dx, dy, tile)
    if not hits:
        return []
    mask = np.zeros((H, W), np.uint8)
    for x, y, t, _ in hits:
        mask[y:y + t, x:x + t] = 255
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    out = []
    for i in range(1, n):
        x, y, w, h, _ = stats[i]
        blocks = [b for hx, hy, _, b in hits if x <= hx < x + w and y <= hy < y + h]
        out.append(Box(int(x), int(y), int(w), int(h), "pixelated region",
                       f"~{int(np.median(blocks))}px blocks", "pixelation"))
    return out


def _pixel_hits(g, dx, dy, tile):
    H, W = g.shape
    hits = []
    for y in range(0, max(1, H - tile + 1), tile // 2):
        for x in range(0, max(1, W - tile + 1), tile // 2):
            tx = dx[y:y + tile, x:x + tile - 1]
            ty = dy[y:y + tile - 1, x:x + tile]
            if tx.mean() > 0.12 or ty.mean() > 0.12:
                continue
            lx = _lattice(tx.sum(axis=0))
            ly = _lattice(ty.sum(axis=1))
            if not lx or not ly or abs(lx[0] - ly[0]) > 1:
                continue
            b = lx[0]
            if _mosaic_cells(g[y:y + tile, x:x + tile], b, (lx[1] + 1) % b, (ly[1] + 1) % b):
                hits.append((x, y, tile, b))
    return hits


def _mosaic_cells(t, b, px, py) -> bool:
    ny, nx = (t.shape[0] - py) // b, (t.shape[1] - px) // b
    if ny < 3 or nx < 3:
        return False
    cells = t[py:py + ny * b, px:px + nx * b].reshape(ny, b, nx, b)[:, 1:b - 1, :, 1:b - 1]
    m = cells.mean(axis=(1, 3))
    sd = cells.std(axis=(1, 3))
    diff = np.zeros(m.shape, bool)
    diff[:, :-1] |= np.abs(np.diff(m, axis=1)) > 10
    diff[:-1, :] |= np.abs(np.diff(m, axis=0)) > 10
    content = diff
    if content.sum() < 8:
        return False
    flat = (sd[content] < 6).mean()
    tones = len(np.unique((m[content] // 24).astype(int)))
    # strong block-to-block contrast: rules out JPEG 8x8 blocking on smooth gradients/sky
    steps = np.concatenate([np.abs(np.diff(m, axis=1)).ravel(), np.abs(np.diff(m, axis=0)).ravel()])
    steps = steps[steps > 10]
    return flat > 0.85 and tones >= 4 and np.percentile(steps, 90) > 35


_BS = np.arange(48, 5, -1) if HAVE_CV else None  # candidate block sizes, largest first


def _lattice(strength):
    """(b, phase) for the largest block size b such that >=90% of edge energy sits on one
    phase mod b (+-1 px for JPEG ringing) and at least 3 distinct lattice lines carry edges.
    Vectorised over all b at once."""
    total = strength.sum()
    if total < 20:
        return None
    pos = np.nonzero(strength)[0]
    w = strength[pos].astype(np.float64)
    nb = len(_BS)
    hist = np.zeros((nb, 50))
    rows = np.repeat(np.arange(nb), len(pos))
    mods = (pos[None, :] % _BS[:, None]).ravel()
    np.add.at(hist, (rows, mods), np.tile(w, nb))
    cols = np.arange(50)[None, :]
    nxt = (cols + 1) % _BS[:, None]
    prv = (cols - 1) % _BS[:, None]
    r = np.arange(nb)[:, None]
    sm = hist + hist[r, nxt] + hist[r, prv]
    sm[cols >= _BS[:, None]] = -1
    best = sm.argmax(axis=1)
    frac = sm[np.arange(nb), best] / total
    for i in np.nonzero(frac >= 0.9)[0]:  # largest b first
        b, p = int(_BS[i]), int(best[i])
        on = pos[np.minimum((pos - p) % b, (p - pos) % b) <= 1]
        if len(np.unique(np.round((on - p) / b))) >= 3:
            return b, p
    return None


# ============================================================ printer dots (MIC)
def yellow_dot_mask(img) -> "np.ndarray":
    """Faint yellow specks on (near-)white paper: high R and G, noticeably lower B."""
    b, g, r = (img[:, :, i].astype(np.int16) for i in range(3))
    yellow = (r > 170) & (g > 170) & ((r + g) // 2 - b > 22) & (np.abs(r - g) < 40)
    return yellow.astype(np.uint8) * 255


def detect_printer_dots(img) -> dict | None:
    h, w = img.shape[:2]
    if h * w < 400_000:  # tracking dots need scan resolution
        return None
    mask = yellow_dot_mask(img)
    n, _, stats, cent = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n < 2:
        return None
    area = stats[1:, cv2.CC_STAT_AREA]
    small = cent[1:][(area >= 1) & (area <= 40)]
    big = (area > 40).sum()
    if len(small) < 40 or big > len(small):  # yellow artwork, not dots
        return None
    # tracking dots repeat on a grid: nearest-neighbour distances cluster tightly
    pts = small.astype(np.float32)
    if len(pts) > 4000:
        pts = pts[np.random.default_rng(0).choice(len(pts), 4000, replace=False)]
    d = np.sqrt(((pts[:, None, :] - pts[None, :, :]) ** 2).sum(-1))
    np.fill_diagonal(d, np.inf)
    nn = d.min(axis=1)
    med = float(np.median(nn))
    regular = float((np.abs(nn - med) <= max(1.5, med * 0.15)).mean())
    if regular < 0.5:
        return None
    return {"dots": int(len(small)), "spacing_px": round(med, 1), "regularity": round(regular, 2)}


def enhance_dots(img) -> bytes:
    """Make the yellow dots visible (blue-channel inversion, like the EFF/DEDA method)."""
    b = img[:, :, 0]
    inv = cv2.normalize(255 - b, None, 0, 255, cv2.NORM_MINMAX)
    ok, png = cv2.imencode(".png", inv)
    return png.tobytes()


def remove_dots(img) -> "np.ndarray":
    mask = yellow_dot_mask(img)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] <= 40
    dots = keep[labels]
    dots = cv2.dilate(dots.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    out = img.copy()
    out[dots] = 255
    return out


# ======================================================== camera fingerprint (PRNU)
def _gray(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)


def noise_residual(gray) -> "np.ndarray":
    """Sensor-noise residual W = I - denoise(I), with row/column means removed and the
    spectrum flattened (suppresses JPEG grid and scene leakage)."""
    den = cv2.GaussianBlur(gray, (0, 0), 1.2)
    w = gray - den
    w -= w.mean(axis=1, keepdims=True)
    w -= w.mean(axis=0, keepdims=True)
    f = np.fft.fft2(w)
    mag = np.abs(f)
    f /= np.maximum(mag, np.median(mag))  # Wiener-ish whitening in the frequency domain
    return np.real(np.fft.ifft2(f)).astype(np.float32)


def _crop(a, shape):
    h, w = shape
    y, x = (a.shape[0] - h) // 2, (a.shape[1] - w) // 2
    return a[y:y + h, x:x + w]


def build_fingerprint(images) -> "np.ndarray":
    """K = sum(W_i * I_i) / sum(I_i^2) over reference photos from one camera."""
    grays = [_gray(i) for i in images]
    shape = (min(g.shape[0] for g in grays), min(g.shape[1] for g in grays))
    num = np.zeros(shape, np.float64)
    den = np.zeros(shape, np.float64)
    for g in grays:
        g = _crop(g, shape)
        num += noise_residual(g) * g
        den += g * g
    return (num / np.maximum(den, 1)).astype(np.float32)


def _ncc(a, b):
    a = a - a.mean()
    b = b - b.mean()
    return float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-12))


def match_fingerprint(K, img) -> dict:
    """Correlate a photo's residual with fingerprint K. Score is a z-score against
    the correlation of misaligned (shifted) copies, i.e. how far above chance."""
    g = _gray(img)
    if (g.shape[0] > g.shape[1]) != (K.shape[0] > K.shape[1]):  # portrait vs landscape
        g = np.rot90(g)
    shape = (min(g.shape[0], K.shape[0]), min(g.shape[1], K.shape[1]))
    g, k = _crop(g, shape), _crop(K, shape)
    w = noise_residual(g)
    expected = g * k
    c0 = _ncc(w, expected)
    rng = np.random.default_rng(1)
    null = []
    for _ in range(40):
        dy, dx = rng.integers(16, shape[0] - 16), rng.integers(16, shape[1] - 16)
        null.append(_ncc(w, np.roll(expected, (int(dy), int(dx)), axis=(0, 1))))
    z = (c0 - float(np.mean(null))) / (float(np.std(null)) + 1e-12)
    verdict = ("same camera (strong match)" if z > 8 else
               "probably the same camera" if z > 4 else "no match")
    return {"correlation": round(c0, 5), "z": round(z, 1), "verdict": verdict,
            "compared_px": f"{shape[1]}x{shape[0]}"}


def desync(img, rng=None) -> "np.ndarray":
    """Break pixel alignment with the sensor grid (slight rescale + crop + rotation),
    which destroys PRNU correlation while being visually imperceptible."""
    rng = rng or np.random.default_rng()
    h, w = img.shape[:2]
    s = float(rng.uniform(0.975, 0.99))
    ang = float(rng.uniform(-0.6, 0.6))
    M = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1 / s)
    out = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
    cx, cy = int(w * 0.01), int(h * 0.01)
    return out[cy:h - cy, cx:w - cx]


# ================================================================== redaction
def redact_boxes(img, boxes, pad=4) -> "np.ndarray":
    out = img.copy()
    H, W = out.shape[:2]
    for b in boxes:
        x0, y0 = max(0, b.x - pad), max(0, b.y - pad)
        x1, y1 = min(W, b.x + b.w + pad), min(H, b.y + b.h + pad)
        out[y0:y1, x0:x1] = 0  # solid fill: nothing to reverse
    return out


def blur_faces(img, faces, rng=None) -> "np.ndarray":
    """Heavy blur + noise. Plain mosaics are reversible; this is not."""
    rng = rng or np.random.default_rng()
    out = img.copy()
    H, W = out.shape[:2]
    for b in faces:
        pad = int(max(b.w, b.h) * 0.2)
        x0, y0 = max(0, b.x - pad), max(0, b.y - pad)
        x1, y1 = min(W, b.x + b.w + pad), min(H, b.y + b.h + pad)
        roi = out[y0:y1, x0:x1]
        k = max(31, (max(roi.shape[:2]) // 2) | 1)
        roi = cv2.GaussianBlur(roi, (k, k), 0)
        roi = cv2.GaussianBlur(cv2.resize(roi, (8, 8), interpolation=cv2.INTER_AREA),
                               (3, 3), 0)
        roi = cv2.resize(roi, (x1 - x0, y1 - y0), interpolation=cv2.INTER_CUBIC)
        noise = rng.normal(0, 6, roi.shape)
        out[y0:y1, x0:x1] = np.clip(roi + noise, 0, 255).astype(np.uint8)
    return out


def to_pil(img) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
