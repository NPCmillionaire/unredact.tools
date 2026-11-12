"""Core data model: findings, facts, recovered artifacts."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

SEVERITIES = ["critical", "high", "medium", "low", "info"]

# Fact buckets feed the "what an adversary learns" narrative.
FACT_LABELS = {
    "location": "WHERE",
    "identity": "WHO",
    "device": "DEVICE",
    "time": "WHEN",
    "software": "SOFTWARE",
    "network": "INTERNAL / ORG",
    "hidden": "HIDDEN CONTENT",
    "tracking": "TRACKING / PHONE-HOME",
}


@dataclass
class Finding:
    severity: str
    category: str
    title: str
    detail: str = ""
    source: str = ""  # e.g. "EXIF", "PDF page 3", "word/comments.xml"


@dataclass
class Artifact:
    name: str
    data: bytes
    note: str = ""


@dataclass
class Report:
    path: str
    filetype: str
    size: int = 0
    sha256: str = ""
    findings: list[Finding] = field(default_factory=list)
    facts: dict[str, list[str]] = field(default_factory=dict)
    artifacts: list[Artifact] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add(self, severity, category, title, detail="", source=""):
        if any(f.title == title and f.detail == str(detail) and f.source == source
               for f in self.findings):
            return
        self.findings.append(Finding(severity, category, title, str(detail), source))

    def fact(self, bucket: str, value: str):
        value = str(value).strip()
        if not value:
            return
        lst = self.facts.setdefault(bucket, [])
        if value not in lst:
            lst.append(value)

    def artifact(self, name, data, note=""):
        self.artifacts.append(Artifact(name, data, note))

    def merge(self, other: "Report", prefix: str):
        """Fold a nested report (embedded image, attachment...) into this one."""
        for f in other.findings:
            self.findings.append(
                Finding(f.severity, f.category, f.title, f.detail,
                        f"{prefix} > {f.source}" if f.source else prefix))
        for k, vals in other.facts.items():
            for v in vals:
                self.fact(k, v)
        for a in other.artifacts:
            self.artifact(f"{prefix.replace('/', '_')}__{a.name}", a.data, a.note)
        self.errors.extend(f"{prefix}: {e}" for e in other.errors)

    def sorted_findings(self):
        return sorted(self.findings, key=lambda f: SEVERITIES.index(f.severity))

    def counts(self):
        c = {s: 0 for s in SEVERITIES}
        for f in self.findings:
            c[f.severity] += 1
        return c

    def to_dict(self):
        return {
            "path": self.path,
            "filetype": self.filetype,
            "size": self.size,
            "sha256": self.sha256,
            "counts": self.counts(),
            "facts": self.facts,
            "findings": [asdict(f) for f in self.sorted_findings()],
            "artifacts": [{"name": a.name, "bytes": len(a.data), "note": a.note}
                          for a in self.artifacts],
            "errors": self.errors,
        }
