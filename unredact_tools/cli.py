"""unredact.tools: see what a file leaks, then scrub it.

  unredact.tools scan photo.jpg report.pdf [--json] [--extract DIR] [--all]
  unredact.tools sanitize photo.jpg [-o out.jpg] [--pdf-mode raster|text]
  unredact.tools tip [--all]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

from . import __version__
from .model import FACT_LABELS, SEVERITIES
from .banner import banner, c, color_enabled
from .scanner import sanitize, scan
from .tips import TIPS

COLORS = {"critical": "\033[1;97;41m", "high": "\033[1;31m", "medium": "\033[33m",
          "low": "\033[36m", "info": "\033[2m"}
BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def _c(s, code, on):
    return f"{code}{s}{RESET}" if on else s


def print_report(r, color=True, show_all=False):
    w = print
    w(_c(f"\n=== {r.path}", BOLD, color))
    w(_c(f"    {r.filetype}  |  {r.size:,} bytes  |  sha256 {r.sha256[:16]}...", DIM, color))
    counts = r.counts()
    summary = "  ".join(_c(f"{counts[s]} {s}", COLORS[s], color) for s in SEVERITIES[:4] if counts[s])
    w("    " + (summary or _c("no leaks found", "\033[32m", color)))

    if r.facts:
        w(_c("\n  WHAT THIS FILE GIVES AWAY", BOLD, color))
        for bucket, label in FACT_LABELS.items():
            vals = r.facts.get(bucket)
            if vals:
                shown = vals[:6]
                more = f" (+{len(vals) - 6} more)" if len(vals) > 6 else ""
                w(f"  {label:<22} " + ("\n" + " " * 25).join(shown) + more)

    findings = [f for f in r.sorted_findings() if show_all or f.severity != "info"]
    if findings:
        w(_c("\n  FINDINGS", BOLD, color))
        for f in findings:
            tag = _c(f"[{f.severity.upper():^8}]", COLORS[f.severity], color)
            w(f"  {tag} {f.title}" + (_c(f"  ({f.source})", DIM, color) if f.source else ""))
            if f.detail:
                for line in f.detail.splitlines()[:6]:
                    w(f"             {line[:160]}")
    if r.artifacts:
        w(_c(f"\n  {len(r.artifacts)} recoverable artifact(s): ", BOLD, color) +
          ", ".join(a.name for a in r.artifacts[:8]) + "  (use --extract DIR)")
    for e in r.errors:
        w(_c(f"  ! {e}", DIM, color))


def cmd_scan(args):
    color = color_enabled(sys.stdout, args.no_color)
    if args.scene == "anthropic":
        print(_c("note: --scene anthropic uploads each image (re-encoded, metadata stripped) to "
                 "the Anthropic API. Use --scene ollama to keep it local.", "\033[33m", color),
              file=sys.stderr)
    reports = []
    for p in args.files:
        if not os.path.isfile(p):
            print(f"skip: {p} is not a file", file=sys.stderr)
            continue
        r = scan(p, visual=not args.fast, ocr=not (args.fast or args.no_ocr))
        if args.scene and r.filetype.startswith("image/"):
            from . import scene
            try:
                data = scene.analyze(open(p, "rb").read(), args.scene, args.scene_model)
                scene.add_findings(r, data, f"scene AI ({data['model']})")
            except scene.SceneError as e:
                r.errors.append(f"scene analysis: {e}")
        reports.append(r)
        if args.extract and r.artifacts:
            d = os.path.join(args.extract, os.path.basename(p) + "_artifacts")
            os.makedirs(d, exist_ok=True)
            for a in r.artifacts:
                with open(os.path.join(d, a.name.replace("/", "_")), "wb") as f:
                    f.write(a.data)
        if not args.json:
            print_report(r, color, args.all)
    if args.json:
        print(json.dumps([r.to_dict() for r in reports], indent=2, default=str))
    worst = min((SEVERITIES.index(f.severity) for r in reports for f in r.findings
                 if f.severity != "info"), default=99)
    return 2 if worst <= 1 else 0  # non-zero exit if high/critical leaks (CI-friendly)


HINTS = {  # remaining finding title prefix -> flag that fixes it
    "Readable in the image": "--redact-text",
    "Boarding pass": "--redact-codes",
    "QR Code": "--redact-codes",
    "face(s) visible": "--blur-faces",
    "License plate": "--redact-plates",
    "Pixelated region": "--redact-region X,Y,W,H",
    "Scene:": "--redact-region X,Y,W,H",
}


def _region(s):
    try:
        x, y, w, h = (int(v) for v in s.split(","))
        return (x, y, w, h)
    except ValueError:
        raise argparse.ArgumentTypeError("expected X,Y,W,H (pixels), e.g. 120,40,300,60")


def _visual_opts(args):
    from .image import VisualOpts
    return VisualOpts(
        blur_faces=args.blur_faces or args.visual,
        redact_codes=args.redact_codes or args.visual,
        redact_text=args.redact_text or args.visual,
        redact_plates=args.redact_plates or args.visual,
        regions=args.redact_region or [],
        desync=args.desync,
        remove_dots=not args.keep_dots,
    )


def cmd_sanitize(args):
    color = color_enabled(sys.stdout, args.no_color)
    if args.output and len(args.files) > 1:
        sys.exit("-o only works with a single input file")
    rc = 0
    opts = _visual_opts(args)
    for p in args.files:
        before = scan(p)
        notes = []
        try:
            out = sanitize(p, args.output, pdf_mode=args.pdf_mode, visual=opts, notes=notes)
        except Exception as e:  # noqa: BLE001
            print(f"x {p}: {e}", file=sys.stderr)
            rc = 1
            continue
        after = scan(out)
        b = sum(1 for f in before.findings if f.severity != "info")
        a = [f for f in after.findings if f.severity not in ("info",)
             and f.category != "structure"]
        print(_c(f"+ {p} -> {out}", BOLD, color))
        for n in notes:
            print(_c(f"  · {n}", DIM, color))
        after = _c(f"{len(a)} after", "\033[1;32m" if not a else "\033[1;31m", color)
        print(f"  leaks: {_c(f'{b} before', COLORS['high'], color)} -> {after}")
        hints = set()
        for f in a:
            tag = _c(f"[{f.severity.upper():^8}]", COLORS[f.severity], color)
            print(f"  remaining {tag} {f.title}: {f.detail[:120]}")
            hints.update(flag for key, flag in HINTS.items() if key in f.title)
        if hints:
            print(_c(f"  hint: re-run with {' '.join(sorted(hints))}", "\033[36m", color))
        if any(f.severity in ("critical", "high") for f in a):
            rc = 2
    return rc


def cmd_camera(args):
    from PIL import Image as _I  # noqa: F401  (ensures pillow plugins load)
    from . import vision
    if not vision.available():
        sys.exit("camera-id needs: pip install 'unredact-tools[vision]'")
    import numpy as np
    color = color_enabled(sys.stdout, args.no_color)
    if args.action == "build":
        imgs = [vision.load(open(f, "rb").read()) for f in args.files]
        K = vision.build_fingerprint(imgs)
        out = args.output or "camera.npz"
        np.savez_compressed(out, K=K)
        print(_c(f"+ fingerprint from {len(imgs)} photo(s) -> {out}", BOLD, color),
              f"({K.shape[1]}x{K.shape[0]})")
        print(_c("  tip: 10-30 photos of bright, flat, unedited scenes (sky, walls) work best",
                 DIM, color))
        return 0
    # match / compare
    if args.action == "match":
        K = np.load(args.files[0])["K"]
        targets = args.files[1:]
    else:  # compare: fingerprint from the first photo, test the rest
        K = vision.build_fingerprint([vision.load(open(args.files[0], "rb").read())])
        targets = args.files[1:]
    if not targets:
        sys.exit("give at least one photo to test")
    hit = False
    for f in targets:
        r = vision.match_fingerprint(K, vision.load(open(f, "rb").read()))
        code = "\033[1;31m" if r["z"] > 4 else "\033[1;32m"
        print(f"  {f}: {_c(r['verdict'], code, color)}  "
              + _c(f"(z={r['z']}, corr={r['correlation']}, {r['compared_px']})", DIM, color))
        hit |= r["z"] > 4
    if hit:
        print(_c("  sanitize --desync breaks this link (resample + crop).", "\033[36m", color))
    return 2 if hit else 0


class Parser(argparse.ArgumentParser):
    """ArgumentParser with an optional banner and colored section headings."""

    def error(self, message):
        message = message.replace("argument : invalid choice:", "unknown command:")
        super().error(message)

    def __init__(self, *a, show_banner=False, **kw):
        kw.setdefault("formatter_class", lambda prog: argparse.RawDescriptionHelpFormatter(
            prog, max_help_position=30, width=88))
        super().__init__(*a, **kw)
        self.show_banner = show_banner

    def print_help(self, file=None):
        file = file or sys.stdout
        on = color_enabled(file)
        if self.show_banner:
            file.write(banner(on) + "\n")
        text = re.sub(r"positional arguments:\n\s*\n\n", "", self.format_help())
        if on:
            text = re.sub(r"(?m)^([A-Za-z][A-Za-z ]+):$", lambda m: c(m.group(0), HEAD, on), text)
            text = re.sub(r"(?m)^usage:", c("usage:", HEAD, on), text)
            def example(m):
                cmd, _, note = m.group(2).partition("  #")
                return (c("  $ ", DIM, on) + c(cmd, "\033[36m", on) +
                        (c("  #" + note, DIM, on) if note else ""))
            text = re.sub(r"(?m)^(  \$ )(.*)$", example, text)
            text = re.sub(r"(?m)^(  )(scan|sanitize|camera-id|sun|tip|web)(\s)", lambda m: m.group(1) +
                          c(m.group(2), BOLD, on) + m.group(3), text)
        file.write(text)


HEAD = "\033[1;38;5;208m"

MAIN_DESC = """\
commands:
  scan       show what metadata and hidden content a file leaks
  sanitize   write a scrubbed copy, then re-scan it to prove it's clean
  camera-id  link photos to the camera that took them (sensor fingerprint)
  sun        geolocate a photo from its timestamp and a shadow
  tip        print a random OPSEC tip (--all for the full list)
  web        run the web app (drag-and-drop scan + sanitize in a browser)

run `unredact.tools <command> --help` for that command's options."""

MAIN_EPILOG = """\
examples:
  $ unredact.tools scan vacation.jpg
  $ unredact.tools scan *.pdf *.docx --extract ./loot
  $ unredact.tools sanitize memo.docx court_filing.pdf
  $ unredact.tools sanitize screenshot.png --visual
  $ unredact.tools camera-id compare known.jpg anonymous.jpg
  $ unredact.tools web                      # http://127.0.0.1:8000

supports:
  images (JPEG PNG WebP TIFF HEIC) · PDF · DOCX XLSX PPTX · audio/video (ffmpeg) · text

docs: https://github.com/NPCmillionaire/unredact.tools"""

SCAN_DESC = """\
Show what a file gives away: GPS, device serials, authors, usernames, failed
redactions, earlier PDF versions, deleted tracked changes, hidden text, comments,
embedded files, and invisible watermarks. Images are also checked pixel by pixel:
readable sensitive text (OCR), QR codes and boarding passes, license plates, faces,
reversible pixelation, and printer tracking dots (decoded when possible). Add --scene
for an AI review of reflections, landmarks and regional clues. Nothing is uploaded
unless you choose --scene anthropic. Files are never modified."""

SCAN_EPILOG = """\
examples:
  $ unredact.tools scan photo.jpg
  $ unredact.tools scan *.pdf *.docx
  $ unredact.tools scan memo.docx --extract ./loot
  $ unredact.tools scan filing.pdf --json | jq '.[0].counts'
  $ unredact.tools scan huge_folder/* --fast       # metadata only, no OCR/vision
  $ unredact.tools scan selfie.jpg --scene          # AI scene review via local Ollama

exit codes:
  0   no high or critical leaks
  2   at least one high or critical leak (use it as a CI gate)"""

SANITIZE_DESC = """\
Rebuild each file from its visible content only, write it next to the original
as <name>.clean.<ext>, then re-scan the copy and report anything that survived.
The original is never modified."""

SANITIZE_EPILOG = """\
pdf modes:
  raster  every page flattened to an image; nothing survives but pixels (default)
  text    keeps selectable text; burns in real redactions, scrubs everything else

examples:
  $ unredact.tools sanitize photo.jpg              # -> photo.clean.jpg
  $ unredact.tools sanitize a.pdf b.docx c.mp4
  $ unredact.tools sanitize report.pdf -o public.pdf --pdf-mode text
  $ unredact.tools sanitize screenshot.png --redact-text --redact-codes
  $ unredact.tools sanitize party.jpg --blur-faces --desync
  $ unredact.tools sanitize driveway.jpg --redact-plates

exit codes:
  0   every output re-scanned clean
  1   a file couldn't be sanitized
  2   a high or critical leak survived"""


CAMERA_DESC = """\
Camera sensor fingerprinting (PRNU). Every sensor has a unique, fixed noise pattern
that survives metadata stripping and JPEG compression. It can link an "anonymous"
photo to other photos from the same phone. Check whether yours can be linked, then
sanitize with --desync to break the link."""

CAMERA_EPILOG = """\
actions:
  build     build a fingerprint from reference photos you took with one camera
  match     test photos against a saved fingerprint
  compare   quick check: use the first photo as the reference, test the rest

examples:
  $ unredact.tools camera-id build my_phone/*.jpg -o myphone.npz
  $ unredact.tools camera-id match myphone.npz anonymous_post.jpg
  $ unredact.tools camera-id compare known.jpg suspect1.jpg suspect2.jpg
  $ unredact.tools sanitize anonymous_post.jpg --desync   # then match again

notes:
  photos must be full-resolution originals in the same orientation; resized or
  cropped images won't match. more reference photos = a stronger fingerprint."""


SUN_DESC = """\
Shadow geolocation. A photo's timestamp plus the angle of a shadow in it narrows
down where on Earth it was taken, with no GPS needed. Use it to see how much a
photo's time and shadows give away, or to check a location someone claims."""

SUN_EPILOG = """\
measuring the shadow:
  pick something vertical (a pole, a person, a door frame) and its shadow on flat ground.
  --shadow-ratio HEIGHT:LENGTH   e.g. 2:3.5 for a 2 m pole with a 3.5 m shadow
  --shadow-bearing DEG           compass direction the shadow points (0=N, 90=E)

examples:
  $ unredact.tools sun --photo beach.jpg --shadow-ratio 1:1.8
  $ unredact.tools sun --time 2026-11-10T14:30-05:00 --shadow-ratio 2:3.5 --shadow-bearing 40
  $ unredact.tools sun --time 2026-11-10T14:30-05:00 --where 41.10,-80.65   # what the sun did there
  $ unredact.tools sun --photo x.jpg --elevation 31 --geojson places.geojson"""


def _parse_time(text):
    from datetime import datetime as _dt
    t = text.strip().replace(" ", "T", 1)
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    d = _dt.fromisoformat(t)
    if d.tzinfo is None:
        raise SystemExit("time needs a UTC offset, e.g. 2026-11-10T14:30-05:00 or ...Z")
    return d


def _photo_time(path):
    from datetime import datetime as _dt, timedelta, timezone as _tz
    from PIL import ExifTags, Image as _I
    ex = _I.open(path).getexif()
    sub = ex.get_ifd(ExifTags.IFD.Exif)
    raw = sub.get(0x9003) or ex.get(0x0132)
    off = sub.get(0x9011) or sub.get(0x9010)
    if not raw:
        raise SystemExit(f"{path}: no capture time in EXIF; pass --time instead")
    d = _dt.strptime(str(raw).strip("\x00"), "%Y:%m:%d %H:%M:%S")
    if not off:
        raise SystemExit(f"{path}: capture time has no timezone ({raw}); pass --time "
                         "with an offset instead")
    sign = -1 if str(off).startswith("-") else 1
    hh, mm = str(off).strip("+-\x00").split(":")[:2]
    return d.replace(tzinfo=_tz(sign * timedelta(hours=int(hh), minutes=int(mm))))


def cmd_sun(args):
    import json as _json
    import numpy as np
    from . import sun as S
    color = color_enabled(sys.stdout, args.no_color)
    when = _photo_time(args.photo) if args.photo else _parse_time(args.time) if args.time else None
    if when is None:
        sys.exit("give --time or --photo")
    from datetime import timezone as _tz
    print(_c(f"  time: {when.isoformat()}  (UTC {when.astimezone(_tz.utc):%Y-%m-%d %H:%M})", DIM, color))
    if args.where:
        lat, lon = (float(v) for v in args.where.split(","))
        el, az = S.solar_position(when, lat, lon)
        if el <= 0:
            print(f"  the sun was below the horizon at {lat},{lon} ({el:.1f}°)")
            return 0
        import math as _m
        ratio = 1 / _m.tan(_m.radians(el))
        print(f"  sun at {lat},{lon}: elevation {el:.1f}°, azimuth {az:.0f}° ({S.compass(az)})")
        print(f"  shadows point {((az + 180) % 360):.0f}° ({S.compass(az + 180)}) and are "
              f"{ratio:.2f}x the object's height")
        return 0
    if args.shadow_ratio:
        try:
            h, l = (float(v) for v in args.shadow_ratio.replace("/", ":").split(":"))
        except ValueError:
            sys.exit("--shadow-ratio expects HEIGHT:LENGTH, e.g. 2:3.5")
        elev = S.elevation_from_shadow(h, l)
    elif args.elevation is not None:
        elev = args.elevation
    else:
        sys.exit("give --shadow-ratio HEIGHT:LENGTH, --elevation DEG, or --where LAT,LON")
    print(f"  sun elevation from the shadow: {_c(f'{elev:.1f}°', BOLD, color)}")
    pts, err = S.locate(when, elev, args.shadow_bearing, elev_tol=args.tolerance)
    if not len(pts):
        print(_c("  no place on Earth matches - check the time zone and the measurement", "\033[33m", color))
        return 1
    ss = S.subsolar_point(when)
    radius_km = (90 - elev) * 111.2
    if args.shadow_bearing is None:
        print(f"  possible locations: a circle {radius_km:,.0f} km from the point under the sun "
              f"({ss[0]:.1f}, {ss[1]:.1f}), about ±{args.tolerance * 111:.0f} km wide")
        off = when.utcoffset().total_seconds() / 3600
        keep = S.tz_band(pts, off)
        if keep.any():
            band = pts[keep]
            band = band[np.argsort(band[:, 0])]
            cuts = np.nonzero(np.diff(band[:, 0]) > 2)[0] + 1
            print(f"  inside the photo's UTC{off:+g} time-zone band, it lies on:")
            for seg in np.split(band, cuts)[:4]:
                print(f"    - latitude {seg[:, 0].min():.1f} to {seg[:, 0].max():.1f}, "
                      f"longitude {seg[:, 1].min():.1f} to {seg[:, 1].max():.1f}")
        print(_c("  add --shadow-bearing to narrow this to a single area", "\033[36m", color))
    else:
        best = pts[0]
        dist = np.hypot(pts[:, 0] - best[0], (pts[:, 1] - best[1]) * np.cos(np.radians(best[0])))
        near = pts[(dist < 10) & (err <= err[0] + 1.0)]
        dlat = float(np.ptp(near[:, 0])) * 111
        dlon = float(np.ptp(near[:, 1])) * 111 * np.cos(np.radians(best[0]))
        print(f"  best match: {_c(f'{best[0]:.2f}, {best[1]:.2f}', BOLD, color)}  "
              f"(candidate area ~{max(dlat, 55):,.0f} x {max(dlon, 55):,.0f} km)")
        print(f"  map: https://www.google.com/maps?q={best[0]:.3f},{best[1]:.3f}")
        others = [p for p in pts[1:] if np.hypot(p[0] - best[0], p[1] - best[1]) > 15]
        if others:
            o = others[0]
            print(f"  another region also fits: {o[0]:.1f}, {o[1]:.1f}")
    if args.geojson:
        with open(args.geojson, "w") as f:
            _json.dump(S.to_geojson(pts, err), f)
        print(_c(f"  wrote {len(pts)} candidate points -> {args.geojson} (open in geojson.io)", DIM, color))
    return 0


def cmd_tip(args):
    on = color_enabled(sys.stdout, args.no_color)
    if args.all:
        for i, t in enumerate(TIPS, 1):
            print(c(f"{i:>2}.", DIM, on), t)
        return 0
    box = banner(on).split("\n")
    start = next(i for i, ln in enumerate(box) if "OPSEC TIP" in ln)
    print("\n".join(box[start:]))
    return 0


def cmd_web(args):
    try:
        from .web.app import serve
    except ImportError:
        sys.exit('the web app needs: pip install "unredact-tools[web]"')
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("note: listening beyond localhost; put it behind HTTPS before exposing it",
              file=sys.stderr)
    print(f"unredact.tools web on http://{args.host}:{args.port}  (ctrl-c to stop)")
    serve(args.host, args.port)
    return 0


def main(argv=None):
    try:
        _main(argv)
    except BrokenPipeError:  # e.g. `unredact.tools scan x | head`
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(130)


def _main(argv=None):
    ap = Parser(prog="unredact.tools", show_banner=True, description=MAIN_DESC,
                epilog=MAIN_EPILOG, usage="%(prog)s <command> [options] files...")
    ap.add_argument("-v", "--version", action="version", version=f"unredact.tools {__version__}")
    sub = ap.add_subparsers(dest="cmd", metavar="", parser_class=Parser, prog="unredact.tools")

    s = sub.add_parser("scan", description=SCAN_DESC, epilog=SCAN_EPILOG)
    s.add_argument("files", nargs="+", metavar="FILE", help="file(s) to inspect")
    s.add_argument("--json", action="store_true", help="machine-readable JSON output")
    s.add_argument("--extract", metavar="DIR",
                   help="save recovered artifacts: thumbnails, old PDF revisions, attachments, "
                        "motion-photo video")
    s.add_argument("--all", action="store_true", help="include info-level findings")
    s.add_argument("--fast", action="store_true",
                   help="metadata only: skip visual checks (OCR, codes, faces, dots)")
    s.add_argument("--no-ocr", action="store_true", help="skip OCR of text inside images")
    s.add_argument("--scene", nargs="?", const="ollama", choices=["ollama", "anthropic"],
                   help="AI scene review: reflections, landmarks, regional clues, shadows "
                        "(default: local Ollama; 'anthropic' uploads the image)")
    s.add_argument("--scene-model", metavar="NAME",
                   help="model override (default qwen3-vl:8b / claude-sonnet-5-5)")
    s.add_argument("--no-color", action="store_true", help="disable colors (NO_COLOR also works)")
    s.set_defaults(fn=cmd_scan)

    z = sub.add_parser("sanitize", description=SANITIZE_DESC, epilog=SANITIZE_EPILOG)
    z.add_argument("files", nargs="+", metavar="FILE", help="file(s) to scrub")
    z.add_argument("-o", "--output", metavar="PATH", help="output path (single input only)")
    z.add_argument("--pdf-mode", choices=["raster", "text"], default="raster",
                   help="how to rebuild PDFs (default: raster, see below)")
    v = z.add_argument_group("visual redaction (images and raster PDFs)")
    v.add_argument("--blur-faces", action="store_true", help="blur every detected face")
    v.add_argument("--redact-codes", action="store_true",
                   help="black out QR codes and barcodes (boarding passes!)")
    v.add_argument("--redact-text", action="store_true",
                   help="black out sensitive text found by OCR (emails, phones, SSNs, cards...)")
    v.add_argument("--redact-plates", action="store_true", help="black out license plates")
    v.add_argument("--redact-region", action="append", type=_region, metavar="X,Y,W,H",
                   help="black out a rectangle (repeatable)")
    v.add_argument("--visual", action="store_true",
                   help="all of the above detectors: faces + codes + text + plates")
    v.add_argument("--desync", action="store_true",
                   help="break the camera sensor fingerprint (PRNU) with a tiny resample")
    v.add_argument("--keep-dots", action="store_true",
                   help="don't remove printer tracking dots (removed by default)")
    z.add_argument("--no-color", action="store_true", help="disable colors (NO_COLOR also works)")
    z.set_defaults(fn=cmd_sanitize)

    k = sub.add_parser("camera-id", description=CAMERA_DESC, epilog=CAMERA_EPILOG)
    k.add_argument("action", choices=["build", "match", "compare"])
    k.add_argument("files", nargs="+", metavar="FILE")
    k.add_argument("-o", "--output", metavar="PATH", help="fingerprint file for build")
    k.add_argument("--no-color", action="store_true", help="disable colors")
    k.set_defaults(fn=cmd_camera)

    g = sub.add_parser("sun", description=SUN_DESC, epilog=SUN_EPILOG)
    src = g.add_mutually_exclusive_group()
    src.add_argument("--photo", metavar="FILE", help="read the capture time from a photo's EXIF")
    src.add_argument("--time", metavar="ISO", help="capture time with UTC offset")
    g.add_argument("--shadow-ratio", metavar="H:L", help="object height : shadow length")
    g.add_argument("--elevation", type=float, metavar="DEG", help="sun elevation, if known")
    g.add_argument("--shadow-bearing", type=float, metavar="DEG",
                   help="compass direction the shadow points")
    g.add_argument("--where", metavar="LAT,LON", help="instead: show the sun at this place/time")
    g.add_argument("--tolerance", type=float, default=1.5, metavar="DEG",
                   help="elevation measurement tolerance (default 1.5)")
    g.add_argument("--geojson", metavar="PATH", help="write all candidate points as GeoJSON")
    g.add_argument("--no-color", action="store_true", help="disable colors")
    g.set_defaults(fn=cmd_sun)

    t = sub.add_parser("tip", description="Print a random OPSEC tip.")
    t.add_argument("--all", action="store_true", help="list every tip")
    t.add_argument("--no-color", action="store_true", help="disable colors")
    t.set_defaults(fn=cmd_tip)

    w = sub.add_parser("web", description="Run the web app: drag files into a browser to scan "
                       "and sanitize them. Uploads go to a temp dir that is deleted per request.")
    w.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    w.add_argument("--port", type=int, default=8000, help="port (default 8000)")
    w.set_defaults(fn=cmd_web)

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        sys.exit(0)
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
