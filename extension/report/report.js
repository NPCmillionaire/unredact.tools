// Full report for a file from the context menu, the popup, or a local drop.
import { scan, clean, FACT_LABELS, RANK } from "../engine/index.js";
import { fromB64 } from "../engine/common.js";
import { getSettings } from "../shared/settings.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const show = (id, on = true) => ($(id).hidden = !on);
const fmtBytes = (n) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);
const params = new URLSearchParams(location.search);
let settings;

const EXT = { "application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif", "image/heic": "heic",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx", "video/mp4": "mp4", "video/quicktime": "mov", "text/plain": "txt" };

function nameFrom(url, disposition, type) {
  const m = /filename\*=(?:UTF-8'')?([^;]+)|filename="?([^";]+)"?/i.exec(disposition || "");
  if (m) { try { return decodeURIComponent((m[1] || m[2]).trim()); } catch { return (m[1] || m[2]).trim(); } }
  try {
    const last = decodeURIComponent(new URL(url).pathname.split("/").filter(Boolean).pop() || "");
    if (/\.\w{2,5}$/.test(last)) return last;
    const ext = EXT[(type || "").split(";")[0].trim()];
    return (last || "file") + (ext ? "." + ext : "");
  } catch { return "file"; }
}

async function fromUrl(url) {
  $("loadmsg").textContent = `Fetching ${url.length > 80 ? url.slice(0, 80) + "…" : url}`;
  show("loading");
  let bytes, type = "", disp = "";
  try {
    const r = await fetch(url, { credentials: "include" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    type = r.headers.get("content-type") || "";
    disp = r.headers.get("content-disposition") || "";
    if (params.get("page") && /text\/html/.test(type)) throw new Error("html");
    bytes = new Uint8Array(await r.arrayBuffer());
  } catch (e) {
    if (e.message === "html") return fail("This is a web page, not a file. Right-click an image, a PDF, or a link to a file instead.");
    // some files only load with the page's own cookies / origin
    const tabId = Number(params.get("tab"));
    if (tabId) {
      const got = await chrome.runtime.sendMessage({ op: "fetchInTab", tabId, frameId: Number(params.get("frame") || 0), url }).catch((x) => ({ error: String(x) }));
      if (got && !got.error) { bytes = fromB64(got.b64); type = got.type; disp = got.disposition; }
    }
    if (!bytes) {
      const origin = originPattern(url);
      if (origin && !(await chrome.permissions.contains({ origins: [origin] }))) return askAccess(url, origin);
      return fail(`Couldn't download the file (${esc(e.message || e)}). If it needs a login, download it and drop it here instead.`);
    }
  }
  return run({ name: nameFrom(url, disp, type), type, bytes, source: url });
}

function originPattern(url) {
  try { const x = new URL(url); return /^https?:$/.test(x.protocol) ? `${x.protocol}//${x.hostname}/*` : null; } catch { return null; }
}

// Files on sites outside the built-in list need a one-time OK for that site (an optional host permission).
function askAccess(url, origin) {
  const host = new URL(url).hostname;
  fail(`To check this file, unredact.tools needs your OK to download from <b>${esc(host)}</b>. The file is still only read on this device.<br><br><button class="btn primary" id="grant">Allow ${esc(host)}</button>`);
  $("grant").onclick = async () => {
    if (await chrome.permissions.request({ origins: [origin] }).catch(() => false)) fromUrl(url);
  };
}

function fail(msg) {
  show("loading", false);
  $("err").innerHTML = msg;
  show("err");
  show("drop");
}

let current = null;

async function run(file) {
  show("drop", false); show("err", false); show("result", false);
  $("loadmsg").textContent = `Checking ${file.name}…`;
  show("loading");
  document.title = `${file.name} · unredact.tools`;
  const report = await scan(file);
  current = { file, report };
  show("loading", false);
  render(file, report);
  show("result");
  deepAvailable();
}

function findingHtml(f) {
  const detail = esc(f.detail).replace(/(https:\/\/www\.openstreetmap\.org\/\S+)/, '<a href="$1" target="_blank" rel="noreferrer">open map</a>');
  return `<div class="finding"><span class="sev ${f.severity}">${f.severity}</span><div class="t">${esc(f.title)}</div>
    ${f.detail ? `<div class="d">${detail}</div>` : ""}${f.source ? `<div class="s">${esc(f.source)}</div>` : ""}</div>`;
}

function render(file, r) {
  $("fname").textContent = file.name;
  const src = file.source ? ` · <a href="${esc(file.source)}" target="_blank" rel="noreferrer">${esc(new URL(file.source).hostname || "source")}</a>` : " · local file";
  $("fmeta").innerHTML = `${esc(r.filetype || r.kind || "unknown type")} · ${fmtBytes(file.bytes.length)}${r.pages ? ` · ${r.pages} page${r.pages > 1 ? "s" : ""}` : ""}${src}`;
  const c = r.counts();
  $("counts").innerHTML = ["critical", "high", "medium", "low"].filter((s) => c[s]).map((s) => `<span class="sev ${s}">${c[s]} ${s}</span>`).join("");

  const leaks = r.leaks(settings.minSeverity);
  const v = $("verdict");
  if (!r.kind) {
    v.className = "verdict";
    v.innerHTML = `Not a file type the extension can read yet.<small>Supported: JPEG, PNG, WebP, HEIC/TIFF (read-only), PDF, Word, Excel, PowerPoint, MP4/MOV (read-only), plain text.</small>`;
  } else if (leaks.length) {
    const tags = [...new Set(leaks.map((f) => f.tag))];
    v.className = "verdict bad";
    v.innerHTML = `⚠️ ${leaks.length} leak${leaks.length > 1 ? "s" : ""} found<small>${esc(tags.slice(0, 6).join(" · "))}${tags.length > 6 ? ` · +${tags.length - 6} more` : ""}</small>`;
  } else {
    v.className = "verdict ok";
    v.innerHTML = `✓ No leaks found<small>${r.findings.length ? "Only low-risk details (listed below)." : "Nothing identifying in the metadata or hidden layers."} Visual content (faces, text in the picture) isn't checked here; use a deep scan for that.</small>`;
  }
  if (r.errors.length) v.insertAdjacentHTML("beforeend", `<small>Partial scan: ${esc(r.errors.join("; "))}</small>`);

  $("clean").hidden = !r.cleanable || !r.findings.length;
  $("clean").title = r.cleanNote || "";
  show("cleanres", false);

  const facts = Object.entries(r.facts);
  show("gives-h", facts.length > 0);
  $("facts").hidden = !facts.length;
  $("facts").innerHTML = facts.map(([k, vals]) => `<tr><th>${esc(FACT_LABELS[k] || k)}</th><td>${vals.map(esc).join("<br>")}</td></tr>`).join("");

  $("findings").innerHTML = r.findings.length ? r.sorted().map(findingHtml).join("") : `<p class="muted">Nothing found.</p>`;

  const arts = r.artifacts;
  show("artifacts-wrap", arts.length > 0);
  $("artifacts").innerHTML = "";
  for (const a of arts) {
    const url = URL.createObjectURL(new Blob([a.data], { type: a.mime }));
    const img = a.mime.startsWith("image/") ? `<img src="${url}" alt="">` : "";
    $("artifacts").insertAdjacentHTML("beforeend", `<div class="art">${img}<div><div>${esc(a.note || a.name)}</div><a href="${url}" download="${esc(a.name)}">Save ${esc(a.name)}</a> <span class="muted">${fmtBytes(a.data.length)}</span></div></div>`);
  }
}

function download(bytes, name, type) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([bytes], { type: type || "application/octet-stream" }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 30000);
}

$("orig").onclick = () => current && download(current.file.bytes, current.file.name, current.file.type);
$("clean").onclick = async () => {
  if (!current) return;
  const b = $("clean");
  b.disabled = true;
  b.textContent = "Cleaning…";
  try {
    const res = await clean(current.file, current.report);
    if (!res) throw new Error("This file type can't be cleaned in the browser.");
    const before = current.report.leaks(settings.minSeverity).length;
    const after = res.after.leaks(settings.minSeverity);
    const name = current.file.name.replace(/(\.[^.]+)?$/, ".clean$1");
    download(res.bytes, name, res.mime);
    $("cleanres").innerHTML = `<b class="${after.length ? "bad" : "good"}">${before} → ${after.length} leaks</b> · saved <code>${esc(name)}</code> (${fmtBytes(res.bytes.length)}).
      ${res.note === "flattened" ? "Pages were flattened to images so covered text is really gone." : ""}
      ${after.length ? `<br>Still present: ${after.map((f) => esc(f.title)).join(", ")}. ${esc(current.report.cleanNote)}` : ""}`;
    show("cleanres");
  } catch (e) {
    $("cleanres").innerHTML = `<b class="bad">Couldn't clean:</b> ${esc(e.message || e)}`;
    show("cleanres");
  } finally {
    b.disabled = false;
    b.textContent = "Download clean copy";
  }
};

// ---- optional: the full Python scanner running locally (`unredact.tools web`)
async function deepAvailable() {
  const base = (settings.deepScanUrl || "").replace(/\/$/, "");
  if (!base) return;
  try {
    const r = await fetch(`${base}/api/info`, { signal: AbortSignal.timeout(1200) });
    const info = await r.json();
    $("deep").hidden = false;
    $("deep").title = `unredact.tools ${info.version} at ${base}${info.ocr ? " · OCR" : ""}${info.vision ? " · vision" : ""}`;
  } catch { /* not running */ }
}
$("deep").onclick = async () => {
  const base = settings.deepScanUrl.replace(/\/$/, "");
  const b = $("deep");
  b.disabled = true;
  b.textContent = "Deep scanning…";
  try {
    const fd = new FormData();
    fd.append("file", new Blob([current.file.bytes], { type: current.file.type || "application/octet-stream" }), current.file.name);
    const r = await fetch(`${base}/api/scan`, { method: "POST", body: fd });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    const seen = new Set(current.report.findings.map((f) => f.title.toLowerCase()));
    const extra = (d.findings || []).filter((f) => !seen.has(String(f.title).toLowerCase()));
    $("deep-note").textContent = `${base} found ${d.findings?.length ?? 0} items, ${extra.length} not caught in the browser (visual checks, OCR, printer dots, camera fingerprint…).`;
    $("deepfindings").innerHTML = extra.sort((a, b2) => RANK[b2.severity] - RANK[a.severity]).map(findingHtml).join("");
    show("deep-wrap");
  } catch (e) {
    $("deep-note").textContent = `Deep scan failed: ${e.message || e}`;
    show("deep-wrap");
  } finally {
    b.disabled = false;
    b.textContent = "Deep scan with local unredact.tools";
  }
};

// ---- local file picker / drop
const zone = $("zone");
const take = async (f) => run({ name: f.name, type: f.type, bytes: new Uint8Array(await f.arrayBuffer()) });
$("file").onchange = (e) => e.target.files[0] && take(e.target.files[0]);
zone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("file").click(); } });
for (const t of ["dragenter", "dragover"]) document.addEventListener(t, (e) => { e.preventDefault(); zone.classList.add("over"); });
document.addEventListener("dragleave", () => zone.classList.remove("over"));
document.addEventListener("drop", (e) => { e.preventDefault(); zone.classList.remove("over"); const f = e.dataTransfer.files[0]; if (f) take(f); });
$("pick").onclick = (e) => { e.preventDefault(); $("file").value = ""; $("file").click(); };

// ---- start
(async () => {
  settings = await getSettings();
  if (params.get("error")) return fail(esc(params.get("error")));
  if (params.get("token")) {
    const b = await chrome.runtime.sendMessage({ op: "takeBlob", token: params.get("token") });
    if (b?.error) return fail("That file is no longer available. Right-click it again.");
    return run({ name: nameFrom(b.url, b.disposition, b.type), type: b.type, bytes: fromB64(b.b64), source: null });
  }
  if (params.get("url")) return fromUrl(params.get("url"));
  show("drop");
})();
