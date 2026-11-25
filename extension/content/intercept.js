// "Clean before send": holds back file uploads (picker, drag-and-drop) on protected sites,
// scans them locally, and lets the person choose to upload a cleaned copy or the original.
(() => {
  if (window.__unredactIntercept) return;
  window.__unredactIntercept = true;

  const SUPPORTED = /\.(jpe?g|png|webp|gif|tiff?|heic|heif|pdf|docx|docm|xlsx|xlsm|pptx|pptm|txt|md|csv|tsv|json|mp4|m4v|mov|3gp|m4a)$/i;
  const released = new WeakSet(); // events we re-dispatched ourselves
  const pending = new WeakSet(); // inputs currently being checked
  let cfg = { minSeverity: "medium", autoClean: false, maxMB: 25, quietWhenClean: false };
  const loadCfg = () => chrome.storage.sync.get(cfg).then((s) => (cfg = { ...cfg, ...s })).catch(() => {});
  loadCfg();
  chrome.storage.onChanged.addListener(loadCfg);

  const relevant = (files) => files.some((f) => SUPPORTED.test(f.name) && f.size <= cfg.maxMB * 1048576);

  // ---------------------------------------------------------------- file picker
  // composedPath() hides nodes inside *closed* shadow roots, so the event looks like it came from
  // the host element. Look inside the host for the file input whose selection just changed.
  const lastFiles = new WeakMap();
  const isFileInput = (n) => n instanceof HTMLInputElement && n.type === "file";
  function findInShadow(host, depth = 0) {
    const sr = chrome.dom?.openOrClosedShadowRoot?.(host) || host.shadowRoot;
    if (!sr || depth > 6) return null;
    for (const n of sr.querySelectorAll("*")) {
      if (isFileInput(n) && n.files?.length && lastFiles.get(n) !== n.files) return n;
      const inner = findInShadow(n, depth + 1);
      if (inner) return inner;
    }
    return null;
  }
  function onInput(e) {
    if (released.has(e)) return;
    let el = e.composedPath()[0];
    if (!isFileInput(el) && el instanceof Element) el = findInShadow(el) || el;
    if (!isFileInput(el)) return;
    lastFiles.set(el, el.files);
    if (pending.has(el)) { e.stopImmediatePropagation(); return; }
    const files = [...(el.files || [])];
    if (!files.length || !relevant(files)) return;
    e.stopImmediatePropagation();
    pending.add(el);
    // "change" isn't composed: inside a shadow root it never reaches window, so stop it at the root
    const root = el.getRootNode();
    const stopper = (ev) => { if (!released.has(ev) && ev.composedPath()[0] === el && pending.has(el)) ev.stopImmediatePropagation(); };
    if (root !== document) root.addEventListener("change", stopper, true);
    check(files, (out) => {
      pending.delete(el);
      if (root !== document) root.removeEventListener("change", stopper, true);
      if (!out) { try { el.value = ""; } catch {} return; }
      if (out !== files) {
        const dt = new DataTransfer();
        out.forEach((f) => dt.items.add(f));
        el.files = dt.files;
      }
      for (const [type, composed] of [["input", true], ["change", false]]) {
        const ev = new Event(type, { bubbles: true, composed });
        released.add(ev);
        el.dispatchEvent(ev);
      }
    });
  }
  window.addEventListener("input", onInput, true);
  window.addEventListener("change", onInput, true);

  // ---------------------------------------------------------------- drag and drop
  window.addEventListener("drop", (e) => {
    if (released.has(e)) return;
    const dt = e.dataTransfer;
    if (!dt || !dt.types.includes("Files")) return;
    if ([...dt.items].some((i) => i.webkitGetAsEntry?.()?.isDirectory)) return; // folders: leave alone
    const files = [...dt.files];
    if (!files.length || !relevant(files)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    const target = e.composedPath()[0];
    const init = { bubbles: true, cancelable: true, composed: true, clientX: e.clientX, clientY: e.clientY,
      screenX: e.screenX, screenY: e.screenY, shiftKey: e.shiftKey, ctrlKey: e.ctrlKey, altKey: e.altKey, metaKey: e.metaKey };
    const where = () => (target.isConnected ? target : document.elementFromPoint(e.clientX, e.clientY) || document.body);
    check(files, (out) => {
      const ndt = new DataTransfer();
      (out || []).forEach((f) => ndt.items.add(f));
      const seq = out ? ["dragenter", "dragover", "drop"] : ["dragleave"];
      for (const t of seq) {
        const ev = new DragEvent(t, { ...init, dataTransfer: ndt });
        released.add(ev);
        where().dispatchEvent(ev);
      }
    });
  }, true);

  // ---------------------------------------------------------------- engine
  const send = (msg) => chrome.runtime.sendMessage(msg).then((r) => {
    if (r?.error) throw new Error(r.error);
    return r;
  });

  async function b64(file) {
    const u8 = new Uint8Array(await file.arrayBuffer());
    let s = "";
    for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
    return btoa(s);
  }
  function fromB64(s) {
    const bin = atob(s);
    const u8 = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    return u8;
  }

  async function check(files, done) {
    let finished = false;
    const finish = (out) => { if (!finished) { finished = true; done(out); } };
    const t = toast();
    t.checking(files.length, () => { t.close(); finish(files); });

    try {
      const targets = files.map((f, i) => ({ f, i })).filter(({ f }) => SUPPORTED.test(f.name) && f.size <= cfg.maxMB * 1048576);
      await send({ op: "ensureEngine" });
      const payload = await Promise.all(targets.map(async ({ f }) => ({ name: f.name, type: f.type, b64: await b64(f) })));
      const { reports } = await send({ target: "engine", op: "scan", files: payload, min: cfg.minSeverity });
      if (finished) return;
      targets.forEach((tg, k) => (tg.report = reports[k]));
      const leaky = targets.filter((tg) => tg.report.leaks > 0);
      const ids = targets.map((tg) => tg.report.id);
      const release = () => send({ target: "engine", op: "release", ids }).catch(() => {});

      if (!leaky.length) {
        cfg.quietWhenClean ? t.close() : t.ok("No leaks found", `${targets.length} file${targets.length > 1 ? "s" : ""} checked locally.`);
        release();
        return finish(files);
      }

      const doClean = async () => {
        t.working("Cleaning…");
        const cleanable = leaky.filter((tg) => tg.report.cleanable);
        const { results } = await send({ target: "engine", op: "clean", ids: cleanable.map((tg) => tg.report.id), min: cfg.minSeverity });
        const out = [...files];
        let before = 0, after = 0;
        const failed = [];
        cleanable.forEach((tg, k) => {
          const r = results[k];
          before += tg.report.leaks;
          if (r.error) { failed.push(`${tg.f.name}: ${r.error}`); after += tg.report.leaks; return; }
          after += r.after.leaks;
          out[tg.i] = new File([fromB64(r.b64)], tg.f.name, { type: r.mime || tg.f.type, lastModified: Date.now() });
        });
        const skipped = leaky.filter((tg) => !tg.report.cleanable);
        skipped.forEach((tg) => { before += tg.report.leaks; after += tg.report.leaks; });
        release();
        finish(out);
        const extra = [...failed, ...skipped.map((tg) => `${tg.f.name}: ${tg.report.cleanNote || "can't be cleaned here"}`)];
        t.ok(after ? `Cleaned: ${before} → ${after} leaks` : `Cleaned: ${before} → 0 leaks`,
          after ? `Uploaded the cleaned copies. Still present: ${extra.join("; ") || "see report"}` : "Uploaded the cleaned copies.", !!after);
      };

      if (cfg.autoClean) return doClean().catch((e) => { t.error(e, () => finish(files), () => finish(null)); });

      t.leaks(leaky, {
        clean: () => doClean().catch((e) => t.error(e, () => finish(files), () => finish(null))),
        original: () => { t.close(); release(); finish(files); },
        cancel: () => { t.close(); release(); finish(null); },
      });
    } catch (e) {
      if (!finished) t.error(e, () => { t.close(); finish(files); }, () => { t.close(); finish(null); });
    }
  }

  // ---------------------------------------------------------------- toast UI (closed shadow DOM)
  const CSS = `
  :host { all: initial; }
  .box { position: fixed; right: 20px; bottom: 20px; z-index: 2147483647; width: min(400px, calc(100vw - 32px));
    background: #151518; color: #f2efe9; border: 1px solid #2a2a30; border-radius: 12px; box-shadow: 0 12px 40px rgba(0,0,0,.45);
    font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; padding: 14px 16px; box-sizing: border-box;
    animation: in .16s ease-out; }
  @keyframes in { from { transform: translateY(8px); opacity: 0; } }
  .brand { font: 600 11px ui-monospace, "SF Mono", Menlo, monospace; color: #6b6862; letter-spacing: .02em; margin-bottom: 6px; display: flex; justify-content: space-between; }
  .brand b { color: #ff8a1f; font-weight: 600; }
  .x { all: unset; cursor: pointer; color: #9b978f; font-size: 16px; line-height: 1; padding: 0 2px; }
  .x:hover { color: #f2efe9; }
  .title { font-weight: 650; font-size: 15px; margin: 0 0 4px; }
  .warn .title { color: #ff5a4a; }
  .good .title { color: #4ade80; }
  .muted { color: #9b978f; font-size: 13px; }
  .files { margin: 8px 0 2px; max-height: 210px; overflow: auto; }
  details { border-top: 1px solid #2a2a30; padding: 6px 0; }
  summary { cursor: pointer; font-size: 13px; list-style: none; display: flex; gap: 8px; align-items: baseline; }
  summary::-webkit-details-marker { display: none; }
  summary .n { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px; }
  ul { margin: 6px 0 2px; padding: 0; list-style: none; }
  li { font-size: 12.5px; margin: 3px 0; display: flex; gap: 6px; }
  li span:last-child { color: #c9c5bd; overflow-wrap: anywhere; }
  .sev { flex: none; font: 600 10px ui-monospace, Menlo, monospace; text-transform: uppercase; padding: 1px 5px; border-radius: 4px; height: fit-content; margin-top: 2px; }
  .critical { background: #c4271b; color: #fff; } .high { background: rgba(255,90,74,.18); color: #ff5a4a; }
  .medium { background: rgba(240,180,41,.16); color: #f0b429; } .low { background: rgba(79,195,217,.14); color: #4fc3d9; }
  .row { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
  button.b { all: unset; box-sizing: border-box; cursor: pointer; border-radius: 8px; padding: 8px 12px; font-weight: 600; font-size: 13px; text-align: center; }
  button.b:focus-visible { outline: 2px solid #ff8a1f; outline-offset: 2px; }
  .primary { background: #ff8a1f; color: #111; flex: 1; } .primary:hover { background: #ff9d45; }
  .ghost { border: 1px solid #2a2a30; color: #f2efe9; } .ghost:hover { background: #1c1c20; }
  .link { color: #9b978f; font-size: 12px; padding: 8px 4px; } .link:hover { color: #f2efe9; }
  .note { font-size: 12px; color: #9b978f; margin-top: 6px; }
  .spin { display: inline-block; width: 12px; height: 12px; border: 2px solid #2a2a30; border-top-color: #ff8a1f; border-radius: 50%; animation: s .7s linear infinite; vertical-align: -2px; margin-right: 6px; }
  @keyframes s { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { .box, .spin { animation: none; } }
  `;

  let current = null;
  function toast() {
    current?.close();
    const host = document.createElement("unredact-toast");
    const root = host.attachShadow({ mode: "closed" });
    root.innerHTML = `<style>${CSS}</style><div class="box" role="alertdialog" aria-live="assertive" aria-label="unredact.tools upload check"></div>`;
    const box = root.querySelector(".box");
    (document.body || document.documentElement).appendChild(host);
    let timer = null;
    const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
    const head = (onClose) => {
      box.innerHTML = `<div class="brand"><span><b>unredact</b>.tools · clean before send</span><button class="x" aria-label="Close">×</button></div>`;
      box.querySelector(".x").onclick = onClose || api.close;
    };
    const api = {
      close() { clearTimeout(timer); host.remove(); if (current === api) current = null; },
      checking(n, skip) {
        head(skip);
        box.className = "box";
        box.insertAdjacentHTML("beforeend", `<div class="title"><span class="spin"></span>Checking ${n} file${n > 1 ? "s" : ""} for leaks…</div>
          <div class="muted">Scanned on this device. Nothing is uploaded until you choose.</div>
          <div class="row"><button class="b link" data-a="skip">Skip check and upload</button></div>`);
        box.querySelector('[data-a="skip"]').onclick = skip;
      },
      working(msg) {
        box.querySelector(".row")?.remove();
        const t = box.querySelector(".title");
        if (t) t.innerHTML = `<span class="spin"></span>${esc(msg)}`;
      },
      ok(title, note, warn = false) {
        head();
        box.className = "box " + (warn ? "warn" : "good");
        box.insertAdjacentHTML("beforeend", `<div class="title">${warn ? "⚠️" : "✓"} ${esc(title)}</div><div class="muted">${esc(note)}</div>`);
        timer = setTimeout(api.close, warn ? 9000 : 3500);
      },
      error(e, original, cancel) {
        head(cancel);
        box.className = "box warn";
        box.insertAdjacentHTML("beforeend", `<div class="title">Couldn't check this upload</div><div class="muted">${esc(e?.message || e)}</div>
          <div class="row"><button class="b ghost" data-a="o">Upload original</button><button class="b link" data-a="c">Cancel upload</button></div>`);
        box.querySelector('[data-a="o"]').onclick = () => { api.close(); original(); };
        box.querySelector('[data-a="c"]').onclick = () => { api.close(); cancel(); };
      },
      leaks(leaky, act) {
        head(act.cancel);
        box.className = "box warn";
        const total = leaky.reduce((a, t) => a + t.report.leaks, 0);
        const tags = [...new Set(leaky.flatMap((t) => t.report.tags))];
        const shown = tags.slice(0, 3).join(", ") + (tags.length > 3 ? ` +${tags.length - 3}` : "");
        const files = leaky.map((t) => {
          const items = t.report.findings.filter((f) => ["critical", "high", "medium", "low"].indexOf(f.severity) <= ["critical", "high", "medium", "low"].indexOf(cfg.minSeverity)).slice(0, 8);
          return `<details${leaky.length === 1 ? " open" : ""}><summary><span class="n">${esc(t.f.name)}</span><span class="muted">${t.report.leaks} leak${t.report.leaks > 1 ? "s" : ""}</span></summary>
            <ul>${items.map((f) => `<li><span class="sev ${f.severity}">${f.severity === "critical" ? "crit" : f.severity}</span><span><b>${esc(f.title)}</b>${f.detail ? " · " + esc(f.detail.replace(/ → https?:\S+/, "").slice(0, 140)) : ""}</span></li>`).join("")}</ul>
            ${t.report.cleanNote ? `<div class="note">${t.report.cleanable ? "Clean: " : "⚠ "}${esc(t.report.cleanNote)}</div>` : ""}</details>`;
        }).join("");
        const anyCleanable = leaky.some((t) => t.report.cleanable);
        box.insertAdjacentHTML("beforeend", `<div class="title">⚠️ ${total} leak${total > 1 ? "s" : ""} found (${esc(shown)})</div>
          <div class="files">${files}</div>
          <div class="row">${anyCleanable ? `<button class="b primary" data-a="clean">Clean &amp; Upload</button>` : ""}
          <button class="b ghost" data-a="orig">Upload Original</button><button class="b link" data-a="cancel">Cancel</button></div>`);
        box.querySelector('[data-a="clean"]')?.addEventListener("click", act.clean);
        box.querySelector('[data-a="orig"]').onclick = act.original;
        box.querySelector('[data-a="cancel"]').onclick = act.cancel;
        (box.querySelector('[data-a="clean"]') || box.querySelector('[data-a="orig"]')).focus({ preventScroll: true });
      },
    };
    current = api;
    return api;
  }
})();
