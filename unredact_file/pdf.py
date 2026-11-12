"""PDF scanner + sanitizer."""
from __future__ import annotations

import io
import os
import re

import pikepdf
import pymupdf

from .common import scan_strings, scan_text_canaries
from .image import sanitize_image_bytes, scan_image
from .model import Report
from .xmp import scan_xmp

pymupdf.TOOLS.mupdf_display_errors(False)

META_INTEREST = {
    "author": ("identity", "high", "Author"),
    "creator": ("software", "low", "Creator application"),
    "producer": ("software", "low", "PDF producer"),
    "title": ("hidden", "low", "Title"),
    "subject": ("hidden", "low", "Subject"),
    "keywords": ("hidden", "low", "Keywords"),
    "creationDate": ("time", "low", "Created"),
    "modDate": ("time", "low", "Modified"),
}


def _pdfdate(s):
    m = re.match(r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(.*)", s or "")
    if not m:
        return s
    y, mo, d, h, mi, sec, tz = m.groups()
    out = f"{y}-{mo or '01'}-{d or '01'} {h or '00'}:{mi or '00'}:{sec or '00'}"
    tz = (tz or "").replace("'", "")
    return out + (f" {tz}" if tz and tz != "Z" else " UTC" if tz == "Z" else "")


def _is_dark(fill):
    return fill is not None and len(fill) >= 3 and max(fill[:3]) < 0.2


def _covered_text(page, rect):
    words = page.get_text("words")
    hits = []
    for w in words:
        wr = pymupdf.Rect(w[:4])
        inter = wr & rect
        if not inter.is_empty and inter.get_area() > 0.5 * wr.get_area():
            hits.append(w[4])
    return " ".join(hits)


def _redaction_boxes(page):
    """Rects that look like redaction boxes: dark filled vectors, dark images, unapplied redact annots."""
    boxes = []
    annot_rects = [a.rect for a in page.annots() or []]
    for d in page.get_drawings():
        r = d.get("rect")
        if any(ar.contains(r) for ar in annot_rects):
            continue  # annotation icon appearance, not page content
        if r and _is_dark(d.get("fill")) and r.width > 4 and r.height > 4:
            boxes.append(("black box (vector)", r))
    for info in page.get_image_info(xrefs=True):
        r = pymupdf.Rect(info["bbox"])
        if r.width < 4 or r.height < 4:
            continue
        try:
            pix = page.get_pixmap(clip=r, dpi=24)
            samples = pix.samples
            if samples and max(samples) < 60:
                boxes.append(("black box (image)", r))
        except Exception:  # noqa: BLE001
            pass
    for a in page.annots() or []:
        if a.type[0] == pymupdf.PDF_ANNOT_REDACT:
            boxes.append(("unapplied redaction annotation", a.rect))
        elif a.type[0] in (pymupdf.PDF_ANNOT_SQUARE, pymupdf.PDF_ANNOT_HIGHLIGHT,
                           pymupdf.PDF_ANNOT_INK):
            fill = (a.colors or {}).get("fill") or (a.colors or {}).get("stroke")
            if _is_dark(fill):
                boxes.append(("black annotation", a.rect))
    return boxes


def _hidden_spans(page):
    """(kind, text, rect) for white, microscopic or transparent text spans."""
    out = []
    for b in page.get_text("dict").get("blocks", []):
        for line in b.get("lines", []):
            for s in line.get("spans", []):
                t = s.get("text", "").strip()
                if not t:
                    continue
                if s.get("color") == 0xFFFFFF:
                    out.append(("White (invisible) text", t, pymupdf.Rect(s["bbox"])))
                elif s.get("size", 10) < 1.5:
                    out.append(("Microscopic text", t, pymupdf.Rect(s["bbox"])))
                elif s.get("alpha", 255) == 0:
                    out.append(("Fully transparent text", t, pymupdf.Rect(s["bbox"])))
    return out


def _revisions(raw: bytes):
    """Return byte offsets where earlier incremental revisions end."""
    ends = [m.end() for m in re.finditer(rb"%%EOF[\r\n]*", raw)]
    if b"/Linearized" in raw[:2048] and len(ends) >= 2:
        ends = ends[1:]  # first %%EOF of a linearized file is part of the structure
    return ends[:-1]


def scan_pdf(path, report: Report | None = None) -> Report:
    raw = open(path, "rb").read() if isinstance(path, str) else path
    report = report or Report(str(path), "application/pdf")
    report.filetype = "application/pdf"
    try:
        doc = pymupdf.open(stream=raw, filetype="pdf")
    except Exception as e:  # noqa: BLE001
        report.errors.append(f"cannot open PDF: {e}")
        return report
    if doc.needs_pass:
        report.add("info", "structure", "Encrypted PDF - needs password", "", "file")
        return report
    report.add("info", "structure", f"PDF {doc.metadata.get('format', '')}, {doc.page_count} pages",
               "", "file")

    # ---- Info dictionary
    for k, (bucket, sev, lbl) in META_INTEREST.items():
        v = (doc.metadata or {}).get(k)
        if not v:
            continue
        if "Date" in k:
            v = _pdfdate(v)
            report.fact("time", f"{lbl.lower()} {v}")
        report.add(sev, bucket, lbl, v, "Info dict")
        if bucket in ("identity", "software"):
            report.fact(bucket, f"{lbl}: {v}")
        scan_strings(report, v, "Info dict")
    try:
        with pikepdf.open(io.BytesIO(raw)) as pk:
            std = {"/Author", "/Creator", "/Producer", "/Title", "/Subject", "/Keywords",
                   "/CreationDate", "/ModDate", "/Trapped"}
            for k, v in pk.docinfo.items():
                if k not in std:
                    report.add("medium", "hidden-content", f"Custom metadata field {k}", str(v)[:300],
                               "Info dict")
                    scan_strings(report, str(v), "Info dict")
            if "/ID" in pk.trailer:
                report.add("info", "tracking", "Persistent document ID",
                           "Trailer /ID links this file to its other versions/copies", "trailer")
    except Exception as e:  # noqa: BLE001
        report.errors.append(f"pikepdf: {e}")

    xmp = doc.get_xml_metadata()
    if xmp:
        scan_xmp(report, xmp.encode(), "XMP")

    # ---- previous revisions
    revs = _revisions(raw)
    if revs:
        final_text = "".join(p.get_text() for p in doc)
        lost = []
        for n, end in enumerate(revs, 1):
            old = raw[:end]
            report.artifact(f"revision_{n}.pdf", old, "earlier saved version")
            try:
                od = pymupdf.open(stream=old, filetype="pdf")
                old_words = set("".join(p.get_text() for p in od).split())
                gone = [w for w in old_words if w not in final_text]
                lost.extend(gone)
            except Exception:  # noqa: BLE001
                pass
        detail = f"{len(revs)} earlier version(s) are still inside the file (extracted)."
        if lost:
            detail += f" Text removed since then: {' '.join(sorted(set(lost))[:40])}"
        report.add("critical" if lost else "high", "hidden-content",
                   "Incremental saves: previous versions recoverable", detail, "file structure")
        report.fact("hidden", f"{len(revs)} earlier revision(s)" +
                    (f" incl. deleted text: {' '.join(sorted(set(lost))[:12])}" if lost else ""))

    # ---- embedded files
    for name in doc.embfile_names():
        try:
            data = doc.embfile_get(name)
            report.artifact(f"attachment_{os.path.basename(name)}", data, "embedded file")
        except Exception:  # noqa: BLE001
            data = b""
        report.add("high", "hidden-content", "Embedded file attachment",
                   f"{name} ({len(data)} bytes, extracted)", "EmbeddedFiles")
        report.fact("hidden", f"attached file '{name}'")

    # ---- dangerous / phone-home objects
    js = launch = submit = remote = 0
    for x in range(1, doc.xref_length()):
        try:
            obj = doc.xref_object(x, compressed=False)
        except Exception:  # noqa: BLE001
            continue
        js += "/JavaScript" in obj or "/JS " in obj or "/JS(" in obj or "/JS<" in obj
        launch += "/Launch" in obj
        submit += "/SubmitForm" in obj
        remote += "/GoToR" in obj or "/ImportData" in obj
    if js:
        report.add("high", "tracking", "JavaScript present",
                   f"{js} object(s) - can phone home when opened in some readers", "objects")
        report.fact("tracking", "embedded JavaScript")
    if launch:
        report.add("high", "tracking", "Launch action", "Can try to run a program", "objects")
    if submit:
        report.add("medium", "tracking", "Form submit action", "Can send form data to a URL", "objects")
    if remote:
        report.add("medium", "tracking", "Remote document reference", "", "objects")

    # ---- per page
    all_text = []
    link_domains = set()
    embedded_imgs = 0
    for pno, page in enumerate(doc, 1):
        src = f"page {pno}"
        all_text.append(page.get_text())
        for label, rect in _redaction_boxes(page):
            hidden = _covered_text(page, rect)
            if hidden.strip():
                report.add("critical", "redaction", f"FAILED REDACTION ({label})",
                           f"Text still present under the box: \"{hidden[:300]}\"", src)
                report.fact("hidden", f"text under redaction box p{pno}: \"{hidden[:80]}\"")
        # invisible / white / tiny text
        for kind, t, _r in _hidden_spans(page):
            report.add("high", "hidden-content", kind, t[:200], src)
            report.fact("hidden", f"{kind.split(' (')[0].lower()} p{pno}: \"{t[:60]}\"")
        # text outside the visible page area
        try:
            outside = page.get_text("text", clip=pymupdf.Rect(-1e5, -1e5, 1e5, 1e5)).strip()
            inside = page.get_text("text").strip()
            if len(outside) > len(inside) + 5:
                report.add("high", "hidden-content", "Text positioned outside the visible page",
                           outside.replace(inside, "")[:200], src)
        except Exception:  # noqa: BLE001
            pass
        for a in page.annots() or []:
            info = a.info or {}
            if a.type[0] in (pymupdf.PDF_ANNOT_LINK, pymupdf.PDF_ANNOT_WIDGET):
                continue
            who = info.get("title", "")
            body = info.get("content", "")
            report.add("high" if who else "medium", "identity" if who else "hidden-content",
                       f"Annotation ({a.type[1]})" + (f" by '{who}'" if who else ""),
                       body[:200], src)
            if who:
                report.fact("identity", f"annotation author: {who}")
            if a.type[0] == pymupdf.PDF_ANNOT_FILE_ATTACHMENT:
                try:
                    report.artifact(f"p{pno}_annot_attachment", a.get_file(), "attachment annot")
                except Exception:  # noqa: BLE001
                    pass
        for w in page.widgets() or []:
            if w.field_value not in (None, "", "Off", False):
                report.add("low", "hidden-content", f"Form field '{w.field_name}'",
                           str(w.field_value)[:200], src)
        for ln in page.get_links():
            if ln.get("uri"):
                m = re.match(r"https?://([^/]+)", ln["uri"])
                if m:
                    link_domains.add(m.group(1))
        # embedded image metadata (JPEGs keep their EXIF inside PDFs)
        for img in page.get_images(full=True):
            xref = img[0]
            if embedded_imgs >= 40:
                break
            try:
                ex = doc.extract_image(xref)
            except Exception:  # noqa: BLE001
                continue
            embedded_imgs += 1
            if ex.get("ext") in ("jpeg", "jpg", "jpx") and (b"Exif" in ex["image"][:64 * 1024]
                                                          or b"xmpmeta" in ex["image"][:64 * 1024]):
                sub = scan_image(ex["image"], label=f"img{xref}")
                sub.findings = [f for f in sub.findings if f.severity != "info"]
                report.merge(sub, f"{src} image xref {xref}")
    if link_domains:
        report.add("low", "tracking", "External links",
                   ", ".join(sorted(link_domains)[:15]), "links")
    try:
        hidden_layers = [o["name"] for o in doc.get_ocgs().values() if not o.get("on", True)]
        if hidden_layers:
            report.add("high", "hidden-content", "Hidden layers (optional content)",
                       ", ".join(hidden_layers), "OCG")
            report.fact("hidden", f"hidden layers: {', '.join(hidden_layers)}")
    except Exception:  # noqa: BLE001
        pass
    if not "".join(all_text).strip() and doc.page_count:
        report.add("info", "structure", "No text layer (scanned/image-only)",
                   "Scanned pages may still carry printer tracking dots (MIC)", "file")
    scan_text_canaries(report, "".join(all_text), "document text")
    return report


# ====================================================================== sanitize
def _strip_info(pdf_bytes: bytes) -> bytes:
    """Final pass with pikepdf: wipe Info + XMP, fresh random /ID, full rewrite."""
    with pikepdf.open(io.BytesIO(pdf_bytes)) as pk:
        for k in list(pk.docinfo.keys()):
            del pk.docinfo[k]
        if "/Info" in pk.trailer:
            del pk.trailer["/Info"]
        for key in ("/Metadata", "/PieceInfo", "/Names", "/OpenAction", "/AA",
                    "/AcroForm", "/Threads", "/SpiderInfo", "/Perms", "/OCProperties"):
            if key in pk.Root:
                if key == "/AcroForm":
                    continue  # kept only in text mode; raster mode never has it
                del pk.Root[key]
        for page in pk.pages:
            for key in ("/PieceInfo", "/Metadata", "/AA", "/Thumb"):
                if key in page.obj:
                    del page.obj[key]
        rnd = pikepdf.String(os.urandom(16))
        pk.trailer.ID = pikepdf.Array([rnd, rnd])
        out = io.BytesIO()
        pk.save(out, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate,
                deterministic_id=False, fix_metadata_version=False)
        return out.getvalue()


def sanitize_pdf(path, out_path, mode="raster", dpi=200):
    """mode='raster': every page flattened to a picture (Dangerzone-style, removes EVERYTHING).
    mode='text': keep selectable text; burn in redactions, scrub metadata/JS/attachments/annots."""
    src = pymupdf.open(path)
    if mode == "raster":
        out = pymupdf.open()
        for page in src:
            pix = page.get_pixmap(dpi=dpi, annots=False)
            np = out.new_page(width=page.rect.width, height=page.rect.height)
            np.insert_image(np.rect, stream=pix.tobytes("jpeg", jpg_quality=90))
        out.set_metadata({})
        data = out.tobytes(garbage=4, deflate=True)
    else:
        for page in src:
            boxes = _redaction_boxes(page)
            for _label, rect in boxes:
                page.add_redact_annot(rect, fill=(0, 0, 0))
            hidden = _hidden_spans(page)
            for _kind, _t, rect in hidden:
                page.add_redact_annot(rect, fill=False)
            if boxes or hidden:
                page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)
            for a in list(page.annots() or []):
                page.delete_annot(a)
        src.scrub(attached_files=True, clean_pages=True, embedded_files=True, hidden_text=True,
                  javascript=True, metadata=True, redactions=True, redact_images=0,
                  remove_links=False, reset_fields=True, reset_responses=True,
                  thumbnails=True, xml_metadata=True)
        # strip EXIF/XMP from embedded JPEGs (they keep it inside PDFs)
        done = set()
        for page in src:
            for img in page.get_images(full=True):
                x = img[0]
                if x in done:
                    continue
                done.add(x)
                try:
                    if src.xref_get_key(x, "Filter")[1] != "/DCTDecode":
                        continue
                    stream = src.xref_stream_raw(x)
                    if b"Exif" in stream[:64 * 1024] or b"xmpmeta" in stream[:64 * 1024]:
                        clean, _ = sanitize_image_bytes(stream)
                        page.replace_image(x, stream=clean)
                except Exception:  # noqa: BLE001
                    continue
        src.set_metadata({})
        src.del_xml_metadata()
        data = src.tobytes(garbage=4, deflate=True, clean=True)
    data = _strip_info(data)
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path
