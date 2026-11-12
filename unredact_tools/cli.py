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
            text = re.sub(r"(?m)^(  )(scan|sanitize|camera-id|sun|tip)(\s)", lambda m: m.group(1) +
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

run `unredact.tools <command> --help` for that command's options."""

MAIN_EPILOG = """\
examples:
  $ unredact.tools scan vacation.jpg
  $ unredact.tools scan *.pdf *.docx --extract ./loot
  $ unredact.tools sanitize memo.docx court_filing.pdf
  $ unredact.tools sanitize screenshot.png --visual
  $ unredact.tools camera-id compare known.jpg anonymous.jpg

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

measuring automatically (best accuracy):
  $ unredact.tools sun --picker street.jpg        # click the points in your browser
  $ unredact.tools sun --photo street.jpg --object 812,1490,820,1105,1040,1522
  $ unredact.tools sun --photo street.jpg --auto  # a vision model finds the points
  results come with odds: where the photo was probably taken, by country and city.

two photos of the same place, hours apart, narrow a band down to an area:
  $ unredact.tools sun --photo morning.jpg --object ... --save am.json
  $ unredact.tools sun --photo afternoon.jpg --object ... --save pm.json
  $ unredact.tools sun --combine am.json pm.json

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


def _photo_meta(path):
    """(width, height, f35, heading, (lat, lon)) with EXIF rotation applied to the size."""
    from PIL import ExifTags, Image as _I, ImageOps
    img = _I.open(path)
    ex = img.getexif()
    sub = ex.get_ifd(ExifTags.IFD.Exif)
    gps = ex.get_ifd(ExifTags.IFD.GPSInfo)
    w, h = ImageOps.exif_transpose(img).size
    f35 = sub.get(0xA405)
    heading = float(gps[17]) if 17 in gps else None
    loc = None
    try:
        if 2 in gps and 4 in gps:
            dms = lambda v: float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600  # noqa: E731
            lat = dms(gps[2]) * (-1 if str(gps.get(1, "N")).startswith("S") else 1)
            lon = dms(gps[4]) * (-1 if str(gps.get(3, "E")).startswith("W") else 1)
            loc = (lat, lon)
    except Exception:  # noqa: BLE001
        pass
    return w, h, (float(f35) if f35 else None), heading, loc


def _pts(text, n, what):
    try:
        v = [float(x) for x in text.split(",")]
        assert len(v) == 2 * n
    except (ValueError, AssertionError):
        raise SystemExit(f"{what} expects {2 * n} comma-separated pixel values")
    return tuple((v[i], v[i + 1]) for i in range(0, 2 * n, 2))


def _bar(p, width=20):
    return "█" * round(p * width) + "░" * (width - round(p * width))


def _print_where(post, when, heading, color, n_obs=1):
    """Probability read-out shared by single-photo and combined measurements."""
    if post.get("impossible"):
        print(_c("  no place on Earth matches this shadow at this time: check the timestamp, "
                 "time zone and points", "\033[33m", color))
        return 1
    off = when.utcoffset().total_seconds() / 3600
    shape = (f"a band ~{post['ring_width_km']:,.0f} km wide, " if n_obs == 1 else
             f"where {n_obs} shadow rings cross, centred near {post['peak'][0]:.1f}, {post['peak'][1]:.1f}; ")
    print(f"  95% of the probability: {shape}"
          f"{post['area95_km2'] / 1e6:.1f} million km² in total "
          + _c(f"(assumes the UTC{off:+g} time-zone band"
               + (f" and compass heading {heading:.0f}°" if heading is not None else "") + ")", DIM, color))
    if heading is None and n_obs == 1:
        print(_c("  no compass heading, so the shadow gives a ring, not a point "
                 "(add --heading DEG if you know which way the camera faced)", DIM, color))
    if post.get("cities") is None and "countries" not in post:
        print(_c("  install geonamescache for the where-people-live breakdown "
                 "(pip install 'unredact-tools[geo]')", DIM, color))
    elif post.get("countries"):
        print(_c("\n  WHERE (weighted by where people live)", BOLD, color))
        for name, pr in post["countries"][:4]:
            if pr >= 0.01:
                print(f"    {pr:5.0%} {_bar(pr)} {name}")
        if post["states"] and post["countries"][0][0] == "United States":
            print("    US states: " + ", ".join(f"{a} {pr:.0%}" for a, pr in post["states"][:4] if pr >= 0.01))
        print(_c("  most likely areas (city + 150 km)", BOLD, color))
        for name, country, admin, pr, la, lo in post["metros"]:
            if pr >= 0.005:
                where = f"{name}, {admin}" if admin else f"{name}, {country}"
                print(f"    {pr:5.0%} {_bar(pr)} {where}  " + _c(f"({la:.1f}, {lo:.1f})", DIM, color))
        top = post["countries"][0]
        what = "this photo was" if n_obs == 1 else "these photos were"
        print(f"\n  {_c(f'{top[1]:.0%} chance {what} taken in {top[0]}', BOLD, color)}"
              + (f"; {post['metros'][0][3]:.0%} near {post['metros'][0][0]}" if post["metros"] else ""))
    return 0


def _sun_combine(paths, args, color):
    """Several photos from the same place at different times: multiply their evidence."""
    import json as _json
    from datetime import datetime as _dt
    from . import shadow as SH
    if len(paths) < 2:
        sys.exit("--combine needs two or more files written by `sun --save`")
    obs = []
    for pth in paths:
        d = _json.load(open(pth))
        obs.append((_dt.fromisoformat(d["time"]),
                    SH.saved(d["elevation"], d["sigma"], d.get("rel_bearing"), d.get("bearing_sigma")),
                    d.get("heading")))
        print(_c(f"  {pth}: {d['time']}  elevation {d['elevation']:.1f}° ± {d['sigma']:.1f}°", DIM, color))
    span = max(o[0] for o in obs) - min(o[0] for o in obs)
    if span.total_seconds() < 3600:
        print(_c("  ! these were taken less than an hour apart: the rings barely differ, so "
                 "combining adds little. Hours apart (or different days) works best.", "\033[33m", color))
    when, m, heading = obs[0]
    post = SH.posterior(when, m, heading, 10.0, more=obs[1:])
    rc = _print_where(post, when, heading, color, n_obs=len(obs))
    if not rc and not post.get("impossible"):
        print(f"  map: https://www.google.com/maps?q={post['peak'][0]:.2f},{post['peak'][1]:.2f}")
        if args.geojson:
            la, lo, pw = post["grid"]
            feats = [{"type": "Feature", "properties": {"p": round(float(c), 6)},
                      "geometry": {"type": "Point", "coordinates": [round(float(b), 2), round(float(a), 2)]}}
                     for a, b, c in zip(la[:8000], lo[:8000], pw[:8000])]
            with open(args.geojson, "w") as f:
                _json.dump({"type": "FeatureCollection", "features": feats}, f)
    return rc


def _sun_measure(args, when, color):
    """Automatic / point-based shadow measurement with a probability read-out."""
    import json as _json
    import numpy as np
    from . import shadow as SH
    w = h = f35 = heading = truth = None
    image = None
    if args.photo:
        w, h, f35, heading, truth = _photo_meta(args.photo)
    if args.size:
        w, h = (int(v) for v in args.size.lower().split("x"))
    f35 = args.focal_35 or f35
    heading = args.heading if args.heading is not None else heading
    if not w:
        sys.exit("need --photo (or --size WxH) for the image dimensions")
    if not f35:
        print(_c("  no focal length in EXIF: assuming a phone main camera (26 mm equiv). "
                 "Override with --focal-35.", "\033[33m", color))
        f35 = 26.0
    objects = [_pts(o, 3, "--object") for o in (args.object or [])]
    horizon = _pts(args.horizon, 2, "--horizon") if args.horizon else None
    noise = SH.Noise()
    if args.auto:
        from . import scene
        try:
            got = scene.propose_shadow_points(open(args.photo, "rb").read(), args.auto, args.scene_model)
        except scene.SceneError as e:
            sys.exit(f"--auto: {e}")
        if not got["objects"]:
            sys.exit("--auto: the model found no object with a measurable shadow. "
                     "Mark one yourself: unredact.tools sun --picker " + args.photo)
        objects += got["objects"]
        horizon = horizon or got["horizon"]
        noise.px = max(10.0, 0.008 * float(np.hypot(w, h)))  # model-placed points are rough
        print(_c(f"  {got['model']} marked {len(got['objects'])} object(s)"
                 f"{' and a horizon' if got['horizon'] else ''}; treating its points as "
                 f"±{noise.px:.0f}px", DIM, color))
    if args.photo and not horizon:
        try:
            from . import vision
            if vision.available():
                image = vision.load(open(args.photo, "rb").read())
        except Exception:  # noqa: BLE001
            image = None
    try:
        m = SH.estimate(objects, w, h, f35, horizon=horizon, image=image, noise=noise,
                        n=args.samples)
    except ValueError as e:
        sys.exit(str(e))
    for wmsg in m.warnings:
        print(_c(f"  ! {wmsg}", "\033[33m", color))
    per = ", ".join(f"{v:.1f}°" for v in m.per_object if v is not None)
    print(f"  sun elevation: {_c(f'{m.elevation:.1f}° ± {m.sigma:.1f}°', BOLD, color)}  "
          + _c(f"({len(objects)} object(s): {per}; camera tilt from {m.gravity_source}, "
               f"pitch {m.pitch:+.0f}°)", DIM, color))
    post = SH.posterior(when, m, heading, noise.heading)
    if _print_where(post, when, heading, color):
        return 1
    if args.save:
        with open(args.save, "w") as f:
            _json.dump({"time": when.isoformat(), "elevation": m.elevation, "sigma": m.sigma,
                        "rel_bearing": m.rel_bearing, "bearing_sigma": m.bearing_sigma,
                        "heading": heading, "photo": args.photo}, f)
        print(_c(f"  saved this measurement -> {args.save} (combine two or more with "
                 "`sun --combine`)", DIM, color))
    if truth:
        t = SH.truth_check(when, truth[0], truth[1], m)
        ok = "\033[32m" if t["within_95"] else "\033[31m"
        true_el = t["true_elevation"]
        print(_c(f"\n  ground truth (this photo still has GPS): true elevation {true_el:.1f}°, "
                 f"estimate off by {t['error']:+.1f}° ({t['z']:+.1f}σ, ~{t['miss_km']:.0f} km)", ok, color))
        if args.log:
            with open(args.log, "a") as f:
                f.write(_json.dumps({"photo": args.photo, "z": t["z"], "error": t["error"],
                                     "sigma": m.sigma, "source": m.gravity_source,
                                     "auto": bool(args.auto)}) + "\n")
    if args.geojson:
        la, lo, pw = post["grid"]
        feats = [{"type": "Feature", "properties": {"p": round(float(c), 6)},
                  "geometry": {"type": "Point", "coordinates": [round(float(b), 2), round(float(a), 2)]}}
                 for a, b, c in zip(la[:8000], lo[:8000], pw[:8000])]
        with open(args.geojson, "w") as f:
            _json.dump({"type": "FeatureCollection", "features": feats}, f)
        print(_c(f"  wrote the 95% region ({len(feats)} cells) -> {args.geojson}", DIM, color))
    return 0


def _sun_report(path, color):
    import json as _json
    import numpy as np
    rows = [_json.loads(ln) for ln in open(path) if ln.strip()]
    if not rows:
        sys.exit("log is empty")
    z = np.array([r["z"] for r in rows])
    err = np.array([r["error"] for r in rows])
    print(_c(f"  {len(rows)} photo(s) with known location", BOLD, color))
    print(f"  mean error {err.mean():+.2f}° (bias), typical miss {np.abs(err).mean():.2f}° "
          f"(~{np.abs(err).mean() * 111:.0f} km)")
    c68, c95 = (np.abs(z) <= 1).mean(), (np.abs(z) <= 1.96).mean()
    print(f"  truth inside the 68% range: {c68:.0%} (want ~68%)   inside the 95% range: {c95:.0%} (want ~95%)")
    k = float(np.sqrt((z ** 2).mean()))
    if len(rows) < 20:
        print(_c("  too few photos to judge calibration; aim for 20+", DIM, color))
    elif k > 1.25:
        print(_c(f"  the meter is overconfident: error bars should be ~{k:.1f}x wider", "\033[33m", color))
    elif k < 0.8:
        print(_c(f"  the meter is underconfident: error bars could be ~{1 / k:.1f}x tighter", "\033[36m", color))
    else:
        print(_c("  the meter is well calibrated", "\033[32m", color))
    return 0


def cmd_sun(args):
    import json as _json
    import numpy as np
    if args.picker:
        from . import shadow as SH
        out = SH.write_picker(args.picker, args.picker + ".picker.html")
        print(f"  open {out} in a browser, click base / top / shadow tip, and copy the command it gives you")
        return 0
    if args.report:
        return _sun_report(args.report, color_enabled(sys.stdout, args.no_color))
    if args.combine:
        return _sun_combine(args.combine, args, color_enabled(sys.stdout, args.no_color))
    from . import sun as S
    color = color_enabled(sys.stdout, args.no_color)
    when = _parse_time(args.time) if args.time else _photo_time(args.photo) if args.photo else None
    if when is None:
        sys.exit("give --time or --photo")
    from datetime import timezone as _tz
    print(_c(f"  time: {when.isoformat()}  (UTC {when.astimezone(_tz.utc):%Y-%m-%d %H:%M})", DIM, color))
    if args.object or args.auto:
        return _sun_measure(args, when, color)
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
    g.add_argument("--photo", metavar="FILE", help="the photo (capture time, lens, size from EXIF)")
    g.add_argument("--time", metavar="ISO", help="capture time with UTC offset (overrides EXIF)")
    a = g.add_argument_group("measure the shadow in the photo (perspective-corrected, with odds)")
    a.add_argument("--picker", metavar="FILE",
                   help="write a click-to-mark HTML page for this photo, then exit")
    a.add_argument("--object", action="append", metavar="BX,BY,TX,TY,SX,SY",
                   help="pixels of an object's base, top and shadow tip (repeat for more objects)")
    a.add_argument("--horizon", metavar="X1,Y1,X2,Y2", help="two pixels on the horizon")
    a.add_argument("--auto", nargs="?", const="ollama", choices=["ollama", "anthropic"],
                   help="let a vision model find the points (local Ollama by default)")
    a.add_argument("--scene-model", metavar="NAME", help="model override for --auto")
    a.add_argument("--heading", type=float, metavar="DEG",
                   help="compass direction the camera faced (read from EXIF when present)")
    a.add_argument("--focal-35", type=float, metavar="MM", help="35 mm-equivalent focal length")
    a.add_argument("--size", metavar="WxH", help="image size, if not using --photo")
    a.add_argument("--samples", type=int, default=3000, help="Monte Carlo samples (default 3000)")
    a.add_argument("--save", metavar="FILE", help="save this measurement for --combine")
    a.add_argument("--combine", nargs="+", metavar="FILE",
                   help="combine saved measurements of the SAME place at different times: "
                        "their rings intersect, which narrows a band down to an area")
    a.add_argument("--log", metavar="FILE",
                   help="append estimate-vs-GPS-truth results here (for photos that have GPS)")
    a.add_argument("--report", metavar="FILE", help="summarize a --log file: is the meter honest?")
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

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        sys.exit(0)
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
