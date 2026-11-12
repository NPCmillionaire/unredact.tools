"""unredact-file: see what a file leaks, then scrub it.

  unredact-file scan photo.jpg report.pdf [--json] [--extract DIR] [--all]
  unredact-file sanitize photo.jpg [-o out.jpg] [--pdf-mode raster|text]
  unredact-file tip [--all]
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
        r = scan(p)
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


def cmd_sanitize(args):
    color = color_enabled(sys.stdout, args.no_color)
    if args.output and len(args.files) > 1:
        sys.exit("-o only works with a single input file")
    rc = 0
    for p in args.files:
        before = scan(p)
        try:
            out = sanitize(p, args.output, pdf_mode=args.pdf_mode)
        except Exception as e:  # noqa: BLE001
            print(f"x {p}: {e}", file=sys.stderr)
            rc = 1
            continue
        after = scan(out)
        b = sum(1 for f in before.findings if f.severity != "info")
        a = [f for f in after.findings if f.severity not in ("info",)
             and f.category != "structure"]
        print(_c(f"+ {p} -> {out}", BOLD, color))
        after = _c(f"{len(a)} after", "\033[1;32m" if not a else "\033[1;31m", color)
        print(f"  leaks: {_c(f'{b} before', COLORS['high'], color)} -> {after}")
        for f in a:
            tag = _c(f"[{f.severity.upper():^8}]", COLORS[f.severity], color)
            print(f"  remaining {tag} {f.title}: {f.detail[:120]}")
        if any(f.severity in ("critical", "high") for f in a):
            rc = 2
    return rc


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
            text = re.sub(r"(?m)^(  )(scan|sanitize|tip)(\s)", lambda m: m.group(1) +
                          c(m.group(2), BOLD, on) + m.group(3), text)
        file.write(text)


HEAD = "\033[1;38;5;208m"

MAIN_DESC = """\
commands:
  scan       show what metadata and hidden content a file leaks
  sanitize   write a scrubbed copy, then re-scan it to prove it's clean
  tip        print a random OPSEC tip (--all for the full list)

run `unredact-file <command> --help` for that command's options."""

MAIN_EPILOG = """\
examples:
  $ unredact-file scan vacation.jpg
  $ unredact-file scan *.pdf *.docx --extract ./loot
  $ unredact-file sanitize memo.docx court_filing.pdf
  $ unredact-file sanitize report.pdf --pdf-mode text

supports:
  images (JPEG PNG WebP TIFF HEIC) · PDF · DOCX XLSX PPTX · audio/video (ffmpeg) · text

docs: https://github.com/NPCmillionaire/unredact.tools"""

SCAN_DESC = """\
Show what a file gives away: GPS, device serials, authors, usernames, failed
redactions, earlier PDF versions, deleted tracked changes, hidden text, comments,
embedded files, and invisible watermarks. Nothing is uploaded or modified."""

SCAN_EPILOG = """\
examples:
  $ unredact-file scan photo.jpg
  $ unredact-file scan *.pdf *.docx
  $ unredact-file scan memo.docx --extract ./loot
  $ unredact-file scan filing.pdf --json | jq '.[0].counts'

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
  $ unredact-file sanitize photo.jpg              # -> photo.clean.jpg
  $ unredact-file sanitize a.pdf b.docx c.mp4
  $ unredact-file sanitize report.pdf -o public.pdf --pdf-mode text

exit codes:
  0   every output re-scanned clean
  1   a file couldn't be sanitized
  2   a high or critical leak survived"""


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
    except BrokenPipeError:  # e.g. `unredact-file scan x | head`
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(130)


def _main(argv=None):
    ap = Parser(prog="unredact-file", show_banner=True, description=MAIN_DESC,
                epilog=MAIN_EPILOG, usage="%(prog)s <command> [options] files...")
    ap.add_argument("-v", "--version", action="version", version=f"unredact-file {__version__}")
    sub = ap.add_subparsers(dest="cmd", metavar="", parser_class=Parser, prog="unredact-file")

    s = sub.add_parser("scan", description=SCAN_DESC, epilog=SCAN_EPILOG)
    s.add_argument("files", nargs="+", metavar="FILE", help="file(s) to inspect")
    s.add_argument("--json", action="store_true", help="machine-readable JSON output")
    s.add_argument("--extract", metavar="DIR",
                   help="save recovered artifacts: thumbnails, old PDF revisions, attachments, "
                        "motion-photo video")
    s.add_argument("--all", action="store_true", help="include info-level findings")
    s.add_argument("--no-color", action="store_true", help="disable colors (NO_COLOR also works)")
    s.set_defaults(fn=cmd_scan)

    z = sub.add_parser("sanitize", description=SANITIZE_DESC, epilog=SANITIZE_EPILOG)
    z.add_argument("files", nargs="+", metavar="FILE", help="file(s) to scrub")
    z.add_argument("-o", "--output", metavar="PATH", help="output path (single input only)")
    z.add_argument("--pdf-mode", choices=["raster", "text"], default="raster",
                   help="how to rebuild PDFs (default: raster, see below)")
    z.add_argument("--no-color", action="store_true", help="disable colors (NO_COLOR also works)")
    z.set_defaults(fn=cmd_sanitize)

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
