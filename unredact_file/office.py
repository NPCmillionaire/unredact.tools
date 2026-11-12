"""Office Open XML (docx/xlsx/pptx + macro variants) scanner + sanitizer."""
from __future__ import annotations

import io
import posixpath
import zipfile
from datetime import datetime

from lxml import etree

from .common import clean_text, scan_strings, scan_text_canaries
from .image import sanitize_image_bytes, scan_image
from .model import Report

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
SS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

PARSER = etree.XMLParser(recover=True, huge_tree=True, remove_blank_text=False)


def _w(tag):
    return f"{{{W}}}{tag}"


def office_kind(names):
    if any(n.startswith("word/") for n in names):
        return "docx"
    if any(n.startswith("xl/") for n in names):
        return "xlsx"
    if any(n.startswith("ppt/") for n in names):
        return "pptx"
    return None


def _xml(data):
    try:
        return etree.fromstring(data, PARSER)
    except Exception:  # noqa: BLE001
        return None


def _local(el):
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


CORE = {
    "creator": ("identity", "high", "Author"),
    "lastModifiedBy": ("identity", "high", "Last modified by"),
    "created": ("time", "low", "Created"),
    "modified": ("time", "low", "Last saved"),
    "lastPrinted": ("time", "medium", "Last printed"),
    "revision": ("structure", "low", "Revision number (times saved)"),
    "title": ("hidden-content", "low", "Title"),
    "subject": ("hidden-content", "low", "Subject"),
    "keywords": ("hidden-content", "medium", "Keywords"),
    "description": ("hidden-content", "medium", "Comments field"),
    "category": ("hidden-content", "low", "Category"),
    "contentStatus": ("hidden-content", "low", "Status"),
}
APP = {
    "Company": ("network", "high", "Company / organisation"),
    "Manager": ("identity", "high", "Manager"),
    "Template": ("software", "low", "Template"),
    "TotalTime": ("time", "medium", "Total editing time (minutes)"),
    "Application": ("software", "low", "Application"),
    "AppVersion": ("software", "low", "Application version"),
    "HyperlinkBase": ("network", "medium", "Hyperlink base"),
}


def scan_office(raw: bytes, report: Report | None = None, label="document", depth=0) -> Report:
    report = report or Report(label, "office")
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except Exception as e:  # noqa: BLE001
        report.errors.append(f"not a valid zip: {e}")
        return report
    names = z.namelist()
    kind = office_kind(names)
    report.filetype = f"office/{kind}"
    report.add("info", "structure", f"Office Open XML ({kind}), {len(names)} parts", "", "zip")

    # zip entry timestamps
    stamps = {datetime(*i.date_time) for i in z.infolist() if i.date_time[0] > 1980}
    if stamps:
        report.add("low", "time", "Zip entry timestamps",
                   f"{min(stamps)} .. {max(stamps)} (local time of whoever saved it)", "zip")

    def read(n):
        try:
            return z.read(n)
        except KeyError:
            return b""

    # ---- core / app / custom properties
    root = _xml(read("docProps/core.xml"))
    if root is not None:
        for el in root:
            ln = _local(el)
            if ln in CORE and (el.text or "").strip():
                bucket, sev, lbl = CORE[ln]
                v = el.text.strip()
                report.add(sev, bucket, lbl, v, "docProps/core.xml")
                if bucket == "identity":
                    report.fact("identity", f"{lbl}: {v}")
                if ln in ("created", "modified", "lastPrinted"):
                    report.fact("time", f"{lbl.lower()} {v}")
                scan_strings(report, v, "docProps/core.xml")
    root = _xml(read("docProps/app.xml"))
    if root is not None:
        for el in root:
            ln = _local(el)
            if ln in APP and (el.text or "").strip():
                bucket, sev, lbl = APP[ln]
                v = el.text.strip()
                if ln == "TotalTime" and v == "0":
                    continue
                report.add(sev, bucket, lbl, v, "docProps/app.xml")
                if bucket in ("network", "identity", "software"):
                    report.fact(bucket if bucket != "software" else "software", f"{lbl}: {v}")
                scan_strings(report, v, "docProps/app.xml")
    root = _xml(read("docProps/custom.xml"))
    if root is not None:
        for prop in root:
            name = prop.get("name", "?")
            val = " ".join(t.strip() for t in prop.itertext() if t.strip())
            sev = "medium"
            if name.startswith("MSIP_Label") and name.endswith("SiteId"):
                sev = "high"
                report.fact("network", f"Microsoft 365 tenant ID {val}")
            report.add(sev, "network" if "MSIP" in name else "hidden-content",
                       f"Custom property '{name}'", val[:200], "docProps/custom.xml")
            scan_strings(report, val, "docProps/custom.xml")
    if "docProps/thumbnail.jpeg" in names or "docProps/thumbnail.wmf" in names:
        n = "docProps/thumbnail.jpeg" if "docProps/thumbnail.jpeg" in names else "docProps/thumbnail.wmf"
        report.add("low", "hidden-content", "Saved preview thumbnail",
                   "Preview of first page as it looked when saved (extracted)", n)
        report.artifact(posixpath.basename(n), read(n))

    # ---- relationships: external targets (template paths, linked files, servers)
    for n in names:
        if not n.endswith(".rels"):
            continue
        r = _xml(read(n))
        if r is None:
            continue
        for rel in r:
            if rel.get("TargetMode") == "External":
                tgt = rel.get("Target", "")
                typ = rel.get("Type", "").rsplit("/", 1)[-1]
                if typ == "hyperlink" and tgt.startswith(("http://", "https://", "mailto:")):
                    if "sharepoint.com" in tgt:
                        scan_strings(report, tgt, n, "medium")
                    continue
                report.add("high", "network", f"External {typ} reference", tgt[:300], n)
                scan_strings(report, tgt, n)

    # ---- people / comments
    for n in names:
        base = posixpath.basename(n)
        if base in ("people.xml", "commentAuthors.xml", "authors.xml") or n.startswith("xl/persons/"):
            r = _xml(read(n))
            if r is None:
                continue
            for el in r.iter():
                attrs = {etree.QName(k).localname: v for k, v in el.attrib.items()}
                who = attrs.get("author") or attrs.get("displayName") or attrs.get("name")
                uid = attrs.get("userId")
                if who:
                    report.add("high", "identity", "Collaborator", who + (f" ({uid})" if uid else ""), n)
                    report.fact("identity", f"collaborator {who}" + (f" <{uid}>" if uid and "@" in uid else ""))
                    if uid:
                        scan_strings(report, uid, n)

    comment_parts = [n for n in names if "comment" in posixpath.basename(n).lower()
                     and n.endswith(".xml")]
    for n in comment_parts:
        r = _xml(read(n))
        if r is None:
            continue
        authors = {(a.text or "").strip() for a in r.iter() if _local(a) == "author" and a.text}
        for el in r.iter():
            if _local(el) in ("comment", "cm", "threadedComment"):
                who = el.get(_w("author")) or el.get("author") or el.get("authorId") or ""
                date = el.get(_w("date")) or el.get("dT") or ""
                text = " ".join(t.strip() for t in el.itertext() if t.strip())
                if text:
                    report.add("high", "hidden-content",
                               "Comment" + (f" by {who}" if who and not who.isdigit() else ""),
                               (f"[{date}] " if date else "") + text[:250], n)
                    if who and not who.isdigit():
                        report.fact("identity", f"commenter {who}")
        for a in authors:
            report.fact("identity", f"commenter {a}")
        report.fact("hidden", f"comments in {n}")

    # ---- Word body: tracked changes, hidden text, rsids
    if kind == "docx":
        body_parts = [n for n in names if n.startswith("word/") and n.endswith(".xml")
                      and posixpath.basename(n).startswith(("document", "header", "footer",
                                                            "footnotes", "endnotes"))]
        doc_text = []
        rsids = set()
        for n in body_parts:
            r = _xml(read(n))
            if r is None:
                continue
            deleted, inserted, authors = [], 0, set()
            for el in r.iter():
                ln = _local(el)
                if ln in ("del", "moveFrom") and el.get(_w("author")):
                    authors.add(el.get(_w("author")))
                    txt = "".join(t.text or "" for t in el.iter(_w("delText"), _w("t")))
                    if txt.strip():
                        deleted.append(txt.strip())
                elif ln in ("ins", "moveTo") and el.get(_w("author")):
                    authors.add(el.get(_w("author")))
                    inserted += 1
                elif ln == "t":
                    doc_text.append(el.text or "")
                for k, v in el.attrib.items():
                    if etree.QName(k).localname.startswith("rsid"):
                        rsids.add(v)
            if deleted:
                report.add("critical", "hidden-content", "Tracked DELETED text is recoverable",
                           " | ".join(deleted)[:500], n)
                report.fact("hidden", f"deleted text: \"{' | '.join(deleted)[:100]}\"")
            if inserted:
                report.add("medium", "hidden-content", f"{inserted} tracked insertion(s)", "", n)
            for a in authors:
                report.add("high", "identity", "Tracked-change author", a, n)
                report.fact("identity", f"editor {a}")
            for rr in r.iter(_w("r")):
                rpr = rr.find(_w("rPr"))
                if rpr is not None and rpr.find(_w("vanish")) is not None:
                    t = "".join(x.text or "" for x in rr.iter(_w("t")))
                    if t.strip():
                        report.add("high", "hidden-content", "Hidden (vanished) text", t[:200], n)
                        report.fact("hidden", f"hidden text: \"{t[:60]}\"")
                        continue
                if rpr is not None:
                    col = rpr.find(_w("color"))
                    if col is not None and col.get(_w("val"), "").upper() == "FFFFFF":
                        t = "".join(x.text or "" for x in rr.iter(_w("t")))
                        if t.strip():
                            report.add("medium", "hidden-content", "White text", t[:200], n)
        if rsids:
            report.add("low", "tracking", f"{len(rsids)} revision-session IDs (rsid)",
                       "Fingerprints editing sessions; can link this file to other copies/drafts",
                       "word/*.xml")
        st = _xml(read("word/settings.xml"))
        if st is not None:
            for el in st.iter():
                if _local(el) in ("docVar",):
                    report.add("medium", "hidden-content", f"Document variable '{el.get(_w('name'))}'",
                               (el.get(_w("val")) or "")[:200], "word/settings.xml")
                if _local(el) == "mailMerge":
                    report.add("high", "network", "Mail-merge data source configured", "",
                               "word/settings.xml")
        scan_text_canaries(report, "".join(doc_text), "document text")
        if "word/vbaProject.bin" in names:
            report.add("high", "hidden-content", "VBA macros present", "", "word/vbaProject.bin")

    # ---- Excel specifics
    if kind == "xlsx":
        wb = _xml(read("xl/workbook.xml"))
        if wb is not None:
            for el in wb.iter():
                ln = _local(el)
                if ln == "sheet" and el.get("state") in ("hidden", "veryHidden"):
                    report.add("high", "hidden-content", f"{el.get('state')} sheet '{el.get('name')}'",
                               "Data the viewer won't see in the tab bar", "xl/workbook.xml")
                    report.fact("hidden", f"{el.get('state')} sheet '{el.get('name')}'")
                if ln == "absPath":
                    report.add("high", "identity", "Absolute save path", el.get("url", ""),
                               "xl/workbook.xml")
                    scan_strings(report, el.get("url", ""), "xl/workbook.xml")
                if ln == "definedName" and el.get("hidden") == "1":
                    report.add("low", "hidden-content", f"Hidden defined name '{el.get('name')}'",
                               (el.text or "")[:200], "xl/workbook.xml")
        for n in names:
            if n.startswith("xl/worksheets/") and n.endswith(".xml"):
                r = _xml(read(n))
                if r is None:
                    continue
                hr = sum(1 for e in r.iter(f"{{{SS}}}row") if e.get("hidden") == "1")
                hc = sum(1 for e in r.iter(f"{{{SS}}}col") if e.get("hidden") == "1")
                if hr or hc:
                    report.add("medium", "hidden-content", f"Hidden rows/columns",
                               f"{hr} row(s), {hc} column range(s)", n)
            if n in ("xl/connections.xml",) or n.startswith("xl/externalLinks/"):
                data = read(n).decode("utf-8", "replace")
                report.add("high", "network", "External data connection / link",
                           data[:300], n)
                scan_strings(report, data, n)
            if n.startswith("xl/pivotCache/") and "Definition" in n:
                report.add("medium", "hidden-content", "Pivot cache",
                           "Pivot tables cache a copy of their source data, even if the source "
                           "sheet was deleted", n)
        if "xl/vbaProject.bin" in names:
            report.add("high", "hidden-content", "VBA macros present", "", "xl/vbaProject.bin")

    # ---- PowerPoint specifics
    if kind == "pptx":
        notes = []
        for n in names:
            if n.startswith("ppt/notesSlides/") and n.endswith(".xml"):
                r = _xml(read(n))
                if r is not None:
                    t = " ".join(x.text or "" for x in r.iter(f"{{{A}}}t")).strip()
                    if t and not t.isdigit():
                        notes.append(t)
            if n.startswith("ppt/slides/") and n.endswith(".xml"):
                r = _xml(read(n))
                if r is not None and r.get("show") == "0":
                    report.add("high", "hidden-content", "Hidden slide",
                               " ".join(x.text or "" for x in r.iter(f"{{{A}}}t"))[:200], n)
                    report.fact("hidden", f"hidden slide {posixpath.basename(n)}")
        if notes:
            report.add("high", "hidden-content", f"Speaker notes on {len(notes)} slide(s)",
                       " | ".join(notes)[:400], "ppt/notesSlides")
            report.fact("hidden", f"speaker notes on {len(notes)} slides")

    # ---- embedded media + embedded documents
    for n in names:
        if "/media/" in n and n.lower().endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff",
                                                   ".webp", ".heic")):
            sub = scan_image(read(n), label=n)
            sub.findings = [f for f in sub.findings
                            if f.severity in ("critical", "high", "medium")]
            if sub.findings:
                report.merge(sub, n)
        elif "/embeddings/" in n:
            data = read(n)
            report.add("medium", "hidden-content", "Embedded object",
                       f"{n} ({len(data)} bytes, extracted) - full file inside the document", n)
            report.artifact(posixpath.basename(n), data)
            if data[:2] == b"PK" and depth < 2:
                sub = scan_office(data, label=n, depth=depth + 1)
                report.merge(sub, n)
    return report


# ====================================================================== sanitize
class Package:
    def __init__(self, raw):
        z = zipfile.ZipFile(io.BytesIO(raw))
        self.order = z.namelist()
        self.parts = {n: z.read(n) for n in self.order}

    @staticmethod
    def rels_for(part):
        d, b = posixpath.split(part)
        return posixpath.join(d, "_rels", b + ".rels")

    @staticmethod
    def resolve(rels_path, target):
        if target.startswith("/"):
            return target[1:]
        src_dir = posixpath.dirname(posixpath.dirname(rels_path))
        return posixpath.normpath(posixpath.join(src_dir, target))

    def remove(self, part):
        if part not in self.parts:
            return
        del self.parts[part]
        self.parts.pop(self.rels_for(part), None)
        ct = _xml(self.parts["[Content_Types].xml"])
        for o in list(ct):
            if o.get("PartName") == "/" + part:
                ct.remove(o)
        self.parts["[Content_Types].xml"] = etree.tostring(ct, xml_declaration=True,
                                                           encoding="UTF-8", standalone=True)
        for rn in [n for n in self.parts if n.endswith(".rels")]:
            r = _xml(self.parts[rn])
            changed = False
            for rel in list(r):
                if rel.get("TargetMode") != "External" and \
                        self.resolve(rn, rel.get("Target", "")) == part:
                    r.remove(rel)
                    changed = True
            if changed:
                self.parts[rn] = etree.tostring(r, xml_declaration=True, encoding="UTF-8",
                                                standalone=True)

    def save(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            names = [n for n in self.order if n in self.parts]
            names = ["[Content_Types].xml"] + [n for n in names if n != "[Content_Types].xml"]
            for n in names:
                zi = zipfile.ZipInfo(n, date_time=(1980, 1, 1, 0, 0, 0))  # no timestamp leak
                zi.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(zi, self.parts[n])
        return buf.getvalue()


def _ser(root):
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _unwrap(el):
    parent = el.getparent()
    idx = parent.index(el)
    for child in list(el):
        parent.insert(idx, child)
        idx += 1
    parent.remove(el)


def _clean_word_xml(root):
    # accept all tracked changes: drop deletions, keep insertions
    for tag in ("del", "moveFrom"):
        for el in list(root.iter(_w(tag))):
            if el.getparent() is not None:
                el.getparent().remove(el)
    for tag in ("ins", "moveTo"):
        for el in list(root.iter(_w(tag))):
            if el.getparent() is None:
                continue
            if len(el) == 0 and el.getparent().tag == _w("rPr"):
                el.getparent().remove(el)  # paragraph-mark insertion marker
            else:
                _unwrap(el)
    for tag in ("rPrChange", "pPrChange", "sectPrChange", "tblPrChange", "trPrChange",
                "tcPrChange", "tblGridChange", "numberingChange", "moveFromRangeStart",
                "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd",
                "commentRangeStart", "commentRangeEnd", "commentReference",
                "proofErr", "permStart", "permEnd"):
        for el in list(root.iter(_w(tag))):
            if el.getparent() is not None:
                el.getparent().remove(el)
    # hidden (vanish) runs
    for r in list(root.iter(_w("r"))):
        rpr = r.find(_w("rPr"))
        if rpr is not None and rpr.find(_w("vanish")) is not None and r.getparent() is not None:
            r.getparent().remove(r)
    # rsid session fingerprints + editor-identity attributes
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for k in list(el.attrib):
            ln = etree.QName(k).localname
            if ln.startswith("rsid") or ln in ("paraId", "textId") or \
                    (ln in ("author", "date", "initials") and el.tag != _w("comment")):
                del el.attrib[k]
    # text canaries
    for t in root.iter(_w("t")):
        if t.text:
            t.text = clean_text(t.text)


def sanitize_office(raw: bytes, depth=0) -> bytes:
    pkg = Package(raw)
    names = list(pkg.parts)
    kind = office_kind(names)

    # properties: replace core.xml with an empty shell
    if "docProps/core.xml" in pkg.parts:
        pkg.parts["docProps/core.xml"] = (
            b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            b'<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            b'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            b'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
            b'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"/>')
    app = _xml(pkg.parts.get("docProps/app.xml", b""))
    if app is not None:
        for el in list(app):
            if _local(el) in ("Company", "Manager", "Template", "TotalTime", "HyperlinkBase",
                              "AppVersion", "DocSecurity", "Application"):
                app.remove(el)
        pkg.parts["docProps/app.xml"] = _ser(app)
    for part in ("docProps/custom.xml", "docProps/thumbnail.jpeg", "docProps/thumbnail.wmf",
                 "docProps/thumbnail.emf"):
        pkg.remove(part)
    # customXml holds SharePoint / DLP / sensitivity-label ids
    for n in [n for n in pkg.parts if n.startswith("customXml/") and not n.endswith(".rels")]:
        pkg.remove(n)

    # comments, people, authors
    for n in list(pkg.parts):
        b = posixpath.basename(n).lower()
        if n.endswith(".xml") and ("comment" in b or b in ("people.xml", "commentauthors.xml",
                                                            "authors.xml")
                                   or n.startswith("xl/persons/") or n.startswith("xl/threadedComments/")):
            pkg.remove(n)

    # external relationships that leak paths (template, data sources, OLE links)
    for rn in [n for n in pkg.parts if n.endswith(".rels")]:
        r = _xml(pkg.parts[rn])
        changed = False
        for rel in list(r):
            typ = rel.get("Type", "").rsplit("/", 1)[-1]
            if rel.get("TargetMode") == "External" and typ in (
                    "attachedTemplate", "mailMergeSource", "mailMergeHeaderSource",
                    "externalLinkPath", "frame", "oleObject", "subDocument"):
                rel.set("Target", "about:blank")
                changed = True
        if changed:
            pkg.parts[rn] = _ser(r)

    if kind == "docx":
        for n in list(pkg.parts):
            if n.startswith("word/") and n.endswith(".xml") and not n.endswith(".rels"):
                r = _xml(pkg.parts[n])
                if r is None:
                    continue
                _clean_word_xml(r)
                if n == "word/settings.xml":
                    for el in list(r):
                        if _local(el) in ("rsids", "attachedTemplate", "mailMerge", "docVars",
                                          "documentProtection", "trackRevisions"):
                            r.remove(el)
                pkg.parts[n] = _ser(r)

    if kind == "xlsx":
        wb = _xml(pkg.parts.get("xl/workbook.xml", b""))
        if wb is not None:
            for el in list(wb.iter()):
                if _local(el) in ("absPath", "revisionPtr", "fileSharing") and el.getparent() is not None:
                    p = el.getparent()
                    # absPath lives in <mc:AlternateContent><mc:Choice>; remove the whole wrapper
                    while p is not None and _local(p) in ("Choice", "AlternateContent"):
                        el, p = p, p.getparent()
                    if p is not None:
                        p.remove(el)
            for sh in wb.iter(f"{{{SS}}}sheet"):  # surface hidden sheets instead of shipping them
                sh.attrib.pop("state", None)
            pkg.parts["xl/workbook.xml"] = _ser(wb)
        for n in list(pkg.parts):
            if n.startswith("xl/worksheets/") and n.endswith(".xml"):
                r = _xml(pkg.parts[n])
                for el in r.iter(f"{{{SS}}}row", f"{{{SS}}}col"):
                    el.attrib.pop("hidden", None)
                for el in list(r.iter(f"{{{SS}}}legacyDrawing")):
                    # comments' VML; only remove if its target VML is gone/comment-only
                    el.getparent().remove(el)
                pkg.parts[n] = _ser(r)
        for n in list(pkg.parts):
            if n.startswith("xl/drawings/") and n.endswith(".vml"):
                pkg.remove(n)

    if kind == "pptx":
        for n in list(pkg.parts):
            if n.startswith("ppt/notesSlides/") and n.endswith(".xml"):
                pkg.remove(n)
        for n in list(pkg.parts):
            if n.startswith("ppt/slides/") and n.endswith(".xml"):
                r = _xml(pkg.parts[n])
                if r is not None and r.get("show") == "0":
                    r.attrib.pop("show")  # unhide so nothing ships invisibly
                    pkg.parts[n] = _ser(r)

    # images and embedded docs
    for n in list(pkg.parts):
        low = n.lower()
        if "/media/" in n and low.endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp")):
            try:
                clean, _ = sanitize_image_bytes(pkg.parts[n])
                pkg.parts[n] = clean
            except Exception:  # noqa: BLE001
                pass
        elif "/embeddings/" in n and pkg.parts[n][:2] == b"PK" and depth < 2:
            try:
                pkg.parts[n] = sanitize_office(pkg.parts[n], depth + 1)
            except Exception:  # noqa: BLE001
                pass
    return pkg.save()
