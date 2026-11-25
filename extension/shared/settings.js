// Settings shared by the service worker, popup, options and report pages.
export const DEFAULT_SITES = [
  { id: "gmail", label: "Gmail", matches: ["https://mail.google.com/*"] },
  { id: "outlook", label: "Outlook", matches: ["https://outlook.live.com/*", "https://outlook.office.com/*", "https://outlook.office365.com/*"] },
  { id: "proton", label: "Proton Mail & Drive", matches: ["https://mail.proton.me/*", "https://drive.proton.me/*"] },
  { id: "slack", label: "Slack", matches: ["https://app.slack.com/*"] },
  { id: "discord", label: "Discord", matches: ["https://discord.com/*"] },
  { id: "x", label: "X / Twitter", matches: ["https://x.com/*", "https://twitter.com/*"] },
  { id: "reddit", label: "Reddit", matches: ["https://www.reddit.com/*", "https://old.reddit.com/*", "https://new.reddit.com/*"] },
  { id: "telegram", label: "Telegram Web", matches: ["https://web.telegram.org/*"] },
  { id: "whatsapp", label: "WhatsApp Web", matches: ["https://web.whatsapp.com/*"] },
  { id: "meta", label: "Facebook & Messenger", matches: ["https://www.facebook.com/*", "https://www.messenger.com/*"] },
  { id: "linkedin", label: "LinkedIn", matches: ["https://www.linkedin.com/*"] },
  { id: "github", label: "GitHub", matches: ["https://github.com/*"] },
  { id: "gdrive", label: "Google Drive & Docs", matches: ["https://drive.google.com/*", "https://docs.google.com/*"] },
  { id: "dropbox", label: "Dropbox", matches: ["https://www.dropbox.com/*"] },
  { id: "onedrive", label: "OneDrive & SharePoint", matches: ["https://onedrive.live.com/*", "https://*.sharepoint.com/*"] },
  { id: "box", label: "Box", matches: ["https://app.box.com/*"] },
];

export const DEFAULTS = {
  sites: Object.fromEntries(DEFAULT_SITES.map((s) => [s.id, true])),
  custom: [], // ["https://example.com/*"]
  minSeverity: "medium", // what counts as a "leak" in the upload toast
  autoClean: false, // clean without asking
  maxMB: 25, // larger files upload untouched
  quietWhenClean: false, // hide the "no leaks" confirmation
  deepScanUrl: "http://127.0.0.1:8000",
};

export async function getSettings() {
  const s = await chrome.storage.sync.get(DEFAULTS);
  s.sites = { ...DEFAULTS.sites, ...s.sites };
  return s;
}

export const setSettings = (patch) => chrome.storage.sync.set(patch);

export function activeMatches(s) {
  const m = DEFAULT_SITES.filter((x) => s.sites[x.id]).flatMap((x) => x.matches);
  return [...new Set([...m, ...s.custom])];
}

// Does this URL fall under one of the given match patterns? (scheme://host/path with * wildcards)
export function matchesUrl(patterns, url) {
  let u;
  try { u = new URL(url); } catch { return false; }
  return patterns.some((p) => {
    const m = /^(\*|https?):\/\/([^/]+)(\/.*)$/.exec(p);
    if (!m) return false;
    const [, scheme, host, path] = m;
    if (scheme !== "*" && scheme + ":" !== u.protocol) return false;
    const hostRx = new RegExp("^" + host.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/^\*\\\./, "(?:[^.]+\\.)*").replace(/\*/g, "[^.]*") + "$");
    const pathRx = new RegExp("^" + path.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*") + "$");
    return hostRx.test(u.hostname) && pathRx.test(u.pathname + u.search);
  });
}

export const siteFor = (url) => DEFAULT_SITES.find((s) => matchesUrl(s.matches, url));
