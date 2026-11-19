"""unredact.tools web app: upload a file, see what it leaks, download a clean copy.

Run:   unredact.tools web            (http://127.0.0.1:8000)
Needs: pip install "unredact-tools[web]"

Privacy model: each upload is written to a private temp dir, processed, and the
dir is deleted before the response is sent. Nothing is persisted, filenames are
never logged, and access logging is off. Responses are marked no-store.

Env:
  UNREDACT_MAX_MB    max upload size in MB (default 50)
  UNREDACT_WORKERS   concurrent scans/sanitizes (default 2; OCR is CPU-heavy)
  UNREDACT_CORS      comma-separated origins allowed to call the API from another
                     site, e.g. https://unredact.tools (for the Cloudflare Pages site)
  UNREDACT_CONTACT_TO    address the /contact form emails (form is off when unset)
  UNREDACT_CONTACT_FROM  sender address for those emails (default noreply@<host>)
  UNREDACT_SMTP          SMTP relay host[:port] (default localhost:25)
  UNREDACT_COUNTER       file holding two running totals, files checked and files sanitized
                         (off when unset). It stores those two integers and nothing else.
  UNREDACT_RELAY_TOKEN   shared secret for trusted clients: the onion edition (relays
                         contact messages, bumps the totals) and tools/sitecheck.py, whose
                         test scans and sanitizes are left out of the totals. Off when unset.
"""
from __future__ import annotations

import asyncio
import base64
import html
import mimetypes
import os
import re
import secrets
import shutil
import smtplib
import tempfile
import time
from email.message import EmailMessage
from importlib import resources

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse, Response)
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__, media, vision
from ..image import VisualOpts
from ..scanner import detect, sanitize, scan
from ..tips import TIPS, random_tip

if vision.HAVE_CV:
    vision.cv2.setNumThreads(0)  # run sequentially; see the thread caps in web/__init__.py

MAX_MB = int(os.environ.get("UNREDACT_MAX_MB", "50"))
WORKERS = asyncio.Semaphore(int(os.environ.get("UNREDACT_WORKERS", "2")))
CONTACT_TO = os.environ.get("UNREDACT_CONTACT_TO", "").strip()
CONTACT_FROM = os.environ.get("UNREDACT_CONTACT_FROM", "").strip()
SMTP = os.environ.get("UNREDACT_SMTP", "localhost:25")
COUNTER = os.environ.get("UNREDACT_COUNTER", "").strip()
RELAY_TOKEN = os.environ.get("UNREDACT_RELAY_TOKEN", "").strip()
CONTACT_LIMIT = 5  # messages per sender per hour
ARTIFACT_CAP = 25 * 1024 * 1024  # inline recovered artifacts up to this total size

STATIC = resources.files(__package__) / "static"
GUIDE_DIR = resources.files(__package__) / "guides"
SITE = "https://unredact.tools"  # canonical origin for the sitemap and link tags
ROOT_ICONS = {"/favicon.ico": "favicon.ico", "/apple-touch-icon.png": "apple-touch-icon.png"}

# Search landing pages: slug -> head and hero text; the body is guides/<slug>.html.
# The main pages reach them only through the /guides hub linked in the footer.
GUIDES = {
    "remove-photo-metadata": {
        "title": "Remove GPS location and EXIF metadata from a photo · unredact.tools",
        "description": "See the GPS coordinates, camera serial and other EXIF data a photo carries, "
                       "then download a clean copy with all of it removed. Free, no signup, nothing stored.",
        "h1": 'Remove the location <span class="hl">from a photo</span>.',
        "lede": "Drop a photo below to see the GPS coordinates, camera serial number and other "
                "metadata it carries. Then download a clean copy with all of it removed.",
    },
    "check-pdf-redaction": {
        "title": "Check if a PDF redaction actually worked · unredact.tools",
        "description": "A black box drawn over text doesn't remove it. Check a redacted PDF for text "
                       "still readable under the boxes, earlier versions and metadata, then fix it.",
        "h1": 'Check if a PDF redaction <span class="hl">actually worked</span>.',
        "lede": "A black box drawn over text doesn't remove the text. Drop a redacted PDF below to "
                "see whether anything is still readable underneath, then download a copy with real redactions.",
    },
    "remove-word-metadata": {
        "title": "Remove author names, tracked changes and hidden data from Word files · unredact.tools",
        "description": "Word, Excel and PowerPoint files keep author names, tracked changes, comments "
                       "and file paths. See what yours carries and download a clean copy.",
        "h1": 'Remove hidden data <span class="hl">from a Word document</span>.',
        "lede": "Word files keep author names, every tracked change, comments and file paths long "
                "after you stop seeing them. Drop a document below to see what yours carries, then "
                "download a clean copy.",
    },
    "find-hidden-text-in-pdf": {
        "title": "Find hidden text in a PDF · unredact.tools",
        "description": "Find white-on-white, microscopic and off-page text, hidden layers, earlier "
                       "versions and invisible watermarks in a PDF.",
        "h1": 'Find hidden text <span class="hl">in a PDF</span>.',
        "lede": "PDFs can carry text nobody sees on screen: white on white, shrunk to a dot, placed "
                "off the page, or left behind in an earlier version of the file. Drop a PDF below to find it.",
    },
    "famous-metadata-leaks": {
        "title": "Real cases where a file gave someone away · unredact.tools",
        "description": "Failed redactions, GPS-tagged photos and printer tracking dots: real cases "
                       "where a shared file revealed more than intended, and what would have caught it.",
        "h1": 'Files that <span class="hl">gave people away</span>.',
        "lede": "Every one of these was a file someone shared on purpose. What it revealed was an "
                "accident. Drop a file below to check yours before you send it.",
    },
    "remove-video-metadata": {
        "title": "Remove location and metadata from a video · unredact.tools",
        "description": "Phone, action camera and drone videos carry GPS location, device details and "
                       "timed location tracks. Check a video and remove them without re-encoding.",
        "h1": 'Remove the location <span class="hl">from a video</span>.',
        "lede": "Videos from phones, action cameras and drones record where they were filmed, on "
                "what device, and sometimes a GPS track of every second. Drop a video below to see "
                "what yours carries, then download a clean copy at full quality.",
    },
    "printer-tracking-dots": {
        "title": "Printer tracking dots: find and remove them from a scan · unredact.tools",
        "description": "Many color laser printers add tiny yellow dots that encode the printer's serial "
                       "number and the print time. Check a scan or scanned PDF for them and remove them.",
        "h1": 'Find printer <span class="hl">tracking dots</span>.',
        "lede": "Many color laser printers add a grid of tiny yellow dots to every page, too faint to "
                "notice. They can encode the printer's serial number and when the page was printed. "
                "Drop a scan or scanned PDF below to check for them.",
    },
    "remove-location-from-iphone-photos": {
        "title": "Remove location from iPhone photos before you share them · unredact.tools",
        "description": "iPhone photos save exactly where they were taken. How to stop it, how to remove "
                       "location from photos you've taken, and how to check what a photo still carries.",
        "h1": 'Remove the location <span class="hl">from iPhone photos</span>.',
        "lede": "Every iPhone photo taken with Location Services on records exactly where it was "
                "taken. Drop a photo below to see what yours carries, or use the iPhone settings "
                "further down.",
    },
    "where-was-this-photo-taken": {
        "title": "Where was this photo taken? See what a photo reveals · unredact.tools",
        "description": "Find out whether a photo reveals where it was taken: GPS coordinates, timezone, "
                       "device and the clues in the picture itself. Check your own photos before you share them.",
        "h1": 'Does your photo say <span class="hl">where it was taken</span>?',
        "lede": "If a photo has GPS data, anyone with the file can put a pin on a map. Drop a photo "
                "below to see exactly what it reveals about where and when it was taken.",
    },
}

# remaining finding title fragment -> sanitize option that fixes it
HINTS = {
    "Readable in the image": "redact_text",
    "Boarding pass": "redact_codes",
    "QR Code": "redact_codes",
    "face(s) visible": "blur_faces",
    "License plate": "redact_plates",
}

app = FastAPI(title="unredact.tools", version=__version__, docs_url=None, redoc_url=None,
              openapi_url=None)


CORS = [o.strip() for o in os.environ.get("UNREDACT_CORS", "").split(",") if o.strip()]
if CORS:
    app.add_middleware(CORSMiddleware, allow_origins=CORS, allow_methods=["GET", "POST"],
                       allow_headers=["*"], max_age=600)


@app.middleware("http")
async def headers(request: Request, call_next):
    host = request.headers.get("host", "")
    if host.startswith("www."):  # one canonical host for search engines
        query = f"?{request.url.query}" if request.url.query else ""
        resp = RedirectResponse(f"https://{host[4:]}{request.url.path}{query}", status_code=301)
    else:
        resp = await call_next(request)
    # assets carry no user data, so let browsers keep them; everything else stays no-store
    static = request.url.path.startswith("/static/") or request.url.path in ROOT_ICONS
    resp.headers["Cache-Control"] = "public, max-age=86400" if static else "no-store"
    resp.headers["Strict-Transport-Security"] = "max-age=31536000"  # ignored over plain http
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
    return resp


class _GZipPages(GZipMiddleware):
    """Compress pages and assets. API responses are skipped: clean files come back as
    large base64 payloads, and gzipping those costs more CPU than it saves."""

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        await super().__call__(scope, receive, send)


app.add_middleware(_GZipPages, minimum_size=1000)


class _HeadAsGet:
    """Answer HEAD like GET without the body. FastAPI's GET routes reject HEAD, which
    crawlers, link checkers and uptime monitors use."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "HEAD":
            return await self.app(scope, receive, send)

        async def headers_only(message):
            if message["type"] == "http.response.body":
                message = {**message, "body": b""}
            await send(message)

        await self.app({**scope, "method": "GET"}, receive, headers_only)


app.add_middleware(_HeadAsGet)


def _safe_name(name: str | None) -> str:
    """Keep the uploaded basename (filenames leak too, and the scan checks them)
    but make it safe to put on disk."""
    base = os.path.basename((name or "upload").replace("\\", "/"))
    base = re.sub(r"[^\w.\- ()@+,]", "_", base).strip(" .") or "upload"
    if len(base) > 120:
        root, ext = os.path.splitext(base)
        base = root[:120 - len(ext)] + ext
    return base


class _Upload:
    """Write an upload into a private temp dir; delete the dir on exit."""

    def __init__(self, f: UploadFile):
        self.f = f

    async def __aenter__(self):
        self.dir = tempfile.mkdtemp(prefix="unredact-")
        self.name = _safe_name(self.f.filename)
        self.path = os.path.join(self.dir, self.name)
        limit, size = MAX_MB * 1024 * 1024, 0
        try:
            with open(self.path, "wb") as out:
                while chunk := await self.f.read(1 << 20):
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, f"file is over the {MAX_MB} MB limit")
                    out.write(chunk)
            if size == 0:
                raise HTTPException(400, "empty file")
        except BaseException:  # __aexit__ won't run if __aenter__ fails
            shutil.rmtree(self.dir, ignore_errors=True)
            raise
        return self

    async def __aexit__(self, *exc):
        shutil.rmtree(self.dir, ignore_errors=True)


def _report_dict(r, name):
    d = r.to_dict()
    d["path"] = name
    d["kind"] = detect(r.path) if os.path.exists(r.path) else None
    return d


@app.get("/api/info")
def info():
    return {
        "version": __version__,
        "max_mb": MAX_MB,
        "ffmpeg": media.have_ffmpeg(),
        "ocr": vision.available() and vision.HAVE_OCR,
        "vision": vision.available(),
        "totals": _counts(),
    }


def _counts(bump: str | None = None) -> dict[str, int] | None:
    """Read, and optionally bump ("checked" or "scrubbed"), the running totals. Locked,
    since Passenger and uvicorn can run several worker processes against the same file."""
    if not COUNTER:
        return None
    try:
        import fcntl
        fd = os.open(COUNTER, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            nums = [int(v) for v in f.read().split()]
            if len(nums) == 1:  # older single-total file: that total was scrubs
                nums = [nums[0], nums[0]]
            checked, scrubbed = (nums + [0, 0])[:2]
            if bump:
                checked += bump == "checked"
                scrubbed += bump == "scrubbed"
                f.seek(0)
                f.truncate()
                f.write(f"{checked} {scrubbed}")
            return {"checked": checked, "scrubbed": scrubbed}
    except (OSError, ValueError, ImportError):  # never let the counter break a request
        return None


def _relayed(request: Request) -> bool:
    """True when the request carries the relay token of a trusted front end."""
    auth = request.headers.get("authorization", "")
    want = f"Bearer {RELAY_TOKEN}".encode()
    return bool(RELAY_TOKEN) and secrets.compare_digest(auth.encode(), want)


@app.post("/api/totals/{kind}")
def bump_total(kind: str, request: Request):
    """Let the onion edition add its scans and cleans to the shared totals."""
    if not _relayed(request):
        raise HTTPException(403, "forbidden")
    if kind not in ("checked", "scrubbed"):
        raise HTTPException(404, "unknown total")
    return {"totals": _counts(kind)}


@app.get("/api/tip")
def tip():
    return {"tip": random_tip(), "count": len(TIPS)}


@app.post("/api/scan")
async def api_scan(request: Request, file: UploadFile = File(...), fast: bool = Form(False),
                   extract: bool = Form(False)):
    async with WORKERS, _Upload(file) as up:
        r = await run_in_threadpool(scan, up.path, not fast, not fast)
        d = _report_dict(r, up.name)
        # trusted test traffic (sitecheck) isn't real use, so it doesn't count
        d["totals"] = await run_in_threadpool(_counts, None if _relayed(request) else "checked")
        if extract and r.artifacts:
            total = sum(len(a.data) for a in r.artifacts)
            if total <= ARTIFACT_CAP:
                for meta, a in zip(d["artifacts"], r.artifacts):
                    meta["data"] = base64.b64encode(a.data).decode()
            else:
                d["errors"].append(f"recovered artifacts total {total:,} bytes, too large to "
                                   "return here; use the CLI with --extract")
        return d


def _regions(text: str):
    out = []
    for part in filter(None, re.split(r"[;\s]+", text or "")):
        try:
            x, y, w, h = (int(v) for v in part.split(","))
        except ValueError:
            raise HTTPException(400, f"bad region {part!r}; expected X,Y,W,H")
        out.append((x, y, w, h))
    return out


@app.post("/api/sanitize")
async def api_sanitize(
    request: Request,
    file: UploadFile = File(...),
    pdf_mode: str = Form("raster"),
    blur_faces: bool = Form(False),
    redact_codes: bool = Form(False),
    redact_text: bool = Form(False),
    redact_plates: bool = Form(False),
    desync: bool = Form(False),
    keep_dots: bool = Form(False),
    regions: str = Form(""),
):
    if pdf_mode not in ("raster", "text"):
        raise HTTPException(400, "pdf_mode must be raster or text")
    opts = VisualOpts(blur_faces=blur_faces, redact_codes=redact_codes, redact_text=redact_text,
                      redact_plates=redact_plates, regions=_regions(regions), desync=desync,
                      remove_dots=not keep_dots)
    relayed = _relayed(request)
    async with WORKERS, _Upload(file) as up:
        def work():
            before = scan(up.path)
            notes: list[str] = []
            out = sanitize(up.path, None, pdf_mode=pdf_mode, visual=opts, notes=notes)
            after = scan(out)
            return before, out, notes, after, _counts(None if relayed else "scrubbed")
        try:
            before, out, notes, after, totals = await run_in_threadpool(work)
        except (ValueError, RuntimeError) as e:
            raise HTTPException(422, str(e))
        remaining = [f for f in after.sorted_findings()
                     if f.severity != "info" and f.category != "structure"]
        hints = sorted({opt for f in remaining for key, opt in HINTS.items() if key in f.title})
        name = os.path.basename(out)
        data = open(out, "rb").read()
        return {
            "filename": name,
            "mime": mimetypes.guess_type(name)[0] or "application/octet-stream",
            "size": len(data),
            "data": base64.b64encode(data).decode(),
            "before": sum(1 for f in before.findings if f.severity != "info"),
            "after": len(remaining),
            "remaining": [f.__dict__ for f in remaining],
            "notes": notes,
            "hints": hints,
            "totals": totals,
        }


@app.exception_handler(Exception)
async def crash(request: Request, exc: Exception):  # never echo internals or paths
    return JSONResponse({"detail": f"processing failed ({type(exc).__name__})"}, status_code=500)


@app.exception_handler(StarletteHTTPException)
async def not_found(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404 and not request.url.path.startswith("/api/"):
        return FileResponse(str(STATIC / "404.html"), status_code=404)
    return await http_exception_handler(request, exc)


@app.get("/")
def index():
    return FileResponse(str(STATIC / "index.html"))


@app.get("/about")
def about():
    return FileResponse(str(STATIC / "about.html"))


@app.get("/contact")
def contact():
    return FileResponse(str(STATIC / "contact.html"))


TOPICS = {"general": "Question", "leak": "Missed leak", "security": "Security issue",
          "bug": "Bug", "other": "Other"}
EMAIL_RE = re.compile(r"[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}")
_recent: dict[str, list[float]] = {}  # in memory only, pruned after an hour


def _one_line(v: str, cap: int) -> str:
    return " ".join(v.split())[:cap]


def _rate_limited(key: str) -> bool:
    now = time.time()
    for k in [k for k, ts in _recent.items() if ts[-1] < now - 3600]:
        del _recent[k]
    ts = [t for t in _recent.get(key, []) if t > now - 3600]
    _recent[key] = ts + [now]
    return len(ts) >= CONTACT_LIMIT


def _send(msg: EmailMessage):
    host, _, port = SMTP.partition(":")
    with smtplib.SMTP(host, int(port or 25), timeout=15) as s:
        s.send_message(msg)


@app.post("/api/contact")
async def contact_send(request: Request, name: str = Form(""), email: str = Form(""),
                       topic: str = Form("general"), message: str = Form(""),
                       website: str = Form("")):
    """Email a contact-form message. Nothing is stored; the reply goes to /contact."""
    def back(status: str):
        return RedirectResponse(f"/contact?{status}#form", status_code=303)

    if website:  # honeypot: only bots fill the hidden field
        return back("sent")
    if not CONTACT_TO:
        return back("error=off")
    message = message.strip()
    email = _one_line(email, 254)
    if not message or len(message) > 5000 or (email and not EMAIL_RE.fullmatch(email)):
        return back("error=invalid")
    who = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    if not _relayed(request) and _rate_limited(who):  # the relay rate-limits its own users
        return back("error=busy")

    name = _one_line(name, 100)
    host = request.url.hostname or "localhost"
    msg = EmailMessage()
    msg["From"] = CONTACT_FROM or f"noreply@{host}"
    msg["To"] = CONTACT_TO
    if email:
        msg["Reply-To"] = email
    msg["Subject"] = f"[{host}] {TOPICS.get(topic, 'Other')}" + (f" from {name}" if name else "")
    msg.set_content(f"Name:  {name or '(not given)'}\nEmail: {email or '(not given)'}\n"
                    f"Topic: {TOPICS.get(topic, 'Other')}\n\n{message}\n")
    try:
        await run_in_threadpool(_send, msg)
    except (OSError, smtplib.SMTPException):
        return back("error=send")
    return back("sent")


@app.get("/donate")
def donate():
    return FileResponse(str(STATIC / "donate.html"))


@app.get("/favicon.ico")
@app.get("/apple-touch-icon.png")
def root_icon(request: Request):  # browsers and crawlers ask for these at the root
    return FileResponse(str(STATIC / ROOT_ICONS[request.url.path]))


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return f"User-agent: *\nDisallow: /api/\n\nSitemap: {SITE}/sitemap.xml\n"


@app.get("/sitemap.xml")
def sitemap():
    paths = ["/", "/about", "/contact", "/donate", "/guides", *(f"/{s}" for s in GUIDES)]
    urls = "".join(f"  <url><loc>{SITE}{p}</loc></url>\n" for p in paths)
    return Response('<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                    f"{urls}</urlset>\n", media_type="application/xml")


@app.get("/guides")
def guides():
    items = "".join(
        f'    <li><a href="/{slug}">{html.escape(g["title"].removesuffix(" · unredact.tools"))}</a>'
        f'<span>{html.escape(g["description"])}</span></li>\n' for slug, g in GUIDES.items())
    page = (GUIDE_DIR / "_index.html").read_text(encoding="utf-8")
    return HTMLResponse(page.replace("{{list}}", items))


@app.get("/{slug}")
def guide(slug: str):
    if slug not in GUIDES:
        raise HTTPException(404)
    g = GUIDES[slug]
    page = (GUIDE_DIR / "_page.html").read_text(encoding="utf-8")
    for key, val in (("title", html.escape(g["title"])), ("description", html.escape(g["description"])),
                     ("url", f"{SITE}/{slug}"), ("h1", g["h1"]), ("lede", g["lede"]),
                     ("article", (GUIDE_DIR / f"{slug}.html").read_text(encoding="utf-8"))):
        page = page.replace("{{" + key + "}}", val)
    return HTMLResponse(page)


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


def serve(host="127.0.0.1", port=8000):
    import uvicorn
    uvicorn.run(app, host=host, port=port, access_log=False, server_header=False)
