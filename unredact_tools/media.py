"""Audio/video via ffprobe/ffmpeg (optional system dependency)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

from .common import scan_strings
from .model import Report

LOC_KEYS = ("location", "location-eng", "com.apple.quicktime.location.iso6709",
            "com.android.capture.location", "xyz")
DEV_KEYS = ("com.apple.quicktime.make", "com.apple.quicktime.model", "make", "model",
            "com.android.manufacturer", "com.android.model")
ID_KEYS = ("artist", "author", "album_artist", "composer", "copyright", "encoded_by",
           "com.apple.quicktime.author", "com.apple.quicktime.artist")
SW_KEYS = ("encoder", "com.apple.quicktime.software", "com.android.version")
NOISE = ("language", "vendor_id", "minor_version", "compatible_brands", "duration", "rotate",
         "handler_name", "major_brand")


def have_ffmpeg():
    return bool(shutil.which("ffprobe") and shutil.which("ffmpeg"))


def _iso6709(s):
    m = re.match(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)?", s)
    return (float(m.group(1)), float(m.group(2))) if m else None


def scan_media(path, report: Report | None = None) -> Report:
    report = report or Report(path, "media")
    if not have_ffmpeg():
        report.errors.append("ffprobe/ffmpeg not installed - install ffmpeg for audio/video support")
        return report
    out = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format",
                          "-show_streams", path], capture_output=True, text=True)
    try:
        info = json.loads(out.stdout or "{}")
    except json.JSONDecodeError:
        report.errors.append("ffprobe returned no data")
        return report
    fmt = info.get("format", {})
    report.filetype = f"media/{fmt.get('format_name', '?')}"
    streams = info.get("streams", [])
    report.add("info", "structure", f"{fmt.get('format_long_name', '')}",
               ", ".join(f"{s.get('codec_type')}:{s.get('codec_name')}" for s in streams), "container")
    tagsets = [("container", fmt.get("tags", {}))] + \
              [(f"stream {s.get('index')} ({s.get('codec_type')})", s.get("tags", {})) for s in streams]
    data_streams = [s for s in streams if s.get("codec_type") == "data"]
    if data_streams:
        report.add("medium", "hidden-content", f"{len(data_streams)} timed-metadata/data track(s)",
                   "Can hold per-frame GPS, gyro, camera settings (e.g. Apple mebx, GoPro GPMF)",
                   "streams")
    for src, tags in tagsets:
        for k, v in tags.items():
            kl = k.lower()
            if kl in LOC_KEYS:
                ll = _iso6709(v)
                url = f" -> https://www.google.com/maps?q={ll[0]},{ll[1]}" if ll else ""
                report.add("critical", "location", "Recording location", v + url, src)
                report.fact("location", f"recorded at {v}{url}")
            elif kl in DEV_KEYS:
                report.add("medium", "device", k, v, src)
                report.fact("device", v)
            elif kl in ID_KEYS:
                report.add("high", "identity", k, v, src)
                report.fact("identity", f"{k}: {v}")
            elif kl == "creation_time":
                report.add("low", "time", "Creation time", v, src)
                report.fact("time", f"recorded {v}")
            elif kl in SW_KEYS:
                report.add("low", "software", k, v, src)
            elif kl not in NOISE:
                report.add("medium" if len(v) > 3 else "low", "hidden-content", f"Tag '{k}'",
                           v[:200], src)
            scan_strings(report, v, src)
    return report


def sanitize_media(path, out_path):
    if not have_ffmpeg():
        raise RuntimeError("ffmpeg not installed")
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", path,
           "-map", "0:v?", "-map", "0:a?", "-map", "0:s?",  # drops data/GPS tracks
           "-map_metadata", "-1", "-map_metadata:s", "-1", "-map_chapters", "-1",
           "-c", "copy", "-fflags", "+bitexact", "-flags:v", "+bitexact",
           "-flags:a", "+bitexact"]
    if out_path.lower().endswith((".mp4", ".mov", ".m4a", ".m4v")):
        cmd += ["-movflags", "+faststart"]
    subprocess.run(cmd + [out_path], check=True)
    return out_path
