// Engine entry point: scan(file) -> Report, clean(file, report) -> {bytes, mime, after}.
// Runs entirely in the browser (extension pages / offscreen document). Nothing leaves the machine.
import { Report } from "./report.js";
import { scanImage, cleanImage, imageKind } from "./image.js";
import { scanPdf, cleanPdf } from "./pdf.js";
import { scanOffice, cleanOffice, isOoxml } from "./office.js";
import { scanTextCanaries, scanStrings, cleanText, latin1, utf8, mapsLink } from "./common.js";

export { Report };
export { summarize, RANK, SEVERITIES, FACT_LABELS } from "./report.js";

const TEXT_EXT = /\.(txt|md|csv|tsv|json|html?|xml|log|rtf)$/i;
const MEDIA_EXT = /\.(mp4|m4v|mov|3gp|m4a)$/i;
const OFFICE_EXT = /\.(docx|docm|xlsx|xlsm|pptx|pptm)$/i;

export function detect(name = "", u8) {
  if (latin1(u8, 0, 5) === "%PDF-" || latin1(u8, 0, 1024).includes("%PDF-")) return "pdf";
  const img = imageKind(u8);
  if (img && img !== "heic") return "image";
  if (img === "heic") return "image";
  if (isOoxml(u8) && OFFICE_EXT.test(name)) return "office";
  if (latin1(u8, 4, 8) === "ftyp" || MEDIA_EXT.test(name)) return "media";
  if (TEXT_EXT.test(name)) return "text";
  return null;
}

// What the upload interceptor should bother holding back.
export const SUPPORTED = /\.(jpe?g|png|webp|gif|tiff?|heic|heif|pdf|docx|docm|xlsx|xlsm|pptx|pptm|txt|md|csv|tsv|json|mp4|m4v|mov|3gp|m4a)$/i;

export async function scan({ name, bytes, type = "" }) {
  const r = new Report(name, "", bytes.length);
  const kind = detect(name, bytes);
  r.kind = kind;
  try {
    if (kind === "pdf") await scanPdf(r, bytes);
    else if (kind === "image") await scanImage(r, bytes);
    else if (kind === "office") await scanOffice(r, bytes);
    else if (kind === "media") scanMedia(r, bytes);
    else if (kind === "text") {
      r.filetype = "text";
      const t = utf8(bytes);
      scanTextCanaries(r, t, "text");
      r.cleanable = true;
      r.cleanNote = "Invisible characters and look-alike letters removed.";
    } else {
      r.filetype = type || "unknown";
    }
  } catch (e) {
    r.errors.push(`scan failed: ${e?.message || e}`);
  }
  return r;
}

export async function clean({ name, bytes, type = "" }, report) {
  const kind = report?.kind ?? detect(name, bytes);
  let res = null;
  if (kind === "pdf") res = await cleanPdf(bytes, report);
  else if (kind === "image") res = await cleanImage(bytes);
  else if (kind === "office") res = await cleanOffice(bytes);
  else if (kind === "text") res = { bytes: new TextEncoder().encode(cleanText(utf8(bytes))), mime: type || "text/plain" };
  if (!res) return null;
  const after = await scan({ name, bytes: res.bytes, type: res.mime });
  return { ...res, after };
}

// QuickTime "mdta" metadata: moov/meta/keys lists names, moov/meta/ilst holds values by 1-based index.
function mdtaTags(u8) {
  const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
  const out = {};
  const walk = (start, end, depth) => {
    let keys = null;
    for (let i = start; i + 8 <= end;) {
      let size = dv.getUint32(i);
      const type = latin1(u8, i + 4, i + 8);
      let hdr = 8;
      if (size === 1 && i + 16 <= end) { size = Number(dv.getBigUint64(i + 8)); hdr = 16; }
      if (size === 0) size = end - i;
      if (size < 8 || i + size > end) break;
      const body = i + hdr;
      if (["moov", "udta", "trak"].includes(type) && depth < 4) walk(body, i + size, depth + 1);
      if (type === "meta") {
        // QuickTime meta has no version/flags; ISO meta does
        const isoMeta = latin1(u8, body + 4, body + 8) !== "hdlr";
        walk(body + (isoMeta ? 4 : 0), i + size, depth + 1);
      }
      if (type === "keys") {
        keys = [];
        const n = dv.getUint32(body + 4);
        let p = body + 8;
        for (let k = 0; k < n && p + 8 <= i + size; k++) {
          const ks = dv.getUint32(p);
          keys.push(latin1(u8, p + 8, p + ks));
          p += ks;
        }
        out.__keys = keys;
      }
      if (type === "ilst") {
        for (let p = body; p + 8 <= i + size;) {
          const is = dv.getUint32(p);
          if (is < 8) break;
          const idx = dv.getUint32(p + 4);
          const d = p + 8;
          if (latin1(u8, d + 4, d + 8) === "data") {
            const ds = dv.getUint32(d);
            const v = utf8(u8.subarray(d + 16, d + ds)).replace(/\0+$/, "");
            const name = out.__keys?.[idx - 1] ?? latin1(u8, p + 4, p + 8).replace("\xa9", "©");
            out[name] = v;
          }
          p += is;
        }
      }
      i += size;
    }
  };
  try { walk(0, u8.length, 0); } catch { /* malformed */ }
  delete out.__keys;
  return out;
}

// MP4 / MOV: Apple & Android write ISO-6709 location and device into the moov box.
function scanMedia(r, u8) {
  r.filetype = "video";
  const head = latin1(u8, 0, Math.min(u8.length, 4 << 20));
  const tail = u8.length > 4 << 20 ? latin1(u8, u8.length - (4 << 20)) : "";
  const s = head + tail;
  const loc = /([+-]\d{1,2}\.\d{3,})([+-]\d{1,3}\.\d{3,})([+-]\d+(?:\.\d+)?)?\/?/.exec(s);
  if (loc) {
    const lat = parseFloat(loc[1]), lon = parseFloat(loc[2]);
    if (Math.abs(lat) <= 90 && Math.abs(lon) <= 180) {
      r.add("critical", "location", "GPS coordinates", `${lat}, ${lon} → ${mapsLink(lat, lon)}`, "video metadata", "GPS");
      r.fact("location", `GPS ${lat}, ${lon}`);
    }
  }
  const tags = mdtaTags(u8);
  const MAP = {
    "com.apple.quicktime.make": ["Device make", "medium", "device", "Device"],
    "com.apple.quicktime.model": ["Device model", "medium", "device", "Device"],
    "com.apple.quicktime.software": ["Software", "low", "software", "Software"],
    "com.android.manufacturer": ["Device make", "medium", "device", "Device"],
    "com.android.model": ["Device model", "medium", "device", "Device"],
    "com.apple.quicktime.author": ["Author", "high", "identity", "Author"],
    "artist": ["Artist", "high", "identity", "Author"],
  };
  for (const [k, v] of Object.entries(tags)) {
    const m = MAP[k];
    if (m && v) { r.add(m[1], m[2], m[0], v, "video metadata", m[3]); r.fact(m[2] === "identity" ? "identity" : m[2], v); }
  }
  const art = /©ART.{12}([\x20-\x7e]{2,60})/.exec(s);
  if (art) { r.add("high", "identity", "Artist", art[1], "video metadata", "Author"); r.fact("identity", art[1]); }
  scanStrings(r, s.slice(0, 200000).match(/[\x20-\x7e]{12,}/g)?.join("\n") || "", "video metadata", "medium");
  r.cleanable = false;
  r.cleanNote = "Video can't be cleaned in the browser yet; run `unredact.tools sanitize` locally.";
}
