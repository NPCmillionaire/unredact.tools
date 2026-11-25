// Ports of unredact_tools/common.py: leaky strings and text canary tricks.

const LEAKS = [
  ["identity", "Windows username in path", /[A-Za-z]:[\\/]+(?:Users|Documents and Settings)[\\/]+([^\\/:*?"<>|\r\n]+)/gi, "Username"],
  ["identity", "macOS username in path", /\/Users\/([A-Za-z0-9._-]+)/g, "Username"],
  ["identity", "Linux username in path", /\/home\/([A-Za-z0-9._-]+)/g, "Username"],
  ["network", "Internal server (UNC path)", /\\\\([A-Za-z0-9._-]+)\\[^\s"<>]+/g, "Internal path"],
  ["network", "SharePoint / OneDrive URL", /https?:\/\/([\w-]+(?:-my)?\.sharepoint\.com)\/[^\s"<>]*/gi, "SharePoint URL"],
  ["identity", "Email address", /[\w.+-]+@[\w-]+\.[\w.-]+/g, "Email"],
  ["network", "Private IP address", /\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b/g, "Private IP"],
  ["network", "Local file URL", /file:\/{2,3}[^\s"<>]+/gi, "File URL"],
];

export function scanStrings(report, text, source, severity = "high") {
  if (!text) return;
  const seen = new Set();
  for (const [bucket, label, rx, tag] of LEAKS) {
    for (const m of String(text).matchAll(rx)) {
      const whole = m[0];
      if (seen.has(label + whole) || report.findings.some((f) => f.title === label && f.detail === whole.slice(0, 200))) continue;
      seen.add(label + whole);
      report.add(severity, bucket, label, whole.slice(0, 200), source, tag);
      if (label.includes("username")) report.fact("identity", `username '${m[1]}'`);
      else if (label === "Email address") report.fact("identity", whole);
      else report.fact("network", whole.slice(0, 120));
    }
  }
}

export const ZERO_WIDTH = {
  "​": "ZERO WIDTH SPACE", "‌": "ZWNJ", "‍": "ZWJ", "‎": "LRM", "‏": "RLM",
  "⁠": "WORD JOINER", "⁡": "FUNCTION APPLICATION", "⁢": "INVISIBLE TIMES",
  "⁣": "INVISIBLE SEPARATOR", "⁤": "INVISIBLE PLUS", "﻿": "BOM / ZWNBSP",
  "᠎": "MONGOLIAN VOWEL SEP", "­": "SOFT HYPHEN",
};
const ODD_SPACES = new Set([..."           ", " ", " ", " ", "　"]);
const isTag = (cp) => cp >= 0xe0000 && cp < 0xe0080;

const CONFUSABLES = {
  "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s",
  "ԁ": "d", "ӏ": "l", "һ": "h", "ԛ": "q", "ԝ": "w", "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M",
  "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X", "Ѕ": "S", "І": "I", "Ј": "J", "ο": "o",
  "α": "a", "ν": "v", "ρ": "p", "ι": "i", "κ": "k", "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H",
  "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
};
const WORD = /[\p{L}\p{N}_]+/gu;
const script = (ch) =>
  /\p{Script=Latin}/u.test(ch) ? "latin" : /\p{Script=Cyrillic}/u.test(ch) ? "cyrillic" : /\p{Script=Greek}/u.test(ch) ? "greek" : null;
const isMixed = (w) => {
  const s = new Set([...w].filter((c) => /\p{L}/u.test(c)).map(script).filter(Boolean));
  return s.has("latin") && s.size > 1;
};
export const mixedScriptWords = (text) => (String(text).match(WORD) || []).filter(isMixed);

export function scanTextCanaries(report, text, source) {
  if (!text) return;
  const zw = {};
  for (const ch of text) if (ZERO_WIDTH[ch]) zw[ZERO_WIDTH[ch]] = (zw[ZERO_WIDTH[ch]] || 0) + 1;
  if (text.startsWith("﻿") && zw["BOM / ZWNBSP"] === 1) delete zw["BOM / ZWNBSP"];
  delete zw["SOFT HYPHEN"];
  const total = Object.values(zw).reduce((a, b) => a + b, 0);
  if (total) {
    report.add(total > 3 ? "high" : "medium", "tracking", `${total} invisible zero-width characters`,
      "Pattern can encode a per-recipient ID (canary trap). " + Object.entries(zw).map(([k, v]) => `${k}x${v}`).join(", "),
      source, "Zero-width watermark");
    report.fact("tracking", `${total} zero-width chars (possible per-copy watermark)`);
  }
  const tags = [...text].filter((c) => isTag(c.codePointAt(0)));
  if (tags.length) {
    const hidden = tags.map((c) => String.fromCharCode(c.codePointAt(0) - 0xe0000)).join("");
    report.add("high", "hidden-content", `${tags.length} invisible Unicode tag characters`,
      `Decoded hidden text: ${JSON.stringify(hidden.slice(0, 200))}`, source, "Hidden text");
    report.fact("hidden", `invisible tag-encoded text: ${JSON.stringify(hidden.slice(0, 80))}`);
  }
  const odd = [...text].filter((c) => ODD_SPACES.has(c)).length;
  if (odd > 5) {
    report.add("low", "tracking", `${odd} non-standard space characters`,
      "Varying space widths is a known per-copy watermarking technique (also produced innocently by some editors).", source, "Odd spaces");
  }
  const mixed = mixedScriptWords(text);
  if (mixed.length) {
    report.add("high", "tracking", `${mixed.length} words mix Latin with Cyrillic/Greek look-alikes`,
      `Homoglyph substitution is used to fingerprint leaked copies. e.g. ${[...new Set(mixed)].sort().slice(0, 8).join(", ")}`,
      source, "Homoglyph watermark");
    report.fact("tracking", `${mixed.length} homoglyph-substituted words`);
  }
}

export function cleanText(text) {
  let out = "";
  for (const ch of text) {
    if (ZERO_WIDTH[ch] || isTag(ch.codePointAt(0))) continue;
    out += ODD_SPACES.has(ch) ? " " : ch;
  }
  return out.replace(WORD, (w) => (isMixed(w) ? [...w].map((c) => CONFUSABLES[c] || c).join("") : w));
}

// ------------------------------------------------------------------ bytes helpers
export const latin1 = (u8, start = 0, end = u8.length) => {
  let s = "";
  for (let i = start; i < end; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, Math.min(end, i + 0x8000)));
  return s;
};
export const utf8 = (u8) => new TextDecoder("utf-8", { fatal: false }).decode(u8);
export const ascii = (s) => Uint8Array.from(s, (c) => c.charCodeAt(0));

export function concat(parts) {
  const len = parts.reduce((a, p) => a + p.length, 0);
  const out = new Uint8Array(len);
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

export function indexOfBytes(hay, needle, from = 0) {
  outer: for (let i = from; i <= hay.length - needle.length; i++) {
    for (let j = 0; j < needle.length; j++) if (hay[i + j] !== needle[j]) continue outer;
    return i;
  }
  return -1;
}

export function toB64(u8) {
  let s = "";
  for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
  return btoa(s);
}
export function fromB64(b64) {
  const s = atob(b64);
  const u8 = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) u8[i] = s.charCodeAt(i);
  return u8;
}

export const mapsLink = (lat, lon) => `https://www.openstreetmap.org/?mlat=${lat.toFixed(6)}&mlon=${lon.toFixed(6)}#map=17/${lat.toFixed(6)}/${lon.toFixed(6)}`;
