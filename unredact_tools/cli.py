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
    reports = []
    for p in args.files:
        if not os.path.isfile(p):
            print(f"skip: {p} is not a file", file=sys.stderr)
            continue
        r = scan(p, visual=not args.fast, ocr=not (args.fast or args.no_ocr))
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
    "Pixelated region": "--redact-region X,Y,W,H",
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
            text = re.sub(r"(?m)^(  )(scan|sanitize|camera-id|tip)(\s)", lambda m: m.group(1) +
                          c(m.group(2), BOLD, on) + m.group(3), text)
        file.write(text)


HEAD = "\033[1;38;5;208m"

MAIN_DESC = """\
commands:
  scan       show what metadata and hidden content a file leaks
  sanitize   write a scrubbed copy, then re-scan it to prove it's clean
  camera-id  link photos to the camera that took them (sensor fingerprint)
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
readable sensitive text (OCR), QR codes and boarding passes, faces, reversible
pixelation, and printer tracking dots. Nothing is uploaded or modified."""

SCAN_EPILOG = """\
examples:
  $ unredact.tools scan photo.jpg
  $ unredact.tools scan *.pdf *.docx
  $ unredact.tools scan memo.docx --extract ./loot
  $ unredact.tools scan filing.pdf --json | jq '.[0].counts'
  $ unredact.tools scan huge_folder/* --fast       # metadata only, no OCR/vision

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
    v.add_argument("--redact-region", action="append", type=_region, metavar="X,Y,W,H",
                   help="black out a rectangle (repeatable)")
    v.add_argument("--visual", action="store_true",
                   help="all of the above detectors: faces + codes + text")
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
