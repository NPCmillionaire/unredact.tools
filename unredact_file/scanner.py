"""Type detection + dispatch for scan and sanitize."""
from __future__ import annotations

import hashlib
import io
import os
import zipfile

from . import image, media, office, pdf
from .common import clean_text, scan_strings, scan_text_canaries
from .model import Report

MEDIA_MAGIC = (b"ID3", b"fLaC", b"OggS")


def detect(path) -> str:
    with open(path, "rb") as f:
        head = f.read(64)
    if head.startswith(b"%PDF") or b"%PDF-" in head[:1024]:
        return "pdf"
    if head.startswith(b"PK"):
        try:
            names = zipfile.ZipFile(path).namelist()
            if office.office_kind(names):
                return "office"
        except zipfile.BadZipFile:
            pass
        return "zip"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"mif1", b"msf1", b"avif", b"hevc"):
            return "image"
        return "media"
    if head.startswith((b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"II*\x00", b"MM\x00*", b"BM")) or \
            (head.startswith(b"RIFF") and head[8:12] == b"WEBP"):
        return "image"
    if head.startswith(MEDIA_MAGIC) or (head.startswith(b"RIFF") and head[8:12] in (b"WAVE", b"AVI ")) \
            or head.startswith(b"\x1aE\xdf\xa3"):  # mkv/webm
        return "media"
    try:
        with open(path, "rb") as f:
            f.read(1 << 20).decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        return "unknown"


def scan(path) -> Report:
    raw = open(path, "rb").read()
    r = Report(path, "?", len(raw), hashlib.sha256(raw).hexdigest())
    kind = detect(path)
    r.add("info", "structure", "Filename", os.path.basename(path), "filesystem")
    scan_strings(r, os.path.basename(path), "filename", "medium")
    if kind == "image":
        image.scan_image(raw, r)
    elif kind == "pdf":
        pdf.scan_pdf(raw, r)
    elif kind == "office":
        office.scan_office(raw, r)
    elif kind == "media":
        media.scan_media(path, r)
    elif kind == "text":
        r.filetype = "text"
        text = raw.decode("utf-8")
        scan_text_canaries(r, text, "text")
    elif kind == "zip":
        r.filetype = "zip"
        z = zipfile.ZipFile(io.BytesIO(raw))
        r.add("medium", "hidden-content", f"Zip archive with {len(z.namelist())} entries",
              "Entry names, timestamps and comments travel with it", "zip")
        if z.comment:
            r.add("medium", "hidden-content", "Zip comment", z.comment.decode("utf-8", "replace"), "zip")
        for n in z.namelist():
            scan_strings(r, n, "zip entry name", "medium")
    else:
        r.filetype = "unknown"
        r.errors.append("unsupported file type")
    return r


def default_out(path, ext=None):
    root, e = os.path.splitext(path)
    return f"{root}.clean{ext or e}"


def sanitize(path, out=None, pdf_mode="raster") -> str:
    kind = detect(path)
    if kind == "image":
        data, ext = image.sanitize_image_bytes(open(path, "rb").read())
        out = out or default_out(path, ext)
        open(out, "wb").write(data)
    elif kind == "pdf":
        out = out or default_out(path)
        pdf.sanitize_pdf(path, out, mode=pdf_mode)
    elif kind == "office":
        out = out or default_out(path)
        open(out, "wb").write(office.sanitize_office(open(path, "rb").read()))
    elif kind == "media":
        out = out or default_out(path)
        media.sanitize_media(path, out)
    elif kind == "text":
        out = out or default_out(path)
        text = open(path, encoding="utf-8").read()
        open(out, "w", encoding="utf-8").write(clean_text(text.lstrip("﻿")))
    else:
        raise ValueError(f"don't know how to sanitize a {kind} file")
    return out
