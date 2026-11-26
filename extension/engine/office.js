// Office Open XML (docx / xlsx / pptx): properties, comments, tracked changes, hidden text,
// hidden sheets, speaker notes, embedded photos. Cleaning edits the XML parts in place.
import "../vendor/jszip.min.js"; // UMD: sets globalThis.JSZip
import { scanStrings, scanTextCanaries, cleanText } from "./common.js";
import { Report } from "./report.js";
import { scanImage, cleanImage, imageKind } from "./image.js";

const JSZip = globalThis.JSZip;
const W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
const parseXml = (s) => new DOMParser().parseFromString(s, "application/xml");
const ser = (d) => new XMLSerializer().serializeToString(d);
const byLocal = (doc, name) => [...doc.getElementsByTagNameNS("*", name)];
const txt = (el) => (el?.textContent || "").trim();

export const isOoxml = (u8) => u8[0] === 0x50 && u8[1] === 0x4b;

export function officeKind(zip) {
  if (zip.file("word/document.xml")) return "docx";
  if (zip.file("xl/workbook.xml")) return "xlsx";
  if (zip.file("ppt/presentation.xml")) return "pptx";
  return null;
}

const read = async (zip, p) => (zip.file(p) ? zip.file(p).async("string") : null);
const files = (zip, rx) => Object.keys(zip.files).filter((n) => rx.test(n) && !zip.files[n].dir);

export async function scanOffice(report, u8) {
  let zip;
  try { zip = await JSZip.loadAsync(u8); } catch (e) { report.errors.push(`not a readable zip: ${e.message || e}`); return report; }
  const kind = officeKind(zip);
  if (!kind) return report;
  report.filetype = kind;

  // ---- core / app / custom properties
  const core = await read(zip, "docProps/core.xml");
  if (core) {
    const d = parseXml(core);
    const p = (n) => txt(byLocal(d, n)[0]);
    const add = (n, sev, cat, label, tag) => {
      const v = p(n);
      if (v) { report.add(sev, cat, label, v, "docProps/core.xml", tag); scanStrings(report, v, `core ${n}`); }
      return v;
    };
    const a = add("creator", "high", "identity", "Author", "Author");
    const l = add("lastModifiedBy", "high", "identity", "Last modified by", "Author");
    [a, l].filter(Boolean).forEach((v) => report.fact("identity", v));
    add("description", "medium", "hidden-content", "Comments property", "Properties");
    add("title", "medium", "hidden-content", "Title", "Properties");
    add("subject", "low", "hidden-content", "Subject", "Properties");
    add("keywords", "low", "hidden-content", "Keywords", "Properties");
    add("category", "low", "hidden-content", "Category", "Properties");
    const rev = p("revision");
    if (rev && Number(rev) > 1) report.add("low", "software", "Revision number", `saved ${rev} times`, "docProps/core.xml", "Revision count");
    for (const n of ["created", "modified"]) {
      const v = p(n);
      if (v) { report.add("low", "time", n === "created" ? "Created" : "Modified", v, "docProps/core.xml", "Timestamp"); report.fact("time", v); }
    }
  }
  const app = await read(zip, "docProps/app.xml");
  if (app) {
    const d = parseXml(app);
    const g = (n) => txt(byLocal(d, n)[0]);
    if (g("Company")) { report.add("high", "network", "Company", g("Company"), "docProps/app.xml", "Company"); report.fact("network", g("Company")); }
    if (g("Manager")) { report.add("high", "identity", "Manager", g("Manager"), "docProps/app.xml", "Manager"); report.fact("identity", g("Manager")); }
    if (g("Template") && !/^Normal(\.dotm)?$/i.test(g("Template"))) report.add("medium", "software", "Template", g("Template"), "docProps/app.xml", "Template");
    if (g("HyperlinkBase")) scanStrings(report, g("HyperlinkBase"), "app HyperlinkBase");
    if (Number(g("TotalTime")) > 0) report.add("low", "time", "Total editing time", `${g("TotalTime")} minutes`, "docProps/app.xml", "Edit time");
    const appName = [g("Application"), g("AppVersion")].filter(Boolean).join(" ");
    if (appName) { report.add("low", "software", "Application", appName, "docProps/app.xml", "Software"); report.fact("software", appName); }
  }
  const custom = await read(zip, "docProps/custom.xml");
  if (custom) {
    for (const p of byLocal(parseXml(custom), "property")) {
      const v = txt(p);
      report.add("medium", "hidden-content", `Custom property: ${p.getAttribute("name")}`, v.slice(0, 200), "docProps/custom.xml", "Custom property");
      scanStrings(report, v, "custom property");
    }
  }
  for (const t of files(zip, /^docProps\/thumbnail\./i)) {
    report.add("medium", "hidden-content", "Saved preview thumbnail", "May show content from before the last edit.", t, "Thumbnail");
  }

  // ---- format-specific
  if (kind === "docx") await scanDocx(report, zip);
  if (kind === "xlsx") await scanXlsx(report, zip);
  if (kind === "pptx") await scanPptx(report, zip);

  // ---- every XML part: usernames / internal paths (e.g. attachedTemplate, external links)
  for (const n of files(zip, /\.(xml|rels)$/i).filter((n) => !/^docProps\/core/.test(n)).slice(0, 200)) {
    const s = await read(zip, n);
    const hits = (s.match(/(?:[A-Za-z]:\\\\?Users\\\\?[^\\"<>]+|\/Users\/[\w.-]+|\\\\\\\\[\w.-]+\\\\[^"<>\s]+|file:\/\/\/[^"<>\s]+|[\w-]+(?:-my)?\.sharepoint\.com\/[^"<>\s]*)/gi) || []);
    for (const h of [...new Set(hits)].slice(0, 5)) scanStrings(report, h, n);
  }

  // ---- embedded photos keep their own EXIF (GPS etc.)
  let i = 0;
  for (const m of files(zip, /^(word|xl|ppt)\/media\/[^/]+\.(jpe?g|png|webp|tiff?|heic)$/i).slice(0, 40)) {
    const b = await zip.file(m).async("uint8array");
    const sub = new Report(m, imageKind(b) || "image", b.length);
    await scanImage(sub, b, m);
    if (sub.findings.length) report.merge(sub, `embedded ${m.split("/").pop()}`);
    i++;
  }

  report.cleanable = true;
  const left = [];
  if (report.findings.some((f) => f.tag === "Hidden sheet")) left.push("hidden sheets");
  if (report.findings.some((f) => f.tag === "Hidden rows")) left.push("hidden rows/columns");
  report.cleanNote = "Properties, comments, tracked changes, hidden text, notes and photo EXIF removed." +
    (left.length ? ` ${left.join(" and ")} can't be removed safely here; delete them in the app.` : "");
  return report;
}

async function scanDocx(report, zip) {
  const parts = files(zip, /^word\/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$/);
  let body = "";
  for (const p of parts) {
    const d = parseXml(await read(zip, p));
    body += byLocal(d, "t").map((e) => e.textContent).join("") + "\n";
    const dels = byLocal(d, "del");
    if (dels.length) {
      const deleted = dels.map((e) => byLocal(e, "delText").map((t) => t.textContent).join("")).join(" | ").trim();
      const who = [...new Set(dels.map((e) => e.getAttributeNS(W, "author")).filter(Boolean))];
      report.add("critical", "hidden-content", `${dels.length} tracked deletion${dels.length > 1 ? "s" : ""}`,
        `Deleted text still in the file: "${deleted.slice(0, 200)}"${who.length ? ` (by ${who.join(", ")})` : ""}`, p, "Tracked changes");
      report.fact("hidden", `deleted text: "${deleted.slice(0, 80)}"`);
      who.forEach((w) => report.fact("identity", w));
    }
    const ins = byLocal(d, "ins");
    if (ins.length) {
      const who = [...new Set(ins.map((e) => e.getAttributeNS(W, "author")).filter(Boolean))];
      report.add("high", "identity", `${ins.length} tracked insertion${ins.length > 1 ? "s" : ""}`, who.length ? `by ${who.join(", ")}` : "", p, "Tracked changes");
      who.forEach((w) => report.fact("identity", w));
    }
    const hidden = byLocal(d, "vanish").map((v) => v.closest?.("r") || v.parentNode?.parentNode).filter(Boolean);
    const ht = hidden.map((r) => byLocal(r, "t").map((t) => t.textContent).join("")).join(" ").trim();
    if (ht) { report.add("high", "hidden-content", "Hidden text", `"${ht.slice(0, 200)}"`, p, "Hidden text"); report.fact("hidden", `hidden text "${ht.slice(0, 60)}"`); }
    const rsids = new Set();
    for (const el of d.getElementsByTagName("*")) for (const a of el.attributes) if (/^w:rsid/.test(a.name)) rsids.add(a.value);
    if (rsids.size > 20) report.add("low", "tracking", "Revision session IDs (rsid)", `${rsids.size} editing-session IDs can link this file to other drafts.`, p, "rsid");
  }
  const comments = await read(zip, "word/comments.xml");
  if (comments) {
    const cs = byLocal(parseXml(comments), "comment");
    if (cs.length) {
      const who = [...new Set(cs.map((c) => c.getAttributeNS(W, "author")).filter(Boolean))];
      report.add("high", "identity", `${cs.length} comment${cs.length > 1 ? "s" : ""}`,
        cs.slice(0, 3).map((c) => `${c.getAttributeNS(W, "author") || "?"}: ${txt(c)}`).join(" · ").slice(0, 300), "word/comments.xml", "Comments");
      who.forEach((w) => report.fact("identity", w));
    }
  }
  const settings = await read(zip, "word/settings.xml");
  if (settings) {
    for (const v of byLocal(parseXml(settings), "docVar")) {
      report.add("medium", "hidden-content", `Document variable: ${v.getAttributeNS(W, "name")}`, (v.getAttributeNS(W, "val") || "").slice(0, 200), "word/settings.xml", "Doc variable");
    }
  }
  scanTextCanaries(report, body, "document text");
}

async function scanXlsx(report, zip) {
  const wb = parseXml(await read(zip, "xl/workbook.xml"));
  for (const s of byLocal(wb, "sheet")) {
    const st = s.getAttribute("state");
    if (st === "hidden" || st === "veryHidden") {
      report.add(st === "veryHidden" ? "critical" : "high", "hidden-content", `${st === "veryHidden" ? "Very hidden" : "Hidden"} sheet`, `"${s.getAttribute("name")}"`, "xl/workbook.xml", "Hidden sheet");
      report.fact("hidden", `hidden sheet "${s.getAttribute("name")}"`);
    }
  }
  for (const p of files(zip, /^xl\/worksheets\/sheet\d+\.xml$/)) {
    const s = await read(zip, p);
    const rows = (s.match(/<row [^>]*hidden="(1|true)"/g) || []).length;
    const cols = (s.match(/<col [^>]*hidden="(1|true)"/g) || []).length;
    if (rows || cols) report.add("medium", "hidden-content", "Hidden rows/columns", `${rows} rows, ${cols} column ranges`, p, "Hidden rows");
  }
  const cs = [];
  for (const p of files(zip, /^xl\/(comments\d*|comments\/[^/]+|threadedComments\/[^/]+)\.xml$/)) {
    const d = parseXml(await read(zip, p));
    const authors = byLocal(d, "author").map(txt);
    for (const c of [...byLocal(d, "comment"), ...byLocal(d, "threadedComment")]) cs.push({ a: authors[+c.getAttribute("authorId")] || "", t: txt(c) });
  }
  if (cs.length) {
    report.add("high", "identity", `${cs.length} cell comment${cs.length > 1 ? "s" : ""}`, cs.slice(0, 3).map((c) => `${c.a ? c.a + ": " : ""}${c.t}`).join(" · ").slice(0, 300), "xl/comments", "Comments");
    cs.map((c) => c.a).filter(Boolean).forEach((a) => report.fact("identity", a));
  }
  if (files(zip, /^xl\/externalLinks\//).length) report.add("medium", "network", "Links to other workbooks", "External references can reveal file paths or servers.", "xl/externalLinks", "External links");
  const ss = await read(zip, "xl/sharedStrings.xml");
  if (ss) scanTextCanaries(report, byLocal(parseXml(ss), "t").map((t) => t.textContent).join(" "), "cell text");
}

async function scanPptx(report, zip) {
  const notes = files(zip, /^ppt\/notesSlides\/notesSlide\d+\.xml$/);
  const nt = [];
  for (const p of notes) {
    const t = byLocal(parseXml(await read(zip, p)), "t").map((e) => e.textContent).join(" ").trim();
    if (t && !/^\d+$/.test(t)) nt.push(t);
  }
  if (nt.length) { report.add("high", "hidden-content", `Speaker notes on ${nt.length} slide${nt.length > 1 ? "s" : ""}`, `"${nt[0].slice(0, 200)}"`, "ppt/notesSlides", "Speaker notes"); report.fact("hidden", `speaker notes: "${nt[0].slice(0, 60)}"`); }
  const cm = files(zip, /^ppt\/comments\/.+\.xml$/);
  if (cm.length) report.add("high", "identity", "Slide comments", `${cm.length} comment file(s)`, "ppt/comments", "Comments");
  const authors = await read(zip, "ppt/commentAuthors.xml");
  if (authors) for (const a of byLocal(parseXml(authors), "cmAuthor")) report.fact("identity", a.getAttribute("name"));
  for (const p of files(zip, /^ppt\/slides\/slide\d+\.xml$/)) {
    const s = await read(zip, p);
    if (/<p:sld [^>]*show="(0|false)"/.test(s)) report.add("high", "hidden-content", "Hidden slide", p, p, "Hidden slide");
  }
}

// ------------------------------------------------------------------ clean
const CT = "[Content_Types].xml";

async function removePart(zip, path) {
  zip.remove(path);
  const ct = await read(zip, CT);
  if (ct) {
    const d = parseXml(ct);
    for (const o of byLocal(d, "Override")) if (o.getAttribute("PartName") === "/" + path) o.remove();
    zip.file(CT, ser(d));
  }
  const removedIds = [];
  for (const r of files(zip, /_rels\/[^/]*\.rels$/)) {
    const base = r.replace(/_rels\/[^/]*\.rels$/, "");
    const d = parseXml(await read(zip, r));
    let changed = false;
    for (const rel of byLocal(d, "Relationship")) {
      if (rel.getAttribute("TargetMode") === "External") continue;
      const t = rel.getAttribute("Target");
      const abs = t.startsWith("/") ? t.slice(1) : resolve(base, t);
      if (abs === path) {
        removedIds.push({ rels: r, id: rel.getAttribute("Id"), source: r.replace(/_rels\/([^/]*)\.rels$/, "$1") });
        rel.remove(); changed = true;
      }
    }
    if (changed) zip.file(r, ser(d));
  }
  return removedIds;
}

const resolve = (base, rel) => {
  const out = base.split("/").filter(Boolean);
  for (const seg of rel.split("/")) { if (seg === "..") out.pop(); else if (seg && seg !== ".") out.push(seg); }
  return out.join("/");
};

function unwrap(el) {
  while (el.firstChild) el.parentNode.insertBefore(el.firstChild, el);
  el.remove();
}

function cleanWordXml(d) {
  for (const t of [...byLocal(d, "del"), ...byLocal(d, "moveFrom")]) t.remove();
  for (const t of [...byLocal(d, "ins"), ...byLocal(d, "moveTo")]) unwrap(t);
  for (const t of [...byLocal(d, "rPrChange"), ...byLocal(d, "pPrChange"), ...byLocal(d, "sectPrChange"), ...byLocal(d, "tblPrChange"),
    ...byLocal(d, "trPrChange"), ...byLocal(d, "tcPrChange"), ...byLocal(d, "numberingChange"),
    ...byLocal(d, "moveFromRangeStart"), ...byLocal(d, "moveFromRangeEnd"), ...byLocal(d, "moveToRangeStart"), ...byLocal(d, "moveToRangeEnd"),
    ...byLocal(d, "commentRangeStart"), ...byLocal(d, "commentRangeEnd")]) t.remove();
  for (const ref of byLocal(d, "commentReference")) (ref.parentNode?.localName === "r" ? ref.parentNode : ref).remove();
  for (const v of byLocal(d, "vanish")) {
    const rPr = v.parentNode;
    const r = rPr?.parentNode;
    if (r?.localName === "r") r.remove(); else if (rPr?.parentNode?.localName === "pPr") v.remove();
  }
  for (const el of d.getElementsByTagName("*")) {
    for (const a of [...el.attributes]) if (/^w:rsid/.test(a.name)) el.removeAttributeNode(a);
  }
  for (const t of byLocal(d, "t")) t.textContent = cleanText(t.textContent);
}

export async function cleanOffice(u8) {
  const zip = await JSZip.loadAsync(u8);
  const kind = officeKind(zip);

  // properties
  if (zip.file("docProps/core.xml")) {
    zip.file("docProps/core.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"></cp:coreProperties>');
  }
  if (zip.file("docProps/app.xml")) {
    const d = parseXml(await read(zip, "docProps/app.xml"));
    for (const n of ["Company", "Manager", "Template", "TotalTime", "HyperlinkBase", "AppVersion", "Application"]) byLocal(d, n).forEach((e) => e.remove());
    zip.file("docProps/app.xml", ser(d));
  }
  for (const p of [...files(zip, /^docProps\/(custom\.xml|thumbnail\.\w+)$/i)]) await removePart(zip, p);

  if (kind === "docx") {
    for (const p of files(zip, /^word\/(comments\w*|people)\.xml$/)) await removePart(zip, p);
    for (const p of files(zip, /^word\/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$/)) {
      const d = parseXml(await read(zip, p));
      cleanWordXml(d);
      zip.file(p, ser(d));
    }
    const sp = "word/settings.xml";
    if (zip.file(sp)) {
      const d = parseXml(await read(zip, sp));
      for (const n of ["rsids", "docVars", "attachedTemplate", "trackRevisions"]) byLocal(d, n).forEach((e) => e.remove());
      zip.file(sp, ser(d));
    }
  }
  if (kind === "xlsx") {
    for (const p of files(zip, /^xl\/(comments\d*\.xml|comments\/.+\.xml|threadedComments\/.+\.xml|persons\/.+\.xml)$/)) {
      for (const r of await removePart(zip, p)) {
        // drop the sheet's <legacyDrawing> (comment boxes) that pointed at the removed parts
        const src = r.source;
        if (!zip.file(src)) continue;
        const d = parseXml(await read(zip, src));
        for (const el of [...d.getElementsByTagName("*")]) if ([...el.attributes].some((a) => a.localName === "id" && a.value === r.id)) el.remove();
        zip.file(src, ser(d));
      }
    }
    for (const p of files(zip, /^xl\/worksheets\/_rels\/.+\.rels$/)) {
      const d = parseXml(await read(zip, p));
      for (const rel of byLocal(d, "Relationship")) {
        if (/vmlDrawing/.test(rel.getAttribute("Type"))) {
          const target = resolve("xl/worksheets/", rel.getAttribute("Target"));
          const vml = await read(zip, target);
          if (vml && /x:ClientData ObjectType="Note"/.test(vml)) {
            await removePart(zip, target);
            const src = p.replace(/_rels\/([^/]*)\.rels$/, "$1");
            const sd = parseXml(await read(zip, src));
            byLocal(sd, "legacyDrawing").forEach((e) => e.remove());
            zip.file(src, ser(sd));
          }
        }
      }
    }
    const ss = "xl/sharedStrings.xml";
    if (zip.file(ss)) {
      const d = parseXml(await read(zip, ss));
      for (const t of byLocal(d, "t")) t.textContent = cleanText(t.textContent);
      zip.file(ss, ser(d));
    }
  }
  if (kind === "pptx") {
    for (const p of files(zip, /^ppt\/(notesSlides\/notesSlide\d+\.xml|comments\/.+\.xml|commentAuthors\.xml|authors\.xml)$/)) await removePart(zip, p);
    for (const p of files(zip, /^ppt\/notesSlides\/_rels\//)) zip.remove(p);
    for (const p of files(zip, /^ppt\/slides\/slide\d+\.xml$/)) {
      const d = parseXml(await read(zip, p));
      for (const t of byLocal(d, "t")) t.textContent = cleanText(t.textContent);
      zip.file(p, ser(d));
    }
  }

  // photos inside the document
  for (const m of files(zip, /^(word|xl|ppt)\/media\/[^/]+\.(jpe?g|png|webp)$/i)) {
    const c = await cleanImage(await zip.file(m).async("uint8array"));
    if (c) zip.file(m, c.bytes);
  }

  // fresh, timestamp-free zip
  const out = new JSZip();
  const names = Object.keys(zip.files).filter((n) => !zip.files[n].dir);
  names.sort((a, b) => (a === CT ? -1 : b === CT ? 1 : 0));
  const epoch = new Date(Date.UTC(1980, 0, 1));
  for (const n of names) out.file(n, await zip.file(n).async("uint8array"), { date: epoch, createFolders: false });
  const mime = { docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation" }[kind];
  return { bytes: await out.generateAsync({ type: "uint8array", compression: "DEFLATE", mimeType: mime }), mime };
}
