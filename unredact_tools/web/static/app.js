"use strict";
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const SEVS = ["critical", "high", "medium", "low", "info"];
  const LABELS = {
    location: "WHERE", identity: "WHO", device: "DEVICE", time: "WHEN",
    software: "SOFTWARE", network: "INTERNAL / ORG", hidden: "HIDDEN CONTENT",
    tracking: "TRACKING / PHONE-HOME",
  };
  // finding title fragment -> sanitize option that removes it
  const HINTS = {
    "Readable in the image": "redact_text", "Boarding pass": "redact_codes",
    "QR Code": "redact_codes", "face(s) visible": "blur_faces", "License plate": "redact_plates",
  };
  const OPT_NAMES = {
    redact_text: "Black out sensitive text", redact_codes: "Black out codes",
    blur_faces: "Blur faces", redact_plates: "Black out license plates",
  };
  let info = { max_mb: 50 };
  const queue = [];
  let running = 0;
  const PARALLEL = 2;

  const el = (tag, props = {}, ...kids) => {
    const n = document.createElement(tag);
    Object.assign(n, props);
    for (const k of kids) if (k != null) n.append(k);
    return n;
  };
  const fmtBytes = (b) => b < 1024 ? `${b} B` : b < 1048576 ? `${(b / 1024).toFixed(1)} KB` : `${(b / 1048576).toFixed(1)} MB`;
  const b64Blob = (b64, type) => {
    const bin = atob(b64), u = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
    return new Blob([u], { type: type || "application/octet-stream" });
  };
  // text -> nodes with http(s) URLs turned into links (never innerHTML)
  const linkify = (text) => {
    const frag = document.createDocumentFragment();
    let last = 0;
    for (const m of text.matchAll(/https?:\/\/[^\s)]+/g)) {
      frag.append(text.slice(last, m.index));
      frag.append(el("a", { href: m[0], textContent: m[0], target: "_blank", rel: "noopener noreferrer" }));
      last = m.index + m[0].length;
    }
    frag.append(text.slice(last));
    return frag;
  };

  async function api(path, form) {
    const res = await fetch(path, { method: "POST", body: form });
    let data;
    try { data = await res.json(); } catch { data = {}; }
    if (!res.ok) throw new Error(data.detail || `server error ${res.status}`);
    return data;
  }

  // ---------- intake ----------
  const drop = $("#drop"), pick = $("#pick");
  drop.addEventListener("click", () => pick.click());
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick.click(); } });
  pick.addEventListener("change", () => { addFiles(pick.files); pick.value = ""; });
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));
  document.addEventListener("dragover", (e) => e.preventDefault());
  document.addEventListener("drop", (e) => { if (!drop.contains(e.target)) { e.preventDefault(); addFiles(e.dataTransfer.files); } });

  function addFiles(list) {
    for (const f of list) {
      const card = makeCard(f);
      $("#results").prepend(card.root);
      if (f.size > info.max_mb * 1048576) { card.fail(`over the ${info.max_mb} MB limit`); continue; }
      if (f.size === 0) { card.fail("empty file"); continue; }
      queue.push(card);
    }
    pump();
  }
  function pump() {
    while (running < PARALLEL && queue.length) {
      const card = queue.shift();
      running++;
      card.scan().finally(() => { running--; pump(); });
    }
  }

  // ---------- card ----------
  function makeCard(file) {
    const root = $("#card-tpl").content.firstElementChild.cloneNode(true);
    const status = $(".status", root), statusText = $(".status-text", root), body = $(".body", root);
    $(".fname", root).textContent = file.name;
    $(".fname", root).title = file.name;
    $(".meta", root).textContent = fmtBytes(file.size);
    let report = null;

    const setStatus = (msg) => { status.hidden = false; status.classList.remove("err"); statusText.textContent = msg; };
    const fail = (msg) => { status.hidden = false; status.classList.add("err"); statusText.textContent = `✕ ${msg}`; };

    async function scan() {
      setStatus($("#opt-fast").checked ? "Reading metadata…" : "Scanning metadata, pixels and hidden content…");
      const form = new FormData();
      form.append("file", file);
      form.append("fast", $("#opt-fast").checked);
      form.append("extract", $("#opt-extract").checked);
      try {
        report = await api("/api/scan", form);
      } catch (e) { fail(e.message); return; }
      status.hidden = true;
      render(report);
    }

    function render(r) {
      $(".meta", root).textContent = `${r.filetype} · ${fmtBytes(r.size)} · sha256 ${r.sha256.slice(0, 12)}…`;
      const real = SEVS.slice(0, 4).filter((s) => r.counts[s]);
      const counts = $(".counts", root);
      counts.replaceChildren(...(real.length
        ? real.map((s) => el("span", { className: `chip ${s}`, textContent: `${r.counts[s]} ${s}` }))
        : [el("span", { className: "chip clean", textContent: "no leaks" })]));

      const n = real.reduce((a, s) => a + r.counts[s], 0);
      const verdict = $(".verdict", root);
      if (r.filetype === "unknown") verdict.textContent = "Unsupported file type. Nothing to scan.";
      else if (!n) verdict.replaceChildren(el("b", { textContent: "No leaks found." }), " The file doesn't carry identifying metadata or hidden content we know how to detect.");
      else verdict.replaceChildren(el("b", { textContent: `${n} thing${n > 1 ? "s" : ""} in this file could identify you or reveal more than you meant to share.` }));

      // facts
      const dl = $(".facts", root);
      dl.replaceChildren();
      for (const [k, label] of Object.entries(LABELS)) {
        const vals = r.facts[k];
        if (!vals || !vals.length) continue;
        dl.append(el("dt", { textContent: label }), el("dd", {}, ...vals.map((v) => el("div", {}, linkify(v)))));
      }
      $(".gives", root).hidden = !dl.children.length;

      // findings
      const ol = $(".findings", root);
      ol.replaceChildren(...r.findings.map((f) => {
        const det = f.detail ? el("pre", { className: "f-detail", textContent: f.detail }) : null;
        const li = el("li", { className: `finding ${f.severity}` },
          el("span", { className: `sev ${f.severity}`, textContent: f.severity }),
          el("div", {}, el("span", { className: "f-title", textContent: f.title }),
            f.source ? el("span", { className: "f-src", textContent: f.source }) : null, det));
        if (det) requestAnimationFrame(() => {
          if (det.scrollHeight > det.clientHeight + 2) {
            const more = el("button", { className: "f-more", type: "button", textContent: "show all" });
            more.onclick = () => { det.classList.add("open"); more.remove(); };
            det.after(more);
          }
        });
        return li;
      }));
      const extra = [...ol.children].filter((li) => !li.classList.contains("info")).slice(8);
      if (extra.length) {
        extra.forEach((li) => (li.hidden = true));
        const more = el("button", { className: "btn ghost more", type: "button", textContent: `Show ${extra.length} more findings` });
        more.onclick = () => { extra.forEach((li) => (li.hidden = false)); more.remove(); };
        ol.after(more);
      }
      const toggle = $(".show-info", root);
      toggle.onchange = () => ol.classList.toggle("with-info", toggle.checked);
      if (!n) { toggle.checked = true; ol.classList.add("with-info"); }
      $(".findings-wrap", root).hidden = !r.findings.length;

      // artifacts
      const arts = r.artifacts.filter((a) => a.data);
      $(".artifacts", root).hidden = !r.artifacts.length;
      $(".art-list", root).replaceChildren(...r.artifacts.map((a) => {
        const li = el("li");
        if (a.data) {
          const url = URL.createObjectURL(b64Blob(a.data));
          li.append(el("a", { href: url, download: a.name, textContent: a.name }));
        } else li.append(el("span", { textContent: a.name }));
        li.append(el("small", { textContent: `${fmtBytes(a.bytes)}${a.note ? " · " + a.note : ""}` }));
        return li;
      }));
      if (r.artifacts.length && !arts.length) $(".art-list", root).append(el("li", {}, el("small", { textContent: "tick “Recover artifacts” and rescan to download these" })));

      $(".errors", root).replaceChildren(...r.errors.map((e) => el("li", { textContent: e })));
      setupClean(r);
      body.hidden = false;
    }

    function setupClean(r) {
      const form = $(".clean-form", root);
      const isPdf = r.kind === "pdf", isImg = r.kind === "image";
      const pdfOpts = $(".pdf-only", root), visOpts = $(".visual-only", root);
      pdfOpts.hidden = !isPdf;
      const syncVisual = () => { visOpts.hidden = !(isImg || (isPdf && form.pdf_mode.value === "raster")); };
      syncVisual();
      form.querySelectorAll("input[name=pdf_mode]").forEach((i) => (i.onchange = syncVisual));
      if (r.filetype === "unknown") { $(".clean", root).hidden = true; return; }
      // pre-tick the detectors this file actually needs
      for (const f of r.findings) for (const [k, opt] of Object.entries(HINTS)) {
        if (f.title.includes(k) && form[opt]) { form[opt].checked = true; form[opt].closest("label").classList.add("suggest"); }
      }
      form.onsubmit = (e) => { e.preventDefault(); clean(form); };
    }

    async function clean(form) {
      const btn = $("button[type=submit]", form);
      const out = $(".clean-result", root);
      btn.disabled = true;
      btn.textContent = "Sanitizing…";
      out.hidden = false;
      out.replaceChildren(el("div", { className: "status" }, el("span", { className: "spinner" }), "Rebuilding the file and re-scanning the copy…"));
      const fd = new FormData();
      fd.append("file", file);
      fd.append("pdf_mode", form.pdf_mode.value);
      for (const k of ["blur_faces", "redact_codes", "redact_text", "redact_plates", "desync", "keep_dots"]) fd.append(k, form[k].checked);
      fd.append("regions", form.regions.value);
      try {
        const res = await api("/api/sanitize", fd);
        showClean(res, out, form);
      } catch (e) {
        out.replaceChildren(el("div", { className: "status err", textContent: `✕ ${e.message}` }));
      } finally {
        btn.disabled = false;
        btn.textContent = "Sanitize again";
      }
    }

    function showClean(res, out, form) {
      const url = URL.createObjectURL(b64Blob(res.data, res.mime));
      const kids = [
        el("div", { className: "tally" },
          el("span", { className: "b", textContent: `${res.before} leak${res.before === 1 ? "" : "s"} before` }), " → ",
          el("span", { className: `a${res.after ? "" : " zero"}`, textContent: `${res.after} after` })),
      ];
      if (res.notes.length) kids.push(el("ul", { className: "notes" }, ...res.notes.map((n) => el("li", { textContent: n }))));
      if (res.remaining.length) {
        kids.push(el("ul", { className: "remaining" }, ...res.remaining.map((f) =>
          el("li", {}, el("span", { className: `sev ${f.severity}`, textContent: f.severity }), " ", f.title))));
      }
      if (res.hints.length) {
        res.hints.forEach((h) => { if (form[h]) { form[h].checked = true; form[h].closest("label").classList.add("suggest"); } });
        kids.push(el("p", { className: "hint", textContent: `Still visible in the pixels. Turned on: ${res.hints.map((h) => OPT_NAMES[h] || h).join(", ")}. Sanitize again to remove it.` }));
      }
      kids.push(el("div", { className: "actions" },
        el("a", { className: "btn", href: url, download: res.filename, textContent: `Download ${res.filename}` }),
        el("span", { className: "meta", textContent: fmtBytes(res.size) })));
      out.replaceChildren(...kids);
    }

    return { root, scan, fail };
  }

  // ---------- boot ----------
  fetch("/api/info").then((r) => r.json()).then((i) => {
    info = i;
    $("#ver").textContent = `v${i.version}`;
    if (!i.ffmpeg) $("#supports").textContent = $("#supports").textContent.replace(" · MP4 · MOV · MP3", "");
  }).catch(() => {});
  const tip = () => fetch("/api/tip").then((r) => r.json()).then((t) => { $("#tip").textContent = t.tip; $("#tip-box").hidden = false; }).catch(() => {});
  $("#tip-next").onclick = tip;
  tip();
})();
