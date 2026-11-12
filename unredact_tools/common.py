"""Shared detectors: leak patterns in strings, and text canary/watermark tricks."""
from __future__ import annotations

import re
import unicodedata

from .model import Report

# ---------------------------------------------------------------- leak regexes
_LEAKS = [
    ("identity", "Windows username in path",
     re.compile(r"[A-Za-z]:[\\/]+(?:Users|Documents and Settings)[\\/]+([^\\/:*?\"<>|\r\n]+)", re.I)),
    ("identity", "macOS username in path", re.compile(r"/Users/([A-Za-z0-9._-]+)")),
    ("identity", "Linux username in path", re.compile(r"/home/([A-Za-z0-9._-]+)")),
    ("network", "Internal server (UNC path)", re.compile(r"\\\\([A-Za-z0-9._-]+)\\[^\s\"<>]+")),
    ("network", "SharePoint / OneDrive URL",
     re.compile(r"https?://([\w-]+(?:-my)?\.sharepoint\.com)/[^\s\"<>]*", re.I)),
    ("identity", "Email address", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("network", "Private IP address",
     re.compile(r"\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b")),
    ("network", "Local file URL", re.compile(r"file:/{2,3}[^\s\"<>]+", re.I)),
]


def scan_strings(report: Report, text: str, source: str, severity="high"):
    """Look for usernames, internal hosts, emails etc. inside metadata strings."""
    if not text:
        return
    seen = set()
    for bucket, label, rx in _LEAKS:
        for m in rx.finditer(text):
            whole = m.group(0)
            if (label, whole) in seen:
                continue
            seen.add((label, whole))
            val = m.group(1) if m.groups() else whole
            report.add(severity, bucket, label, whole[:200], source)
            if "username" in label:
                report.fact("identity", f"username '{val}'")
            elif label == "Email address":
                report.fact("identity", whole)
            elif bucket == "network":
                report.fact("network", whole[:120])


# ---------------------------------------------------------------- canary traps
ZERO_WIDTH = {
    "​": "ZERO WIDTH SPACE", "‌": "ZWNJ", "‍": "ZWJ",
    "‎": "LRM", "‏": "RLM", "⁠": "WORD JOINER",
    "⁡": "FUNCTION APPLICATION", "⁢": "INVISIBLE TIMES",
    "⁣": "INVISIBLE SEPARATOR", "⁤": "INVISIBLE PLUS",
    "﻿": "BOM / ZWNBSP", "᠎": "MONGOLIAN VOWEL SEP",
    "­": "SOFT HYPHEN",
}
ODD_SPACES = {chr(c) for c in range(0x2000, 0x200B)} | {" ", " ", " ", "　"}
# Tag characters (U+E0000 block) are used for invisible "ASCII smuggling".
TAG_RANGE = range(0xE0000, 0xE0080)

# Common Cyrillic/Greek look-alikes of Latin letters.
CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "ӏ": "l", "һ": "h", "ԛ": "q", "ԝ": "w",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "Х": "X", "Ѕ": "S", "І": "I", "Ј": "J",
    "ο": "o", "α": "a", "ν": "v", "ρ": "p", "ι": "i", "κ": "k",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K",
    "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
})
_WORD = re.compile(r"\w+", re.UNICODE)


def _script(ch):
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return None
    if name.startswith("LATIN"):
        return "latin"
    if name.startswith("CYRILLIC"):
        return "cyrillic"
    if name.startswith("GREEK"):
        return "greek"
    return None


def mixed_script_words(text):
    out = []
    for w in _WORD.findall(text):
        scripts = {_script(c) for c in w if c.isalpha()} - {None}
        if "latin" in scripts and len(scripts) > 1:
            out.append(w)
    return out


def scan_text_canaries(report: Report, text: str, source: str):
    """Detect fingerprinting tricks used to trace who leaked a document."""
    if not text:
        return
    zw = {}
    for ch in text:
        if ch in ZERO_WIDTH:
            zw[ZERO_WIDTH[ch]] = zw.get(ZERO_WIDTH[ch], 0) + 1
    if text.startswith("﻿") and zw.get("BOM / ZWNBSP") == 1:
        zw.pop("BOM / ZWNBSP")  # a single leading BOM is just an encoding marker
    zw.pop("SOFT HYPHEN", None)  # common in normal typesetting; too noisy
    if zw:
        total = sum(zw.values())
        report.add("high" if total > 3 else "medium", "tracking",
                   f"{total} invisible zero-width characters",
                   "Pattern can encode a per-recipient ID (canary trap). " +
                   ", ".join(f"{k}x{v}" for k, v in zw.items()), source)
        report.fact("tracking", f"{total} zero-width chars (possible per-copy watermark)")
    tags = sum(1 for ch in text if ord(ch) in TAG_RANGE)
    if tags:
        hidden = "".join(chr(ord(c) - 0xE0000) for c in text if ord(c) in TAG_RANGE)
        report.add("high", "hidden-content", f"{tags} invisible Unicode tag characters",
                   f"Decoded hidden text: {hidden[:200]!r}", source)
        report.fact("hidden", f"invisible tag-encoded text: {hidden[:80]!r}")
    odd = sum(1 for ch in text if ch in ODD_SPACES)
    if odd > 5:
        report.add("low", "tracking", f"{odd} non-standard space characters",
                   "Varying space widths is a known per-copy watermarking technique "
                   "(also produced innocently by some editors).", source)
    mixed = mixed_script_words(text)
    if mixed:
        ex = ", ".join(sorted(set(mixed))[:8])
        report.add("high", "tracking", f"{len(mixed)} words mix Latin with Cyrillic/Greek look-alikes",
                   f"Homoglyph substitution is used to fingerprint leaked copies. e.g. {ex}", source)
        report.fact("tracking", f"{len(mixed)} homoglyph-substituted words")


def clean_text(text: str) -> str:
    """Remove canary-style fingerprinting from text while keeping content."""
    text = "".join(ch for ch in text if ch not in ZERO_WIDTH and ord(ch) not in TAG_RANGE)
    text = "".join(" " if ch in ODD_SPACES else ch for ch in text)
    return _WORD.sub(lambda m: m.group(0).translate(CONFUSABLES)
                     if mixed_script_words(m.group(0)) else m.group(0), text)
