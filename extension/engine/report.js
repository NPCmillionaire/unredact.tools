// Same shape as unredact_tools/model.py so local deep-scan results can be merged in.
export const SEVERITIES = ["critical", "high", "medium", "low", "info"];
export const RANK = { critical: 4, high: 3, medium: 2, low: 1, info: 0 };
export const FACT_LABELS = {
  location: "WHERE", identity: "WHO", device: "DEVICE", time: "WHEN", software: "SOFTWARE",
  network: "INTERNAL / ORG", hidden: "HIDDEN CONTENT", tracking: "TRACKING",
};

export class Report {
  constructor(name, filetype, size = 0) {
    this.path = name;
    this.filetype = filetype;
    this.size = size;
    this.findings = [];
    this.facts = {};
    this.errors = [];
    this.artifacts = []; // {name, data: Uint8Array, mime, note}
    this.cleanable = false;
    this.cleanNote = "";
  }

  // tag: 1-3 word label used in the upload toast ("GPS", "Author", "Fake redaction")
  add(severity, category, title, detail = "", source = "", tag = "") {
    detail = String(detail ?? "");
    if (this.findings.some((f) => f.title === title && f.detail === detail && f.source === source)) return;
    this.findings.push({ severity, category, title, detail, source, tag: tag || title });
  }

  fact(bucket, value) {
    value = String(value ?? "").trim();
    if (!value) return;
    const l = (this.facts[bucket] ||= []);
    if (!l.includes(value)) l.push(value);
  }

  artifact(name, data, mime = "application/octet-stream", note = "") {
    this.artifacts.push({ name, data, mime, note });
  }

  merge(other, prefix) {
    for (const f of other.findings) {
      this.findings.push({ ...f, source: f.source ? `${prefix} > ${f.source}` : prefix });
    }
    for (const [k, vals] of Object.entries(other.facts)) for (const v of vals) this.fact(k, v);
    for (const a of other.artifacts) this.artifacts.push({ ...a, name: `${prefix.replace(/\//g, "_")}__${a.name}` });
    this.errors.push(...other.errors.map((e) => `${prefix}: ${e}`));
  }

  sorted() {
    return [...this.findings].sort((a, b) => RANK[b.severity] - RANK[a.severity]);
  }

  counts() {
    const c = Object.fromEntries(SEVERITIES.map((s) => [s, 0]));
    for (const f of this.findings) c[f.severity]++;
    return c;
  }

  // findings at or above `min` severity
  leaks(min = "medium") {
    return this.sorted().filter((f) => RANK[f.severity] >= RANK[min]);
  }

  toJSON() {
    return {
      path: this.path, filetype: this.filetype, size: this.size, counts: this.counts(),
      facts: this.facts, findings: this.sorted(), errors: this.errors,
      artifacts: this.artifacts.map((a) => ({ name: a.name, bytes: a.data.length, mime: a.mime, note: a.note })),
      cleanable: this.cleanable, cleanNote: this.cleanNote,
    };
  }
}

// Plain-object version that survives chrome.runtime messaging (no class, no bytes).
export function summarize(r, min = "medium") {
  const j = r.toJSON();
  j.leaks = r.leaks(min).length;
  j.tags = [...new Set(r.leaks(min).map((f) => f.tag))];
  return j;
}
