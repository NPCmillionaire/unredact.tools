"""unredact.tools web app: upload a file, see what it leaks, download a clean copy.

Run:   unredact.tools web            (http://127.0.0.1:8000)
Needs: pip install "unredact-tools[web]"

Privacy model: each upload is written to a private temp dir, processed, and the
dir is deleted before the response is sent. Nothing is persisted, filenames are
never logged, and access logging is off. Responses are marked no-store.

Env:
  UNREDACT_MAX_MB    max upload size in MB (default 50)
  UNREDACT_WORKERS   concurrent scans/sanitizes (default 2; OCR is CPU-heavy)
"""
from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
import re
import shutil
import tempfile
from importlib import resources

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, media, vision
from ..image import VisualOpts
from ..scanner import detect, sanitize, scan
from ..tips import TIPS, random_tip

MAX_MB = int(os.environ.get("UNREDACT_MAX_MB", "50"))
WORKERS = asyncio.Semaphore(int(os.environ.get("UNREDACT_WORKERS", "2")))
ARTIFACT_CAP = 25 * 1024 * 1024  # inline recovered artifacts up to this total size

STATIC = resources.files(__package__) / "static"

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


@app.middleware("http")
async def headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
    return resp


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
    }


@app.get("/api/tip")
def tip():
    return {"tip": random_tip(), "count": len(TIPS)}


@app.post("/api/scan")
async def api_scan(file: UploadFile = File(...), fast: bool = Form(False),
                   extract: bool = Form(False)):
    async with WORKERS, _Upload(file) as up:
        r = await run_in_threadpool(scan, up.path, not fast, not fast)
        d = _report_dict(r, up.name)
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
    async with WORKERS, _Upload(file) as up:
        def work():
            before = scan(up.path)
            notes: list[str] = []
            out = sanitize(up.path, None, pdf_mode=pdf_mode, visual=opts, notes=notes)
            after = scan(out)
            return before, out, notes, after
        try:
            before, out, notes, after = await run_in_threadpool(work)
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
        }


@app.exception_handler(Exception)
async def crash(request: Request, exc: Exception):  # never echo internals or paths
    return JSONResponse({"detail": f"processing failed ({type(exc).__name__})"}, status_code=500)


@app.get("/")
def index():
    return FileResponse(str(STATIC / "index.html"))


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


def serve(host="127.0.0.1", port=8000):
    import uvicorn
    uvicorn.run(app, host=host, port=port, access_log=False, server_header=False)
