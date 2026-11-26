// Service worker: context menu, content-script registration, offscreen engine lifecycle.
import { getSettings, activeMatches } from "./shared/settings.js";

const MENU = "unredact-inspect";
const SCRIPT_IDS = ["ur-shim", "ur-intercept"];
const blobs = new Map(); // token -> {b64, name, type, url}

// ------------------------------------------------------------------ setup
chrome.runtime.onInstalled.addListener(async ({ reason }) => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: MENU,
      title: "Inspect with unredact.tools",
      contexts: ["image", "link", "video", "audio", "page", "frame"],
    });
  });
  await syncScripts();
  if (reason === "install") chrome.tabs.create({ url: chrome.runtime.getURL("options/options.html?welcome=1") });
});
chrome.runtime.onStartup.addListener(syncScripts);
chrome.storage.onChanged.addListener((c, area) => {
  if (area === "sync" && (c.sites || c.custom)) syncScripts();
});
// custom sites are optional host permissions: re-register when the user grants or revokes one
chrome.permissions.onAdded.addListener(syncScripts);
chrome.permissions.onRemoved.addListener(syncScripts);

// serialize: install, startup, storage and permission events can fire together, and overlapping
// unregister/register calls fail with duplicate script IDs
let syncing = Promise.resolve();
function syncScripts() {
  syncing = syncing.then(doSyncScripts, doSyncScripts);
  return syncing;
}

async function doSyncScripts() {
  const matches = [];
  for (const m of activeMatches(await getSettings())) {
    if (await chrome.permissions.contains({ origins: [m] }).catch(() => false)) matches.push(m);
  }
  const existing = (await chrome.scripting.getRegisteredContentScripts({ ids: SCRIPT_IDS })).map((s) => s.id);
  if (existing.length) await chrome.scripting.unregisterContentScripts({ ids: existing });
  if (!matches.length) return;
  await chrome.scripting.registerContentScripts([
    { id: "ur-shim", js: ["content/shim.js"], matches, runAt: "document_start", allFrames: true, world: "MAIN" },
    { id: "ur-intercept", js: ["content/intercept.js"], matches, runAt: "document_start", allFrames: true },
  ]);
}

// ------------------------------------------------------------------ context menu
const FILEISH = /\.(pdf|jpe?g|png|webp|gif|tiff?|heic|heif|docx?|xlsx?|pptx?|txt|csv|md|mp4|mov|m4v)(?:$|[?#])/i;

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== MENU) return;
  const url = info.srcUrl || info.linkUrl || info.frameUrl || info.pageUrl;
  if (!url) return;
  const q = new URLSearchParams({ tab: String(tab?.id ?? ""), frame: String(info.frameId ?? 0) });
  if (/^(blob|data):/.test(url)) {
    // page-scoped URLs: only the page itself can read them
    try {
      const got = await fetchInTab(tab.id, info.frameId ?? 0, url);
      const token = crypto.randomUUID();
      blobs.set(token, got);
      q.set("token", token);
    } catch (e) {
      q.set("error", `Couldn't read that ${url.slice(0, 4)}: URL from the page (${e.message || e}).`);
    }
  } else {
    q.set("url", url);
    if (!info.srcUrl && !info.linkUrl && !FILEISH.test(url)) q.set("page", "1");
  }
  chrome.tabs.create({ url: chrome.runtime.getURL(`report/report.html?${q}`), index: (tab?.index ?? 0) + 1, openerTabId: tab?.id });
});

async function fetchInTab(tabId, frameId, url) {
  const [res] = await chrome.scripting.executeScript({
    target: { tabId, frameIds: [frameId] },
    args: [url],
    func: async (u) => {
      const r = await fetch(u, { credentials: "include" });
      if (!r.ok) return { error: `HTTP ${r.status}` };
      const b = new Uint8Array(await r.arrayBuffer());
      let s = "";
      for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
      const cd = r.headers.get("content-disposition") || "";
      return { b64: btoa(s), type: r.headers.get("content-type") || "", disposition: cd };
    },
  });
  if (!res?.result || res.result.error) throw new Error(res?.result?.error || "no result");
  return { ...res.result, url };
}

// ------------------------------------------------------------------ offscreen engine
let creating = null;
async function ensureEngine() {
  const ctx = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"] });
  if (ctx.length) return;
  creating ||= chrome.offscreen.createDocument({
    url: "offscreen/offscreen.html",
    reasons: ["DOM_PARSER", "BLOBS", "WORKERS"],
    justification: "Scan and clean files locally (PDF rendering, XML parsing) before they are uploaded.",
  }).finally(() => (creating = null));
  await creating;
}

// ------------------------------------------------------------------ messages
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg?.target === "engine") return false; // handled by the offscreen document
  (async () => {
    switch (msg?.op) {
      case "ensureEngine": await ensureEngine(); return { ok: true };
      case "takeBlob": { const b = blobs.get(msg.token); blobs.delete(msg.token); return b || { error: "expired" }; }
      case "fetchInTab": return fetchInTab(msg.tabId, msg.frameId, msg.url);
      case "openReport": chrome.tabs.create({ url: chrome.runtime.getURL(`report/report.html${msg.query || ""}`) }); return { ok: true };
      case "openOptions": chrome.runtime.openOptionsPage(); return { ok: true };
      default: return { error: "unknown op" };
    }
  })().then(reply, (e) => reply({ error: String(e?.message || e) }));
  return true;
});
