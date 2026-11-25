import { getSettings, setSettings, matchesUrl, siteFor, activeMatches } from "../shared/settings.js";

const $ = (id) => document.getElementById(id);
const FILEISH = /\.(pdf|jpe?g|png|webp|gif|tiff?|heic|heif|docx|xlsx|pptx|txt|csv|mp4|mov)(?:$|[?#])/i;

const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
const url = tab?.url || "";
let s = await getSettings();
let u = null;
try { u = new URL(url); } catch { /* chrome:// etc. */ }
const web = u && /^https?:$/.test(u.protocol);

function paint() {
  const on = web && matchesUrl(activeMatches(s), url);
  $("host").textContent = web ? u.hostname : "Not a web page";
  $("toggle").checked = !!on;
  $("toggle").disabled = !web;
  $("status").innerHTML = on ? '<span class="status-on">● Clean before send is on</span>' : web ? "Clean before send is off here" : "Uploads can only be checked on websites";
}
paint();

$("toggle").onchange = async (e) => {
  const want = e.target.checked;
  const known = siteFor(url);
  if (known) s.sites[known.id] = want;
  const pat = `${u.protocol}//${u.hostname}/*`;
  s.custom = s.custom.filter((p) => p !== pat);
  if (want && !known) s.custom.push(pat);
  // a toggle-off on a custom site that matched via a wildcard: drop any custom pattern matching it
  if (!want) s.custom = s.custom.filter((p) => !matchesUrl([p], url));
  await setSettings({ sites: s.sites, custom: s.custom });
  paint();
  $("reload").hidden = false;
};

if (web && (FILEISH.test(u.pathname) || /\/pdf|\.pdf/i.test(url))) {
  $("inspect-page").hidden = false;
  $("inspect-page").onclick = () => {
    chrome.tabs.create({ url: chrome.runtime.getURL(`report/report.html?${new URLSearchParams({ url, tab: String(tab.id), frame: "0" })}`) });
    window.close();
  };
}
$("inspect-local").onclick = () => { chrome.tabs.create({ url: chrome.runtime.getURL("report/report.html") }); window.close(); };
$("opts").onclick = (e) => { e.preventDefault(); chrome.runtime.openOptionsPage(); window.close(); };
