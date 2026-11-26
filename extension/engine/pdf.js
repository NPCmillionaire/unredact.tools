// PDFs: metadata, fake redactions (text under boxes), hidden text, earlier revisions,
// attachments, comments, embedded photo EXIF. Cleaning rebuilds or flattens the file.
import * as pdfjs from "../vendor/pdf.min.mjs";
import { PDFDocument, PDFName, PDFRawStream, PDFNumber, PDFDict, PDFArray, PDFStream } from "../vendor/pdf-lib.esm.min.js";
import { scanStrings, scanTextCanaries, latin1, ascii } from "./common.js";
import { Report } from "./report.js";
import { scanImage, cleanImage } from "./image.js";

pdfjs.GlobalWorkerOptions.workerSrc = new URL("../vendor/pdf.worker.min.mjs", import.meta.url).href;
const FONTS = new URL("../vendor/standard_fonts/", import.meta.url).href;
const MAX_RENDER_PAGES = 40;
const FLATTEN_TAGS = new Set(["Fake redaction", "Hidden text", "Unapplied redaction"]);

const open = (u8, extra = {}) =>
  pdfjs.getDocument({ data: u8.slice(), standardFontDataUrl: FONTS, isEvalSupported: false, verbosity: 0, ...extra }).promise;

const pdfDate = (s) => {
  const m = /D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?([Z+-])?(\d{2})?'?(\d{2})?/.exec(s || "");
  if (!m) return s;
  return `${m[1]}-${m[2] || "01"}-${m[3] || "01"} ${m[4] || "00"}:${m[5] || "00"}${m[7] && m[7] !== "Z" ? ` ${m[7]}${m[8]}:${m[9] || "00"}` : m[7] === "Z" ? " UTC" : ""}`;
};

export async function scanPdf(report, u8) {
  report.filetype = "pdf";
  let doc;
  try {
    doc = await open(u8);
  } catch (e) {
    if (e?.name === "PasswordException") {
      report.add("info", "hidden-content", "Password-protected PDF", "Can't inspect without the password.", "PDF", "Encrypted");
    } else report.errors.push(`couldn't open PDF: ${e.message || e}`);
    return report;
  }
  report.pages = doc.numPages;

  // ---- document metadata
  const { info = {}, metadata } = await doc.getMetadata().catch(() => ({}));
  const meta = (k, sev, cat, label, tag) => {
    const v = info[k];
    if (v && typeof v === "string" && v.trim()) {
      report.add(sev, cat, label, v.trim(), "PDF info", tag);
      scanStrings(report, v, `PDF ${k}`);
      return v.trim();
    }
  };
  const author = meta("Author", "high", "identity", "Author", "Author");
  if (author) report.fact("identity", author);
  meta("Title", "medium", "hidden-content", "Title", "Title");
  meta("Subject", "medium", "hidden-content", "Subject", "Subject");
  meta("Keywords", "medium", "hidden-content", "Keywords", "Keywords");
  for (const k of ["Creator", "Producer"]) {
    const v = meta(k, "low", "software", `${k} app`, "Software");
    if (v) report.fact("software", v);
  }
  for (const k of ["CreationDate", "ModDate"]) {
    if (info[k]) {
      report.add(k === "CreationDate" && /[+-]\d{2}'?\d{2}/.test(info[k]) ? "medium" : "low", "time",
        k === "CreationDate" ? "Created" : "Modified", pdfDate(info[k]), "PDF info", "Timestamp");
      report.fact("time", pdfDate(info[k]));
    }
  }
  for (const [k, v] of Object.entries(info.Custom || {})) {
    report.add("medium", "hidden-content", `Custom property: ${k}`, String(v?.name ?? v).slice(0, 200), "PDF info", "Custom property");
  }
  if (metadata) {
    const all = metadata.getAll?.() || {};
    for (const [k, v] of Object.entries(all)) {
      const val = Array.isArray(v) ? v.join(", ") : String(v);
      if (/creator$|author/i.test(k) && val) { report.add("high", "identity", `XMP ${k}`, val, "PDF XMP", "Author"); report.fact("identity", val); }
      if (/documentid|instanceid/i.test(k)) report.add("medium", "tracking", `XMP ${k}`, val, "PDF XMP", "Document IDs");
      scanStrings(report, val, `PDF XMP ${k}`);
    }
  }

  // ---- attachments
  const atts = await doc.getAttachments().catch(() => null);
  for (const a of Object.values(atts || {})) {
    report.add("high", "hidden-content", "Embedded file attachment", `${a.filename} (${a.content?.length ?? "?"} bytes)`, "PDF", "Attachment");
    report.fact("hidden", `attached file ${a.filename}`);
    if (a.content) report.artifact(a.filename, a.content, "application/octet-stream", "PDF attachment");
  }

  // ---- JavaScript / auto actions
  try {
    const js = await doc.getJSActions();
    if (js && Object.keys(js).length) report.add("medium", "tracking", "Embedded JavaScript", Object.keys(js).join(", "), "PDF", "JavaScript");
  } catch { /* none */ }

  // ---- per page: text, annotations, fake redactions
  const allText = [];
  let flagged = 0;
  for (let n = 1; n <= doc.numPages; n++) {
    const page = await doc.getPage(n);
    const tc = await page.getTextContent();
    allText.push(tc.items.map((i) => i.str).join(" "));
    for (const a of await page.getAnnotations().catch(() => [])) {
      const body = a.contentsObj?.str || a.contents || "";
      const by = a.titleObj?.str || a.title || "";
      if (a.subtype === "Redact") {
        report.add("critical", "hidden-content", "Unapplied redaction annotation", `Page ${n}: marked for redaction but never applied, the text underneath is still there.`, `PDF page ${n}`, "Unapplied redaction");
      } else if (a.subtype === "FileAttachment") {
        report.add("high", "hidden-content", "Attached file in a comment", a.file?.filename || "", `PDF page ${n}`, "Attachment");
      } else if (a.subtype === "Popup") {
        continue;
      } else if (body || (by && a.subtype !== "Link" && a.subtype !== "Widget")) {
        report.add(by ? "high" : "medium", by ? "identity" : "hidden-content", `Comment (${a.subtype})`, `${by ? by + ": " : ""}${body}`.slice(0, 300), `PDF page ${n}`, "Comment");
        if (by) report.fact("identity", by);
      }
      if (a.subtype === "Widget" && a.fieldValue && typeof a.fieldValue === "string") {
        report.add("medium", "hidden-content", "Filled form field", `${a.fieldName}: ${a.fieldValue}`.slice(0, 200), `PDF page ${n}`, "Form data");
      }
      if (a.url && /^file:|\\\\/i.test(a.url)) scanStrings(report, a.url, `PDF page ${n} link`);
    }
    if (n <= MAX_RENDER_PAGES) flagged += await hiddenText(report, page, tc, n);
  }
  const text = allText.join("\n");
  scanTextCanaries(report, text, "PDF text");
  if (!text.trim() && doc.numPages) report.fact("software", "image-only PDF (no text layer)");

  // ---- earlier revisions (incremental saves keep every previous version)
  await revisions(report, u8, text);

  // ---- photos embedded in the PDF keep their own EXIF
  await embeddedImages(report, u8);

  await doc.destroy();
  report.cleanable = true;
  report.cleanNote = report.findings.some((f) => FLATTEN_TAGS.has(f.tag))
    ? "Pages will be flattened to images so covered text is really gone (text won't be selectable)."
    : "Rebuilt from visible pages only: metadata, earlier versions, attachments, comments and scripts removed.";
  return report;
}

// Render the page and check whether each text run is actually visible. Text sitting under an
// opaque box (or drawn invisibly) leaves a perfectly uniform patch where the glyphs should be.
async function hiddenText(report, page, tc, n) {
  const vp = page.getViewport({ scale: 1.5 });
  const canvas = document.createElement("canvas");
  canvas.width = Math.ceil(vp.width);
  canvas.height = Math.ceil(vp.height);
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  try {
    await page.render({ canvasContext: ctx, viewport: vp, background: "rgb(255,255,255)" }).promise;
  } catch { return 0; }
  const { data, width: W, height: H } = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const lum = (x, y) => { const i = (y * W + x) * 4; return 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2]; };

  const cell = (x0, x1, y0, y1) => {
    x0 = Math.max(0, Math.floor(x0)); x1 = Math.min(W - 1, Math.ceil(x1));
    y0 = Math.max(0, Math.floor(y0)); y1 = Math.min(H - 1, Math.ceil(y1));
    if (x1 <= x0 || y1 <= y0) return null;
    const v = [];
    for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) v.push(lum(x, y));
    v.sort((a, b) => a - b);
    const med = v[v.length >> 1];
    const off = v.filter((l) => Math.abs(l - med) > 40).length / v.length;
    return { med, uniform: off < 0.015 };
  };

  let count = 0;
  for (const it of tc.items) {
    const s = it.str;
    if (!s || !s.trim()) continue;
    const t = pdfjs.Util.transform(vp.transform, it.transform);
    if (Math.abs(t[1]) > Math.abs(t[0]) * 0.05) continue; // rotated text: skip
    const fh = Math.hypot(t[2], t[3]);
    const w = it.width * vp.scale;
    if (fh < 4 || w < 2) continue;
    const cw = w / s.length;
    const covered = [];
    for (let i = 0; i < s.length; i++) {
      const ch = s[i];
      if (!/[\p{L}\p{N}]/u.test(ch)) { covered.push(null); continue; }
      const c = cell(t[4] + i * cw + cw * 0.1, t[4] + (i + 1) * cw - cw * 0.1, t[5] - fh * 0.7, t[5] - fh * 0.05);
      covered.push(c && c.uniform ? c.med : false);
    }
    // group runs of covered characters (punctuation/spaces inside a run don't break it)
    let run = [], lums = [];
    const flush = () => {
      const txt = run.join("").trim();
      if ((txt.match(/[\p{L}\p{N}]/gu) || []).length >= 3) {
        const m = lums.reduce((a, b) => a + b, 0) / lums.length;
        if (m < 200) {
          report.add("critical", "hidden-content", "Text under a redaction box", `Page ${n}: "${txt.slice(0, 160)}" is still in the file under a ${m < 80 ? "black" : "solid"} box. Anyone can copy it out.`, `PDF page ${n}`, "Fake redaction");
          report.fact("hidden", `text under black box: "${txt.slice(0, 60)}"`);
        } else {
          report.add("high", "hidden-content", "Invisible text", `Page ${n}: "${txt.slice(0, 160)}" is in the file but not visible (white or hidden text).`, `PDF page ${n}`, "Hidden text");
          report.fact("hidden", `invisible text: "${txt.slice(0, 60)}"`);
        }
        count++;
      }
      run = []; lums = [];
    };
    for (let i = 0; i < s.length; i++) {
      if (covered[i] === false) flush();
      else if (covered[i] === null) { if (run.length) run.push(s[i]); }
      else { run.push(s[i]); lums.push(covered[i]); }
    }
    flush();
  }
  canvas.width = canvas.height = 0;
  return count;
}

async function revisions(report, u8, finalText) {
  const s = latin1(u8);
  const eofs = [];
  for (let i = s.indexOf("%%EOF"); i !== -1; i = s.indexOf("%%EOF", i + 5)) eofs.push(i + 5);
  const linearized = /\/Linearized/.test(s.slice(0, 2048));
  const earlier = eofs.slice(0, -1).filter((_, i) => !(linearized && i === 0));
  if (!earlier.length) return;
  const norm = (t) => t.replace(/\s+/g, " ").trim();
  const final = norm(finalText);
  const removed = new Set();
  let loaded = 0;
  for (const end of earlier.slice(-5)) {
    try {
      const d = await open(u8.subarray(0, end));
      loaded++;
      for (let n = 1; n <= Math.min(d.numPages, 30); n++) {
        const tc = await (await d.getPage(n)).getTextContent();
        for (const it of tc.items) {
          const t = norm(it.str);
          if (t.length >= 4 && !final.includes(t)) removed.add(t);
        }
      }
      await d.destroy();
    } catch { /* not a loadable revision */ }
  }
  if (!loaded) return;
  report.add("high", "hidden-content", `${loaded} earlier version${loaded > 1 ? "s" : ""} saved inside the file`,
    "The PDF was edited with incremental saves; each earlier version can be restored.", "PDF revisions", "Earlier versions");
  report.fact("hidden", `${loaded} recoverable earlier version(s)`);
  if (removed.size) {
    const list = [...removed].slice(0, 6).map((t) => `"${t.slice(0, 80)}"`).join(", ");
    report.add("critical", "hidden-content", "Deleted text recoverable from an earlier version", list, "PDF revisions", "Deleted text");
    report.fact("hidden", `deleted text: ${list.slice(0, 120)}`);
  }
}

function dctStreams(ctx) {
  const out = [];
  for (const [ref, obj] of ctx.enumerateIndirectObjects()) {
    if (!(obj instanceof PDFRawStream)) continue;
    const f = obj.dict.get(PDFName.of("Filter"));
    const isDct = f === PDFName.of("DCTDecode") || (f instanceof PDFArray && f.size() === 1 && f.get(0) === PDFName.of("DCTDecode"));
    if (isDct && obj.dict.get(PDFName.of("Subtype")) === PDFName.of("Image")) out.push([ref, obj]);
  }
  return out;
}

async function embeddedImages(report, u8) {
  let src;
  try { src = await PDFDocument.load(u8, { ignoreEncryption: true, updateMetadata: false, throwOnInvalidObject: false }); } catch { return; }
  if (src.isEncrypted) return;
  let i = 0;
  for (const [, obj] of dctStreams(src.context).slice(0, 40)) {
    i++;
    const sub = new Report(`image ${i}`, "jpeg", obj.contents.length);
    await scanImage(sub, obj.contents, `embedded image ${i}`);
    const keep = sub.findings.filter((f) => f.category !== "hidden-content" || f.tag !== "Thumbnail");
    if (keep.length) { sub.findings = keep; report.merge(sub, `PDF embedded image ${i}`); }
  }
}

// ------------------------------------------------------------------ clean
export async function cleanPdf(u8, report) {
  const flatten = report?.findings?.some((f) => FLATTEN_TAGS.has(f.tag));
  let src = null;
  try { src = await PDFDocument.load(u8, { ignoreEncryption: true, updateMetadata: false }); } catch { /* fall back */ }
  if (flatten || !src || src.isEncrypted) return { bytes: await rasterize(u8), mime: "application/pdf", note: "flattened" };

  const out = await PDFDocument.create({ updateMetadata: false });
  const pages = await out.copyPages(src, src.getPageIndices());
  for (const p of pages) {
    const node = p.node;
    for (const k of ["Metadata", "PieceInfo", "Thumb", "LastModified", "AA"]) node.delete(PDFName.of(k));
    const annots = node.lookup(PDFName.of("Annots"));
    if (annots instanceof PDFArray) {
      const keep = PDFArray.withContext(out.context);
      for (let i = 0; i < annots.size(); i++) {
        const a = annots.lookup(i);
        const act = a instanceof PDFDict ? a.lookup(PDFName.of("A")) : null;
        const isUriLink = a instanceof PDFDict && a.get(PDFName.of("Subtype")) === PDFName.of("Link") &&
          (!act || (act instanceof PDFDict && act.get(PDFName.of("S")) === PDFName.of("URI")));
        if (isUriLink) keep.push(annots.get(i));
      }
      if (keep.size()) node.set(PDFName.of("Annots"), keep); else node.delete(PDFName.of("Annots"));
    }
    out.addPage(p);
  }
  // scrub per-object XMP / app data and EXIF inside embedded photos
  for (const [, obj] of out.context.enumerateIndirectObjects()) {
    const d = obj instanceof PDFDict ? obj : obj instanceof PDFStream ? obj.dict : null;
    if (d && d.get(PDFName.of("Type")) !== PDFName.of("Catalog")) { d.delete(PDFName.of("Metadata")); d.delete(PDFName.of("PieceInfo")); }
  }
  for (const [ref, obj] of dctStreams(out.context)) {
    const c = await cleanImage(obj.contents);
    if (c && c.bytes.length !== obj.contents.length) {
      obj.dict.set(PDFName.of("Length"), PDFNumber.of(c.bytes.length));
      out.context.assign(ref, PDFRawStream.of(obj.dict, c.bytes));
    }
  }
  out.context.trailerInfo.Info = undefined;
  out.context.trailerInfo.ID = undefined;
  return { bytes: await out.save({ useObjectStreams: true }), mime: "application/pdf", note: "rebuilt" };
}

async function rasterize(u8) {
  const doc = await open(u8);
  const out = await PDFDocument.create({ updateMetadata: false });
  for (let n = 1; n <= doc.numPages; n++) {
    const page = await doc.getPage(n);
    const base = page.getViewport({ scale: 1 });
    const vp = page.getViewport({ scale: 2 });
    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil(vp.width);
    canvas.height = Math.ceil(vp.height);
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    await page.render({ canvasContext: ctx, viewport: vp }).promise;
    const blob = await new Promise((r) => canvas.toBlob(r, "image/jpeg", 0.9));
    const img = await out.embedJpg(new Uint8Array(await blob.arrayBuffer()));
    const p = out.addPage([base.width, base.height]);
    p.drawImage(img, { x: 0, y: 0, width: base.width, height: base.height });
    canvas.width = canvas.height = 0;
  }
  await doc.destroy();
  out.context.trailerInfo.Info = undefined;
  return out.save({ useObjectStreams: true });
}

export { ascii };
