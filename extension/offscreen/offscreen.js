// Hosts the scan engine for content scripts (they can't run pdf.js workers or DOMParser safely
// inside arbitrary pages). Files stay here between "scan" and "clean" so they're sent only once.
import { scan, clean, summarize } from "../engine/index.js";
import { toB64, fromB64 } from "../engine/common.js";

const held = new Map(); // id -> {file, report, at}

setInterval(() => {
  const cutoff = Date.now() - 15 * 60 * 1000;
  for (const [id, h] of held) if (h.at < cutoff) held.delete(id);
}, 60 * 1000);

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg?.target !== "engine") return false;
  (async () => {
    if (msg.op === "scan") {
      const out = [];
      for (const f of msg.files) {
        const file = { name: f.name, type: f.type, bytes: fromB64(f.b64) };
        const report = await scan(file);
        const id = crypto.randomUUID();
        held.set(id, { file, report, at: Date.now() });
        out.push({ id, ...summarize(report, msg.min || "medium") });
      }
      return { reports: out };
    }
    if (msg.op === "clean") {
      const out = [];
      for (const id of msg.ids) {
        const h = held.get(id);
        if (!h) { out.push({ id, error: "expired" }); continue; }
        try {
          const c = await clean(h.file, h.report);
          if (!c) out.push({ id, error: "can't clean this type" });
          else out.push({ id, b64: toB64(c.bytes), mime: c.mime, note: c.note, after: summarize(c.after, msg.min || "medium") });
        } catch (e) {
          out.push({ id, error: String(e?.message || e) });
        }
      }
      return { results: out };
    }
    if (msg.op === "release") { for (const id of msg.ids || []) held.delete(id); return { ok: true }; }
    return { error: "unknown op" };
  })().then(reply, (e) => reply({ error: String(e?.message || e) }));
  return true;
});
