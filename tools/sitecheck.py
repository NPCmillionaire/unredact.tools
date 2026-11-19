#!/usr/bin/env python3
"""sitecheck: test a live unredact.tools deployment end to end.

    python tools/sitecheck.py                          # https://unredact.tools
    python tools/sitecheck.py --url http://127.0.0.1:8000
    python tools/sitecheck.py --tor                    # also check the onion mirror
    python tools/sitecheck.py --send-email             # also send one real contact message

Checks pages, security headers, /api/info (ffmpeg, OCR, upload limit), the
contact form, relay auth, and a scan + sanitize of leaky sample files: each
must leak before and nothing after.

Uploads need the relay token (UNREDACT_RELAY_TOKEN, or --token-file) so the
server leaves them out of the public totals. Without it they're skipped, and
the run checks that the totals didn't move either way.

Standard library only. Sample files come from tests/make_fixtures.py when its
dependencies are installed, otherwise from site/sample/. Exits 1 on any failure.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ONION = "http://unred42id7l3elhrlj2oveau3vg2osq2w3qzguj4ajxrg3vycohoueyd.onion"

# path -> text the page must contain
PAGES = {
    "/": "See what your files",
    "/about": "Who it's for",
    "/contact": 'action="/api/contact"',
    "/donate": "bc1q8hdtgw3f8fan587ey3jqy073d0wv2k07vu98d9",
    "/static/app.js": "showCounts",
    "/static/app.css": "--accent",
    "/static/contact.js": "form-status",
    "/static/donate.js": "clipboard",
}
WALLETS = ("46yuwxTQphAMVk44rFkBkW15Qp6dPEdGD25LsFLyAiiHCnb9TXEovTzU7d9yCM9SA3Y4be53QXidF7gxtqNxH4vcCSGPsDj",
           "bc1q8hdtgw3f8fan587ey3jqy073d0wv2k07vu98d9", "legitbiz.eth",
           "business=legitimatebizlabs%40gmail.com")
HEADERS = {
    "content-security-policy": "default-src 'self'",
    "x-content-type-options": "nosniff",
    "cache-control": "no-store",
    "x-frame-options": "DENY",
}
# sample file -> extra sanitize options it needs to come out clean
SAMPLES = {
    "photo.jpg": {}, "filing.pdf": {}, "memo.docx": {}, "budget.xlsx": {},
    "deck.pptx": {}, "leak.txt": {}, "clip.mp4": {}, "vacation.jpg": {},
    "ocr.png": {"redact_text": "true"},
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


class Check:
    def __init__(self, verbose: bool):
        self.failed: list[str] = []
        self.passed = 0
        self.verbose = verbose

    def __call__(self, name: str, ok: bool, detail: str = "") -> bool:
        if ok:
            self.passed += 1
            print(f"  ok    {name}" + (f"  ({detail})" if detail and self.verbose else ""))
        else:
            self.failed.append(name)
            print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))
        return ok

    def section(self, title: str):
        print(f"\n{title}")


def request(url: str, data: bytes | None = None, headers: dict | None = None,
            timeout: float = 30, method: str | None = None):
    """Returns (status, headers, body bytes). Never follows redirects, never raises
    on HTTP errors; network errors come back as status 0."""
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"User-Agent": "unredact-sitecheck", **(headers or {})})
    try:
        with OPENER.open(req, timeout=timeout) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()
    except (OSError, ValueError) as e:
        return 0, {}, str(e).encode()


def multipart(fields: dict, filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = "----sitecheck" + secrets.token_hex(8)
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                  f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
                 + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def samples(tmp: Path) -> dict[str, Path]:
    """Leaky sample files: generated fixtures when possible, else the shipped one."""
    found: dict[str, Path] = {}
    gen = subprocess.run([sys.executable, str(REPO / "tests" / "make_fixtures.py"), str(tmp)],
                         capture_output=True, text=True)
    if gen.returncode == 0:
        found.update({p.name: p for p in tmp.iterdir() if p.name in SAMPLES})
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (900, 260), "white")
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 40)
        except OSError:
            try:
                font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 40)
            except OSError:
                font = ImageFont.load_default(size=40)
        d.text((30, 40), "Call me at 330-555-0142", fill="black", font=font)
        d.text((30, 130), "Email: jane.doe@example.com", fill="black", font=font)
        img.save(tmp / "ocr.png")
        found["ocr.png"] = tmp / "ocr.png"
    except ImportError:
        pass
    shipped = REPO / "site" / "sample" / "vacation.jpg"
    if shipped.is_file():
        found["vacation.jpg"] = shipped
    return found


def check_pages(base: str, check: Check):
    check.section("pages")
    status, hdrs, body = request(base + "/")
    home = body.decode("utf-8", "replace")
    for name, want in HEADERS.items():
        got = hdrs.get(name, "") if hdrs else ""
        check(f"header {name}", want in got, got)
    for path, want in PAGES.items():
        s, _, b = request(base + path)
        text = b.decode("utf-8", "replace")
        check(f"GET {path}", s == 200 and want in text, f"status {s}")
    s, _, b = request(base + "/donate")
    donate = b.decode("utf-8", "replace")
    for w in WALLETS:
        check(f"donate shows {w[:16]}…", w in donate)
    links = sorted({h for h in re.findall(r'href="(/[a-z0-9-]+)"', home)} - set(PAGES))
    for path in links:
        s, _, _ = request(base + path)
        check(f"linked page {path}", s == 200, f"status {s}")
    s, _, _ = request(base + "/no-such-page-" + secrets.token_hex(3))
    check("unknown page is 404", s == 404, f"status {s}")


def check_info(base: str, check: Check, min_mb: int) -> dict | None:
    check.section("api")
    s, _, b = request(base + "/api/info")
    try:
        info = json.loads(b)
    except ValueError:
        check("/api/info is JSON", False, f"status {s}")
        return None
    check("/api/info", s == 200, info.get("version", ""))
    check("ffmpeg available (video and audio)", info.get("ffmpeg") is True)
    check("OCR available", info.get("ocr") is True)
    check(f"upload limit is at least {min_mb} MB", info.get("max_mb", 0) >= min_mb,
          f"{info.get('max_mb')} MB")
    check("totals reported", isinstance(info.get("totals"), dict), str(info.get("totals")))
    s, _, _ = request(base + "/api/totals/checked", data=b"", method="POST")
    check("totals can't be bumped without the token", s == 403, f"status {s}")
    s, _, b = request(base + "/api/tip")
    check("/api/tip", s == 200 and b"tip" in b)
    return info


def check_contact(base: str, check: Check, send_email: bool):
    check.section("contact form")
    form = "application/x-www-form-urlencoded"

    def post(**fields):
        s, h, _ = request(base + "/api/contact", data=urllib.parse.urlencode(fields).encode(),
                          headers={"Content-Type": form})
        return s, (h.get("location", "") if h else "")

    s, loc = post(message="   ")
    check("empty message is rejected", s == 303 and "error=invalid" in loc, loc)
    s, loc = post(message="sitecheck honeypot", website="bot")
    check("honeypot is accepted silently (nothing sent)", s == 303 and loc.endswith("?sent#form"), loc)
    if send_email:
        s, loc = post(name="sitecheck", topic="other",
                      message=f"sitecheck test message, {time.strftime('%Y-%m-%d %H:%M')}. "
                              "Safe to delete.")
        check("real message sent (check the inbox)", s == 303 and "?sent" in loc, loc)


def check_uploads(base: str, check: Check, token: str, files: dict[str, Path]):
    check.section(f"scan + sanitize ({len(files)} sample files)")
    auth = {"Authorization": f"Bearer {token}"}
    for name, path in sorted(files.items()):
        content = path.read_bytes()
        body, ctype = multipart({"fast": "false"}, name, content)
        t = time.time()
        s, _, b = request(base + "/api/scan", data=body, headers={**auth, "Content-Type": ctype},
                          timeout=180)
        try:
            r = json.loads(b)
            leaks = sum(r["counts"].get(k, 0) for k in ("critical", "high", "medium", "low"))
        except (ValueError, KeyError, AttributeError):
            check(f"scan {name}", False, f"status {s}: {b[:120]!r}")
            continue
        errs = r.get("errors") or []
        check(f"scan {name} finds leaks", s == 200 and leaks > 0 and not errs,
              f"{leaks} leaks, {time.time() - t:.1f}s" + (f", errors: {errs}" if errs else ""))
        body, ctype = multipart(SAMPLES.get(name, {}), name, content)
        t = time.time()
        s, _, b = request(base + "/api/sanitize", data=body,
                          headers={**auth, "Content-Type": ctype}, timeout=300)
        try:
            r = json.loads(b)
        except ValueError:
            check(f"sanitize {name}", False, f"status {s}: {b[:120]!r}")
            continue
        left = [f.get("title") for f in r.get("remaining", [])]
        check(f"sanitize {name} leaves nothing", s == 200 and r.get("after") == 0 and r.get("data"),
              f"{r.get('before')} → {r.get('after')}, {time.time() - t:.1f}s"
              + (f", still there: {left}" if left else "")
              + ("" if s == 200 else f", status {s}: {r.get('detail')}"))


def check_tor(check: Check, proxy: str):
    check.section("onion mirror")
    for path in ("/", "/about", "/contact", "/donate", "/pgp.txt"):
        out = subprocess.run(["curl", "-s", "--socks5-hostname", proxy, "-m", "90",
                              "-w", "\n%{http_code}", ONION + path],
                             capture_output=True, text=True)
        body, _, code = out.stdout.rpartition("\n")
        ok = code == "200" and ("<script" not in body)
        check(f"onion {path}", ok, f"status {code or out.stderr.strip()[:80]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default="https://unredact.tools")
    ap.add_argument("--token-file", help="file holding UNREDACT_RELAY_TOKEN "
                    "(a bare token, or a KEY=value env file)")
    ap.add_argument("--min-mb", type=int, default=200, help="expected upload limit")
    ap.add_argument("--tor", action="store_true", help="also check the onion mirror")
    ap.add_argument("--tor-proxy", default="127.0.0.1:9050")
    ap.add_argument("--send-email", action="store_true", help="send one real contact message")
    ap.add_argument("--no-uploads", action="store_true", help="skip scan and sanitize")
    ap.add_argument("-v", "--verbose", action="store_true", help="show details for passing checks")
    args = ap.parse_args()
    base = args.url.rstrip("/")

    token = os.environ.get("UNREDACT_RELAY_TOKEN", "").strip()
    if args.token_file:
        raw = Path(args.token_file).expanduser().read_text().strip()
        token = raw.split("=", 1)[1].strip() if "=" in raw else raw

    print(f"sitecheck {base}  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    check = Check(args.verbose)
    started = time.time()
    check_pages(base, check)
    info = check_info(base, check, args.min_mb)
    before = (info or {}).get("totals")
    check_contact(base, check, args.send_email)

    if args.no_uploads:
        pass
    elif not token:
        print("\nscan + sanitize\n  skip  no relay token (set UNREDACT_RELAY_TOKEN or --token-file); "
              "uploads without it would count toward the public totals")
    else:
        with tempfile.TemporaryDirectory(prefix="sitecheck-") as tmp:
            check_uploads(base, check, token, samples(Path(tmp)))

    if before is not None:
        _, _, b = request(base + "/api/info")
        try:
            after = json.loads(b).get("totals")
        except ValueError:
            after = None
        # real visitors may have used the site meanwhile, so allow a small rise
        moved = (after or {}).get("checked", 0) - before.get("checked", 0)
        check.section("totals")
        check("test traffic left the public totals alone", after is not None and 0 <= moved <= 3,
              f"{before} → {after}")

    if args.tor:
        check_tor(check, args.tor_proxy)

    total = check.passed + len(check.failed)
    print(f"\n{check.passed}/{total} passed in {time.time() - started:.0f}s")
    if check.failed:
        print("failed: " + ", ".join(check.failed))
    return 1 if check.failed else 0


if __name__ == "__main__":
    sys.exit(main())
