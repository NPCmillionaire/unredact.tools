"""Printer tracking dots (Machine Identification Code): extraction + decoding.

Decodes the Xerox DocuColor pattern documented by the EFF (2005):
https://w2.eff.org/Privacy/printers/docucolor/

  * a 15 x 8 grid of yellow dots, repeated across the page
  * top row = column parity, column 1 = row parity, all odd parity
    (the top row itself is not covered by row parity)
  * rows 2-8 are bits 64, 32, 16, 8, 4, 2, 1 (top to bottom)
  * columns: 2 minute, 5 hour, 6 day, 7 month, 8 year (no century),
    10 separator (usually all ones), 11-14 serial number, two decimal digits
    per column, read 14-13-12-11; 15 unknown, constant per printer; 3, 4, 9 unused

Other manufacturers use different (mostly undecoded) layouts; for those we can
still detect, extract and compare patterns, just not interpret them.
"""
from __future__ import annotations

from collections import Counter

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = np = None

ROWS, COLS = 8, 15
WEIGHTS = [64, 32, 16, 8, 4, 2, 1]


# ===================================================================== codec
def encode_docucolor(minute, hour, day, month, year, serial: str, col15=0, sep=127):
    """Build a 8x15 bool matrix (row 0 = parity row, col 0 = parity column)."""
    m = np.zeros((ROWS, COLS), bool)
    serial = serial.zfill(8)
    values = {2: minute, 5: hour, 6: day, 7: month, 8: year % 100, 10: sep, 15: col15,
              14: int(serial[0:2]), 13: int(serial[2:4]), 12: int(serial[4:6]),
              11: int(serial[6:8])}
    for col, val in values.items():
        for r, w in enumerate(WEIGHTS, start=1):
            m[r, col - 1] = bool(val & w)
    for r in range(1, ROWS):  # row parity (column 1): odd number of dots per row
        m[r, 0] = m[r, 1:].sum() % 2 == 0
    for c in range(COLS):  # column parity (top row): odd number of dots per column
        m[0, c] = m[1:, c].sum() % 2 == 0
    return m


def check_parity(m):
    bad_rows = [r for r in range(1, ROWS) if m[r].sum() % 2 == 0]
    bad_cols = [c for c in range(COLS) if m[:, c].sum() % 2 == 0]
    return bad_rows, bad_cols


def correct(m):
    """Fix a single flipped dot (or a single error in the parity row). Returns (matrix, note)."""
    bad_rows, bad_cols = check_parity(m)
    if not bad_rows and not bad_cols:
        return m, "parity OK"
    m = m.copy()
    if len(bad_cols) == 1 and not bad_rows:
        m[0, bad_cols[0]] ^= True
        return m, "corrected 1 dot in the parity row"
    if len(bad_cols) == 1 and len(bad_rows) == 1:
        m[bad_rows[0], bad_cols[0]] ^= True
        return m, f"corrected 1 dot (row {bad_rows[0] + 1}, column {bad_cols[0] + 1})"
    return m, f"uncorrectable: {len(bad_rows)} bad rows, {len(bad_cols)} bad columns"


def column_value(m, col):  # col is 1-based like the EFF guide
    return sum(w for r, w in enumerate(WEIGHTS, start=1) if m[r, col - 1])


def decode_docucolor(m) -> dict:
    m, note = correct(m)
    v = {c: column_value(m, c) for c in range(1, COLS + 1)}
    serial = "".join(f"{v[c]:02d}" for c in (14, 13, 12, 11))
    year = 2000 + v[8] if v[8] < 100 else None
    ok = (note.startswith(("parity OK", "corrected")) and 0 <= v[2] < 60 and v[5] < 24
          and 1 <= v[6] <= 31 and 1 <= v[7] <= 12 and all(v[c] < 100 for c in (11, 12, 13, 14)))
    return {
        "valid": ok,
        "parity": note,
        "serial": serial.lstrip("0") or "0",
        "serial_raw": serial,
        "printed": f"{year}-{v[7]:02d}-{v[6]:02d} {v[5]:02d}:{v[2]:02d}" if year else None,
        "separator_ok": v[10] == 127,
        "column15": v[15],
    }


# ================================================================ extraction
def dot_centroids(img, mask_fn):
    mask = mask_fn(img)
    n, _, stats, cent = cv2.connectedComponentsWithStats(mask, connectivity=8)
    area = stats[1:, cv2.CC_STAT_AREA]
    return cent[1:][(area >= 1) & (area <= 40)]


def _clusters(pts, link):
    """Group dots into separate matrix instances (single-linkage on a grid hash)."""
    parent = list(range(len(pts)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    cell = {}
    for i, (x, y) in enumerate(pts):
        cell.setdefault((int(x // link), int(y // link)), []).append(i)
    for (cx, cy), idx in cell.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in cell.get((cx + dx, cy + dy), []):
                    for i in idx:
                        if i < j and (pts[i][0] - pts[j][0]) ** 2 + (pts[i][1] - pts[j][1]) ** 2 <= link * link:
                            parent[find(i)] = find(j)
    groups = {}
    for i in range(len(pts)):
        groups.setdefault(find(i), []).append(i)
    return [pts[g] for g in groups.values()]


def _snap(cluster, spacing):
    """Dot centroids -> set of (row, col) lattice coordinates (tolerates slight skew)."""
    # estimate local axes from the cluster itself so small scan rotations don't matter
    c = cluster - cluster.mean(axis=0)
    angle = 0.0
    if len(c) >= 3:
        # dominant direction among near-neighbour vectors
        d = c[:, None, :] - c[None, :, :]
        dist = np.hypot(d[..., 0], d[..., 1])
        near = (dist > spacing * 0.75) & (dist < spacing * 1.25)  # axis neighbours, not diagonals
        vecs = d[near]
        if len(vecs):
            ang = np.arctan2(vecs[:, 1], vecs[:, 0]) % (np.pi / 2)
            angle = float(np.median(np.where(ang > np.pi / 4, ang - np.pi / 2, ang)))
    rot = np.array([[np.cos(-angle), -np.sin(-angle)], [np.sin(-angle), np.cos(-angle)]])
    p = cluster @ rot.T
    p -= p.min(axis=0)
    return {(int(round(y / spacing)), int(round(x / spacing))) for x, y in p}


def _candidates(cells):
    """All 8x15 placements (and the 4 rotations/mirrors a scan can introduce) of a cell set."""
    if not cells:
        return
    rmax = max(r for r, _ in cells)
    cmax = max(c for _, c in cells)
    grid = np.zeros((rmax + 1, cmax + 1), bool)
    for r, c in cells:
        grid[r, c] = True
    variants = [grid, grid[::-1, ::-1], grid.T[::-1], grid.T[:, ::-1]]
    variants += [v[:, ::-1] for v in variants] + [v[::-1] for v in variants]
    for g in variants:
        H, W = g.shape
        if H > ROWS or W > COLS:
            continue
        # the parity row/column may be empty, so try every placement that fits
        for oy in range(ROWS - H + 1):
            for ox in range(COLS - W + 1):
                m = np.zeros((ROWS, COLS), bool)
                m[oy:oy + H, ox:ox + W] = g
                yield m


def decode_page(img, mask_fn) -> dict | None:
    """Find DocuColor matrices on a page and decode them by majority vote."""
    pts = dot_centroids(img, mask_fn)
    if len(pts) < 10:
        return None
    sample = pts if len(pts) <= 3000 else pts[np.random.default_rng(0).choice(len(pts), 3000, False)]
    d = np.sqrt(((sample[:, None] - sample[None]) ** 2).sum(-1))
    np.fill_diagonal(d, np.inf)
    spacing = float(np.median(d.min(axis=1)))
    results, raw = [], []
    for cl in _clusters(pts, spacing * 3.6):  # unused columns leave gaps up to 3 cells
        if not 8 <= len(cl) <= ROWS * COLS:
            continue
        cells = _snap(cl, spacing)
        best = None
        for m in _candidates(cells):
            dec = decode_docucolor(m)
            if dec["valid"] and dec["parity"] == "parity OK" and dec["separator_ok"]:
                best = dec
                break
            if dec["valid"] and best is None:
                best = dec
        if best:
            results.append(best)
        raw.append(len(cl))
    if not results:
        return {"decoded": False, "matrices": len(raw), "spacing_px": round(spacing, 1)}
    votes = Counter((r["serial"], r["printed"]) for r in results)
    (serial, printed), n = votes.most_common(1)[0]
    winner = next(r for r in results if (r["serial"], r["printed"]) == (serial, printed))
    return {"decoded": True, "serial": serial, "printed": printed, "agree": n,
            "matrices": len(raw), "parity": winner["parity"], "column15": winner["column15"],
            "spacing_px": round(spacing, 1)}
