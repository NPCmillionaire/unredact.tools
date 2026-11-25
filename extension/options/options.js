import { DEFAULT_SITES, getSettings, setSettings } from "../shared/settings.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
let s = await getSettings();
if (new URLSearchParams(location.search).get("welcome")) $("welcome").hidden = false;

let t;
const save = async (patch) => {
  Object.assign(s, patch);
  await setSettings(patch);
  $("saved").classList.add("on");
  clearTimeout(t);
  t = setTimeout(() => $("saved").classList.remove("on"), 1200);
};

$("sites").innerHTML = DEFAULT_SITES.map((x) => `<div class="item"><div class="lbl">${esc(x.label)}<div class="sub">${esc(x.matches[0].replace(/^https:\/\/|\/\*$/g, ""))}</div></div>
  <label class="switch"><input type="checkbox" data-site="${x.id}" aria-label="${esc(x.label)}" ${s.sites[x.id] ? "checked" : ""}><span></span></label></div>`).join("");
$("sites").addEventListener("change", (e) => {
  const id = e.target.dataset.site;
  if (id) save({ sites: { ...s.sites, [id]: e.target.checked } });
});

function paintCustom() {
  $("custom").innerHTML = s.custom.length
    ? s.custom.map((p, i) => `<div class="item"><div class="lbl" style="font:13px var(--mono)">${esc(p)}</div><button class="rm" data-i="${i}" aria-label="Remove ${esc(p)}">×</button></div>`).join("")
    : `<div class="item"><div class="lbl muted">None yet. Add a site here or use the toolbar popup on any page.</div></div>`;
}
paintCustom();
$("custom").addEventListener("click", async (e) => {
  const i = e.target.dataset.i;
  if (i == null) return;
  await save({ custom: s.custom.filter((_, k) => k !== Number(i)) });
  paintCustom();
});
$("addform").addEventListener("submit", async (e) => {
  e.preventDefault();
  let v = $("newsite").value.trim();
  if (!/^https?:\/\//.test(v)) v = "https://" + v;
  try {
    const u = new URL(v.replace("*.", "wildcard."));
    v = `${u.protocol}//${u.hostname.replace(/^wildcard\./, "*.")}/*`;
  } catch { $("newsite").setCustomValidity("Not a valid site"); $("newsite").reportValidity(); return; }
  if (!s.custom.includes(v)) await save({ custom: [...s.custom, v] });
  $("newsite").value = "";
  paintCustom();
});
$("newsite").addEventListener("input", (e) => e.target.setCustomValidity(""));

for (const k of ["minSeverity", "deepScanUrl", "maxMB"]) {
  $(k).value = s[k];
  $(k).addEventListener("change", (e) => save({ [k]: k === "maxMB" ? Math.max(1, Math.min(45, Number(e.target.value) || 25)) : e.target.value.trim() }));
}
for (const k of ["autoClean", "quietWhenClean"]) {
  $(k).checked = !!s[k];
  $(k).addEventListener("change", (e) => save({ [k]: e.target.checked }));
}
