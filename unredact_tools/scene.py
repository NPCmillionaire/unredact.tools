"""Scene analysis with a vision-language model (opt-in).

The detectors in vision.py catch things with a fixed shape: text, codes, faces,
plates, dot grids. What they can't do is *reason* about a scene the way an OSINT
analyst does: a reflection in sunglasses, a distinctive skyline, the brand of power
outlet, a school logo on a hoodie, the direction of shadows. That needs a model.

Providers:
  ollama     (default) a local model via Ollama - the image never leaves your machine
  anthropic  Claude via the Anthropic API - the image IS uploaded; needs ANTHROPIC_API_KEY

Nothing here runs unless the user passes --scene.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import urllib.error
import urllib.request

from PIL import Image, ImageOps

DEFAULT_MODELS = {"ollama": "qwen3-vl:8b", "anthropic": "claude-sonnet-5-5"}

PROMPT = """You are an OPSEC reviewer. Someone is about to post this image publicly and wants \
to know what an adversary (stalker, doxxer, OSINT investigator) could learn from it.

Look for anything that reveals WHO is involved, WHERE it was taken, or WHEN:
- reflections (sunglasses, windows, mirrors, screens, glossy surfaces) showing people or places
- landmarks, skylines, distinctive buildings, mountains, coastlines
- street signs, house numbers, shop names, transit maps, license plates, vehicle details
- regional clues: power outlets, road markings, side of the road, vegetation, architecture, language
- documents, screens, badges, mail, packages, prescription labels, school/company logos
- people, children, uniforms; interior details that identify a home or workplace
- sun and shadows: estimate the direction shadows fall and the time of day

Reply with ONLY a JSON object, no prose:
{"clues": [{"type": "reflection|landmark|signage|plate|vehicle|regional|document|screen|logo|person|interior|shadow|other",
            "what": "short description of what is visible",
            "reveals": "what an adversary learns from it",
            "severity": "critical|high|medium|low",
            "box": [x, y, w, h]}],
 "location_guess": {"place": "most specific plausible place, or unknown", "confidence": "low|medium|high", "why": "short"},
 "time_guess": "time of day / season if inferable, else unknown"}
"box" is optional, in pixels of the image you were given. Report only things actually visible. \
If nothing is revealing, return {"clues": [], "location_guess": {"place": "unknown", \
"confidence": "low", "why": ""}, "time_guess": "unknown"}."""


class SceneError(RuntimeError):
    pass


def _prepare(raw: bytes, max_side=1568) -> tuple[str, float]:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    scale = min(1.0, max_side / max(img.size))
    if scale < 1:
        img = img.resize((round(img.width * scale), round(img.height * scale)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)  # re-encoded: no metadata goes to the model
    return base64.b64encode(buf.getvalue()).decode(), scale


def _post(url, payload, headers, timeout=180):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise SceneError(f"{url}: HTTP {e.code} {e.read().decode(errors='replace')[:300]}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SceneError(f"{url}: {e}")


def _ollama(b64, model):
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    if not host.startswith("http"):
        host = "http://" + host
    try:
        resp = _post(f"{host}/api/chat", {
            "model": model, "stream": False, "format": "json",
            "options": {"temperature": 0.1},
            "messages": [{"role": "user", "content": PROMPT, "images": [b64]}],
        }, {})
    except SceneError as e:
        if "refused" in str(e) or "Errno 61" in str(e) or "Errno 111" in str(e):
            raise SceneError(f"Ollama isn't running at {host}. Install it from ollama.com, then "
                             f"`ollama pull {model}` (or set OLLAMA_HOST)") from None
        if "not found" in str(e):
            raise SceneError(f"model {model} isn't pulled: run `ollama pull {model}`") from None
        raise
    return (resp.get("message") or {}).get("content", "")


def _anthropic(b64, model):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SceneError("ANTHROPIC_API_KEY is not set")
    resp = _post("https://api.anthropic.com/v1/messages", {
        "model": model, "max_tokens": 2000,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
            {"type": "text", "text": PROMPT}]}],
    }, {"x-api-key": key, "anthropic-version": "2023-06-01"})
    return "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")


def parse(text: str) -> dict:
    """Pull the JSON object out of a model reply (tolerates code fences and chatter)."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise SceneError("model reply had no JSON")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise SceneError(f"model reply was not valid JSON: {e}")
    data.setdefault("clues", [])
    return data


def analyze(raw: bytes, provider="ollama", model=None) -> dict:
    if provider not in DEFAULT_MODELS:
        raise SceneError(f"unknown provider {provider!r} (use ollama or anthropic)")
    model = model or os.environ.get("UNREDACT_SCENE_MODEL") or DEFAULT_MODELS[provider]
    b64, scale = _prepare(raw)
    text = _ollama(b64, model) if provider == "ollama" else _anthropic(b64, model)
    data = parse(text)
    # map boxes back to original-image pixels
    for c in data["clues"]:
        box = c.get("box")
        if isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
            c["box"] = [round(v / scale) for v in box]
        else:
            c.pop("box", None)
    data["provider"], data["model"] = provider, model
    return data


BUCKET = {"reflection": "identity", "person": "identity", "logo": "identity", "document": "identity",
          "screen": "identity", "plate": "identity", "vehicle": "identity",
          "landmark": "location", "signage": "location", "regional": "location",
          "interior": "location", "shadow": "location"}


def add_findings(report, data, source="scene AI"):
    for c in data.get("clues", []):
        sev = c.get("severity", "medium")
        sev = sev if sev in ("critical", "high", "medium", "low") else "medium"
        typ = str(c.get("type", "other"))
        bucket = BUCKET.get(typ, "hidden-content")
        box = c.get("box")
        where = f" at x={box[0]} y={box[1]} ({box[2]}x{box[3]})" if box else ""
        report.add(sev, bucket, f"Scene: {typ} - {c.get('what', '')}"[:120],
                   f"{c.get('reveals', '')}{where}", source)
        report.fact("location" if bucket == "location" else "identity" if bucket == "identity"
                    else "hidden", f"{typ}: {c.get('what', '')}"[:90])
    g = data.get("location_guess") or {}
    if g.get("place") and str(g.get("place")).lower() != "unknown":
        conf = g.get("confidence", "low")
        report.add({"high": "critical", "medium": "high"}.get(conf, "medium"), "location",
                   f"Scene: location guess ({conf} confidence) - {g['place']}",
                   g.get("why", ""), source)
        report.fact("location", f"AI location guess: {g['place']} ({conf})")
    t = data.get("time_guess")
    if t and str(t).lower() != "unknown":
        report.add("low", "time", "Scene: time guess", str(t), source)
