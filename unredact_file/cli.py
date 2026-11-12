"""unredact-file: see what a file leaks, then scrub it.

  unredact-file scan photo.jpg report.pdf [--json] [--extract DIR] [--all]
  unredact-file sanitize photo.jpg [-o out.jpg] [--pdf-mode raster|text]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .model import FACT_LABELS, SEVERITIES
from .scanner import sanitize, scan

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
    color = sys.stdout.isatty() and not args.no_color
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
    color = sys.stdout.isatty() and not args.no_color
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
        print(f"  leaks: {b} before -> {len(a)} after")
        for f in a:
            tag = _c(f"[{f.severity.upper():^8}]", COLORS[f.severity], color)
            print(f"  remaining {tag} {f.title}: {f.detail[:120]}")
        if any(f.severity in ("critical", "high") for f in a):
            rc = 2
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(prog="unredact-file",
                                 description="See what your files leak - then scrub them.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="show what metadata / hidden info a file leaks")
    s.add_argument("files", nargs="+")
    s.add_argument("--json", action="store_true", help="machine-readable output")
    s.add_argument("--extract", metavar="DIR",
                   help="save recovered artifacts (thumbnails, old revisions, attachments...)")
    s.add_argument("--all", action="store_true", help="include info-level findings")
    s.add_argument("--no-color", action="store_true")
    s.set_defaults(fn=cmd_scan)

    z = sub.add_parser("sanitize", help="write a scrubbed copy (original is never modified)")
    z.add_argument("files", nargs="+")
    z.add_argument("-o", "--output", help="output path (single file only)")
    z.add_argument("--pdf-mode", choices=["raster", "text"], default="raster",
                   help="raster = flatten pages to images, removes everything (default); "
                        "text = keep selectable text, burn in redactions, scrub the rest")
    z.add_argument("--no-color", action="store_true")
    z.set_defaults(fn=cmd_sanitize)

    args = ap.parse_args(argv)
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
