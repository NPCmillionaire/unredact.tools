// Images: EXIF / XMP / IPTC / PNG text / trailing data, and lossless metadata stripping.
import exifr from "../vendor/full.esm.mjs";
import { scanStrings, latin1, utf8, ascii, concat, indexOfBytes, mapsLink } from "./common.js";

export function imageKind(u8) {
  if (u8[0] === 0xff && u8[1] === 0xd8) return "jpeg";
  if (u8[0] === 0x89 && latin1(u8, 1, 4) === "PNG") return "png";
  if (latin1(u8, 0, 4) === "RIFF" && latin1(u8, 8, 12) === "WEBP") return "webp";
  if (latin1(u8, 0, 3) === "GIF") return "gif";
  if ((u8[0] === 0x49 && u8[1] === 0x49) || (u8[0] === 0x4d && u8[1] === 0x4d)) return "tiff";
  if (latin1(u8, 4, 8) === "ftyp" && /hei|mif|avif/.test(latin1(u8, 8, 12))) return "heic";
  return null;
}

// ------------------------------------------------------------------ JPEG segments
export function jpegSegments(u8) {
  const segs = []; // {marker, start, end, data(payload)}
  let i = 2, sof = null;
  while (i < u8.length - 1) {
    if (u8[i] !== 0xff) return { segs, sof, scanStart: i, broken: true };
    const m = u8[i + 1];
    if (m === 0xff) { i++; continue; }
    if (m === 0xd9) return { segs, sof, scanStart: i, eoi: i + 2 };
    if (m >= 0xd0 && m <= 0xd7) { i += 2; continue; }
    const len = (u8[i + 2] << 8) | u8[i + 3];
    const seg = { marker: m, start: i, end: i + 2 + len, data: u8.subarray(i + 4, i + 2 + len) };
    segs.push(seg);
    if (m >= 0xc0 && m <= 0xcf && m !== 0xc4 && m !== 0xc8 && m !== 0xcc) {
      sof = { h: (seg.data[1] << 8) | seg.data[2], w: (seg.data[3] << 8) | seg.data[4] };
    }
    if (m === 0xda) {
      // entropy-coded data until the next non-RST marker; find EOI
      let j = seg.end;
      while (j < u8.length - 1) {
        if (u8[j] === 0xff && u8[j + 1] !== 0 && !(u8[j + 1] >= 0xd0 && u8[j + 1] <= 0xd7)) {
          if (u8[j + 1] === 0xd9) return { segs, sof, scanStart: seg.start, eoi: j + 2 };
          if (u8[j + 1] !== 0xff) break; // another marker (progressive: more scans)
        }
        j++;
      }
      i = j;
      continue;
    }
    i = seg.end;
  }
  return { segs, sof, scanStart: i, eoi: null };
}

// ------------------------------------------------------------------ PNG / WebP chunks
export function pngChunks(u8) {
  const out = [];
  let i = 8;
  while (i + 8 <= u8.length) {
    const len = ((u8[i] << 24) >>> 0) + (u8[i + 1] << 16) + (u8[i + 2] << 8) + u8[i + 3];
    const type = latin1(u8, i + 4, i + 8);
    out.push({ type, start: i, end: i + 12 + len, data: u8.subarray(i + 8, i + 8 + len) });
    i += 12 + len;
    if (type === "IEND") break;
  }
  return { chunks: out, end: i };
}

export function riffChunks(u8) {
  const out = [];
  let i = 12;
  while (i + 8 <= u8.length) {
    const type = latin1(u8, i, i + 4);
    const len = u8[i + 4] | (u8[i + 5] << 8) | (u8[i + 6] << 16) | ((u8[i + 7] << 24) >>> 0);
    out.push({ type, start: i, end: i + 8 + len + (len & 1), data: u8.subarray(i + 8, i + 8 + len) });
    i += 8 + len + (len & 1);
  }
  return out;
}

async function inflate(data) {
  try {
    const s = new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate"));
    return new Uint8Array(await new Response(s).arrayBuffer());
  } catch { return new Uint8Array(); }
}

async function imageSize(bytes) {
  try {
    const bm = await createImageBitmap(new Blob([bytes]));
    const s = { w: bm.width, h: bm.height };
    bm.close();
    return s;
  } catch { return null; }
}

const str = (v) => (v == null ? "" : Array.isArray(v) ? v.join(", ") : v instanceof Uint8Array ? utf8(v).replace(/\0+$/, "") : typeof v === "object" ? (v.value ?? JSON.stringify(v)) : String(v)).trim();

// ------------------------------------------------------------------ scan
export async function scanImage(report, u8, src = "image") {
  const kind = imageKind(u8);
  report.filetype ||= kind;
  let meta = {};
  try {
    meta = (await exifr.parse(u8, {
      tiff: true, ifd0: true, exif: true, gps: true, ifd1: true, xmp: true, iptc: true, icc: false, jfif: false,
      interop: false, makerNote: false, userComment: true, mergeOutput: true, translateValues: true, reviveValues: true,
      multiSegment: true,
    })) || {};
  } catch (e) {
    if (!/Unknown file format/i.test(String(e))) report.errors.push(`metadata parse: ${e.message || e}`);
  }

  // WHERE
  const lat = meta.latitude, lon = meta.longitude;
  if (Number.isFinite(lat) && Number.isFinite(lon) && !(lat === 0 && lon === 0)) {
    report.add("critical", "location", "GPS coordinates", `${lat.toFixed(6)}, ${lon.toFixed(6)} → ${mapsLink(lat, lon)}`, `${src} EXIF`, "GPS");
    report.fact("location", `GPS ${lat.toFixed(5)}, ${lon.toFixed(5)}`);
    if (meta.GPSAltitude != null) report.add("medium", "location", "GPS altitude", `${Math.round(meta.GPSAltitude)} m`, `${src} EXIF`, "Altitude");
    if (meta.GPSImgDirection != null) report.add("medium", "location", "Camera compass direction", `${Math.round(meta.GPSImgDirection)}°`, `${src} EXIF`, "Compass");
  }
  for (const k of ["City", "State", "Country", "Sublocation", "Location", "CountryName", "ProvinceState", "SubLocation"]) {
    if (meta[k] && typeof meta[k] !== "object") {
      report.add("high", "location", `Place name (${k})`, str(meta[k]), `${src} XMP/IPTC`, "Place name");
      report.fact("location", str(meta[k]));
    }
  }

  // WHO
  const who = { Artist: "Artist", Copyright: "Copyright", CameraOwnerName: "Camera owner", OwnerName: "Camera owner",
    XPAuthor: "Author", creator: "Creator", Creator: "Creator", "By-line": "Byline", Byline: "Byline", Credit: "Credit",
    rights: "Rights", Rights: "Rights", Contact: "Contact", AuthorsPosition: "Author title" };
  for (const [k, label] of Object.entries(who)) {
    const v = str(meta[k]);
    if (v && !/^©?\s*$/.test(v)) {
      report.add("high", "identity", label, v, `${src} metadata`, "Author");
      report.fact("identity", v);
    }
  }

  // DEVICE
  const dev = [str(meta.Make), str(meta.Model)].filter(Boolean).join(" ");
  if (dev) { report.add("medium", "device", "Camera / phone model", dev, `${src} EXIF`, "Device"); report.fact("device", dev); }
  for (const k of ["BodySerialNumber", "SerialNumber", "LensSerialNumber", "InternalSerialNumber", "CameraSerialNumber", "ImageUniqueID"]) {
    const v = str(meta[k]);
    if (v) { report.add("high", "device", `Serial number (${k})`, v, `${src} EXIF`, "Serial number"); report.fact("device", `serial ${v}`); }
  }
  if (meta.LensModel) report.add("low", "device", "Lens", str(meta.LensModel), `${src} EXIF`, "Lens");
  if (meta.HostComputer) report.add("medium", "device", "Host computer", str(meta.HostComputer), `${src} EXIF`, "Device");

  // WHEN
  const when = meta.DateTimeOriginal || meta.CreateDate || meta.DateTime || meta.ModifyDate;
  if (when) {
    const w = when instanceof Date ? when.toISOString().replace(".000Z", "Z") : str(when);
    report.add("low", "time", "Capture time", w, `${src} EXIF`, "Timestamp");
    report.fact("time", w);
  }
  const tz = meta.OffsetTimeOriginal || meta.OffsetTime;
  if (tz) report.add("medium", "time", "Time zone offset", `${str(tz)} — narrows where you were and enables sun/shadow geolocation`, `${src} EXIF`, "Time zone");

  // SOFTWARE
  for (const k of ["Software", "CreatorTool", "ProcessingSoftware"]) {
    if (meta[k]) { report.add("low", "software", k, str(meta[k]), `${src} metadata`, "Software"); report.fact("software", str(meta[k])); }
  }

  // TRACKING (XMP ids link edited copies back to the original file)
  for (const k of ["DocumentID", "OriginalDocumentID", "InstanceID", "DerivedFrom", "History"]) {
    const v = str(meta[k]);
    if (v) report.add("medium", "tracking", `XMP ${k}`, v.slice(0, 200), `${src} XMP`, "Edit history IDs");
  }

  // free-text fields
  for (const k of ["ImageDescription", "UserComment", "XPComment", "XPSubject", "XPKeywords", "description", "title", "Caption-Abstract", "Keywords", "subject", "Headline"]) {
    const v = str(meta[k]);
    if (v && v.length > 1 && !/^(\s|default|ascii|undefined)*$/i.test(v)) {
      report.add("medium", "hidden-content", `Embedded text (${k})`, v.slice(0, 300), `${src} metadata`, "Caption text");
      scanStrings(report, v, `${src} ${k}`);
    }
  }
  scanStrings(report, Object.values(meta).filter((v) => typeof v === "string").join("\n"), `${src} metadata`);

  // ---- format-specific
  if (kind === "jpeg") await scanJpeg(report, u8, src);
  if (kind === "png") await scanPng(report, u8, src);
  if (kind === "webp") {
    for (const c of riffChunks(u8)) if (c.type === "EXIF" || c.type === "XMP ") report.fact("hidden", `WebP ${c.type.trim()} chunk`);
  }

  report.cleanable = ["jpeg", "png", "webp"].includes(kind);
  if (!report.cleanable && kind) report.cleanNote = `${kind.toUpperCase()} can't be cleaned in the browser yet; use the CLI.`;
  return report;
}

async function scanJpeg(report, u8, src) {
  const { segs, sof, eoi } = jpegSegments(u8);
  for (const s of segs) {
    if (s.marker === 0xfe) {
      const t = utf8(s.data).replace(/\0+$/, "");
      if (t.trim()) {
        report.add("medium", "hidden-content", "JPEG comment", t.slice(0, 300), `${src} COM`, "Comment");
        scanStrings(report, t, `${src} COM`);
      }
    }
    if (s.marker === 0xe1 && latin1(s.data, 0, 29).startsWith("http://ns.adobe.com/xmp/extension/")) {
      report.add("low", "hidden-content", "Extended XMP block", `${s.data.length} bytes`, `${src} APP1`, "Extended XMP");
    }
  }
  // embedded thumbnail: may show the image before it was cropped or edited
  try {
    const thumb = await exifr.thumbnail(u8);
    if (thumb && thumb.length) {
      const t = await imageSize(thumb);
      const main = sof;
      const differs = t && main && Math.abs(t.w / t.h - main.w / main.h) > 0.05;
      report.add(differs ? "high" : "medium", "hidden-content",
        differs ? "Embedded thumbnail shows a different crop" : "Embedded thumbnail",
        differs ? `Thumbnail ${t.w}×${t.h} vs image ${main.w}×${main.h}: it likely shows what you cropped out.`
          : `${thumb.length} byte preview stored inside the file.`, `${src} EXIF IFD1`, differs ? "Uncropped thumbnail" : "Thumbnail");
      report.artifact("thumbnail.jpg", new Uint8Array(thumb), "image/jpeg", "Embedded EXIF thumbnail");
      if (differs) report.fact("hidden", "thumbnail of the uncropped original");
    }
  } catch { /* no thumbnail */ }
  // data after EOI (motion photos, appended archives)
  if (eoi && u8.length - eoi > 32) {
    const tail = u8.subarray(eoi);
    const isVid = latin1(tail, 0, 64).includes("ftyp");
    report.add("high", "hidden-content", isVid ? "Hidden video appended (motion photo)" : "Data appended after the image",
      `${tail.length.toLocaleString()} extra bytes after the end of the JPEG`, src, isVid ? "Motion photo" : "Appended data");
    report.artifact(isVid ? "appended.mp4" : "appended.bin", tail, isVid ? "video/mp4" : "application/octet-stream", "Bytes after JPEG end");
    report.fact("hidden", isVid ? "embedded motion-photo video" : `${tail.length} hidden trailing bytes`);
  }
}

async function scanPng(report, u8, src) {
  const { chunks, end } = pngChunks(u8);
  for (const c of chunks) {
    if (c.type !== "tEXt" && c.type !== "zTXt" && c.type !== "iTXt") {
      if (c.type === "tIME") report.add("low", "time", "PNG modification time", "", `${src} tIME`, "Timestamp");
      continue;
    }
    const nul = c.data.indexOf(0);
    const key = latin1(c.data, 0, nul);
    let val;
    if (c.type === "tEXt") val = latin1(c.data, nul + 1);
    else if (c.type === "zTXt") val = utf8(await inflate(c.data.subarray(nul + 2)));
    else {
      const comp = c.data[nul + 1];
      let p = nul + 3;
      p = c.data.indexOf(0, p) + 1; // language
      p = c.data.indexOf(0, p) + 1; // translated keyword
      val = comp ? utf8(await inflate(c.data.subarray(p))) : utf8(c.data.subarray(p));
    }
    if (key === "XML:com.adobe.xmp") continue; // exifr already handled XMP
    const ai = /^(parameters|prompt|workflow|Dream|sd-metadata|invokeai)/i.test(key);
    report.add(ai ? "high" : "medium", ai ? "hidden-content" : "identity",
      ai ? `AI generation prompt (${key})` : `PNG text: ${key}`, val.slice(0, 300), `${src} ${c.type}`, ai ? "AI prompt" : key === "Author" ? "Author" : "PNG text");
    scanStrings(report, val, `${src} ${c.type} ${key}`);
  }
  if (u8.length - end > 16) {
    report.add("high", "hidden-content", "Data appended after the image", `${(u8.length - end).toLocaleString()} extra bytes after IEND`, src, "Appended data");
  }
}

// ------------------------------------------------------------------ clean (lossless)
function minimalExifOrientation(o) {
  const tiff = [0x4d, 0x4d, 0, 0x2a, 0, 0, 0, 8, 0, 1, 0x01, 0x12, 0, 3, 0, 0, 0, 1, 0, o, 0, 0, 0, 0, 0, 0];
  const payload = concat([ascii("Exif\0\0"), Uint8Array.from(tiff)]);
  const len = payload.length + 2;
  return concat([Uint8Array.of(0xff, 0xe1, len >> 8, len & 0xff), payload]);
}

export async function cleanImage(u8) {
  const kind = imageKind(u8);
  if (kind === "jpeg") {
    let orientation = 1;
    try { orientation = (await exifr.orientation(u8)) || 1; } catch { /* none */ }
    const { segs, eoi } = jpegSegments(u8);
    const parts = [Uint8Array.of(0xff, 0xd8)];
    let wroteOrient = orientation === 1;
    for (const s of segs) {
      const m = s.marker;
      const id = latin1(s.data, 0, 14);
      const keep = (m === 0xe0 && id.startsWith("JFIF")) || (m === 0xe2 && id.startsWith("ICC_PROFILE")) ||
        (m === 0xee && id.startsWith("Adobe")) || (m < 0xe0 || m > 0xef) && m !== 0xfe;
      if (!keep) continue;
      if (!wroteOrient && m !== 0xe0) { parts.push(minimalExifOrientation(orientation)); wroteOrient = true; }
      if (m === 0xda) { parts.push(u8.subarray(s.start, eoi ?? u8.length)); break; }
      parts.push(u8.subarray(s.start, s.end));
    }
    return { bytes: concat(parts), mime: "image/jpeg" };
  }
  if (kind === "png") {
    const KEEP = new Set(["IHDR", "PLTE", "IDAT", "IEND", "tRNS", "gAMA", "cHRM", "sRGB", "iCCP", "sBIT", "pHYs", "acTL", "fcTL", "fdAT", "bKGD", "cICP"]);
    const { chunks } = pngChunks(u8);
    return { bytes: concat([u8.subarray(0, 8), ...chunks.filter((c) => KEEP.has(c.type)).map((c) => u8.subarray(c.start, c.end))]), mime: "image/png" };
  }
  if (kind === "webp") {
    const chunks = riffChunks(u8).filter((c) => c.type !== "EXIF" && c.type !== "XMP ");
    const body = chunks.map((c) => {
      const b = u8.slice(c.start, c.end);
      if (c.type === "VP8X") b[8] &= ~(0x08 | 0x04); // clear EXIF + XMP flags
      return b;
    });
    const size = 4 + body.reduce((a, b) => a + b.length, 0);
    const hdr = concat([ascii("RIFF"), Uint8Array.of(size & 255, (size >> 8) & 255, (size >> 16) & 255, (size >>> 24) & 255), ascii("WEBP")]);
    return { bytes: concat([hdr, ...body]), mime: "image/webp" };
  }
  return null;
}

export { indexOfBytes };
