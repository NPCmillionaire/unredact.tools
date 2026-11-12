"""Image scanner + sanitizer (JPEG, PNG, WebP, TIFF, GIF, HEIC*).

*HEIC/HEIF/AVIF needs `pip install pillow-heif`.
"""
from __future__ import annotations

import io

from PIL import ExifTags, Image, ImageCms, ImageOps, IptcImagePlugin

from .common import scan_strings
from .model import Report
from .xmp import find_xmp, scan_xmp

try:  # optional HEIC support
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
except Exception:  # noqa: BLE001
    pass

Image.MAX_IMAGE_PIXELS = 400_000_000

TAGS = ExifTags.TAGS
GPSTAGS = ExifTags.GPSTAGS

# EXIF tag name -> (bucket, severity, label)
EXIF_INTEREST = {
    "Make": ("device", "medium", "Camera / phone make"),
    "Model": ("device", "medium", "Camera / phone model"),
    "BodySerialNumber": ("device", "high", "Camera body serial number"),
    "LensSerialNumber": ("device", "high", "Lens serial number"),
    "LensMake": ("device", "low", "Lens make"),
    "LensModel": ("device", "low", "Lens model"),
    "CameraOwnerName": ("identity", "high", "Camera owner name"),
    "Artist": ("identity", "high", "Artist"),
    "Copyright": ("identity", "medium", "Copyright"),
    "HostComputer": ("device", "medium", "Host computer"),
    "Software": ("software", "low", "Software"),
    "ImageDescription": ("hidden", "medium", "Image description"),
    "UserComment": ("hidden", "medium", "User comment"),
    "XPAuthor": ("identity", "high", "Windows author"),
    "XPComment": ("hidden", "medium", "Windows comment"),
    "XPSubject": ("hidden", "low", "Windows subject"),
    "XPKeywords": ("hidden", "low", "Windows keywords"),
    "ImageUniqueID": ("tracking", "medium", "Unique image ID"),
    "DateTimeOriginal": ("time", "medium", "Taken at"),
    "DateTimeDigitized": ("time", "low", "Digitized at"),
    "DateTime": ("time", "low", "Last modified"),
    "OffsetTimeOriginal": ("location", "medium", "Timezone offset (narrows region)"),
    "OffsetTime": ("location", "low", "Timezone offset"),
}

IPTC_FIELDS = {
    (2, 80): ("identity", "high", "By-line (creator)"),
    (2, 85): ("identity", "medium", "By-line title"),
    (2, 90): ("location", "high", "City"),
    (2, 92): ("location", "high", "Sub-location"),
    (2, 95): ("location", "high", "State / province"),
    (2, 101): ("location", "high", "Country"),
    (2, 116): ("identity", "medium", "Copyright"),
    (2, 120): ("hidden", "medium", "Caption"),
    (2, 122): ("identity", "medium", "Caption writer"),
    (2, 25): ("hidden", "low", "Keywords"),
    (2, 55): ("time", "low", "Date created"),
    (2, 118): ("identity", "high", "Contact"),
}

# PNG/info keys Pillow sets that are not user metadata
BENIGN_INFO = {"dpi", "gamma", "transparency", "aspect", "jfif", "jfif_version",
               "jfif_unit", "jfif_density", "progressive", "progression", "adobe",
               "adobe_transform", "interlace", "srgb", "duration", "loop", "background",
               "version", "compression", "resolution", "chromaticity", "mp", "exif",
               "icc_profile", "xmp", "XML:com.adobe.xmp", "photoshop", "comment",
               "mpoffset", "mpentry", "frame_rate", "timestamp", "lossless"}


def _decode(v):
    if isinstance(v, bytes):
        if v[:8] in (b"ASCII\x00\x00\x00", b"UNICODE\x00"):
            v = v[8:]
        try:
            # XP* tags are UTF-16LE
            if len(v) > 2 and v[1:2] == b"\x00":
                return v.decode("utf-16-le").rstrip("\x00").strip()
            return v.decode("utf-8").rstrip("\x00").strip()
        except UnicodeDecodeError:
            return f"<{len(v)} bytes binary>"
    if isinstance(v, tuple):
        return ", ".join(_decode(x) for x in v)
    return str(v).strip()


def _dms(vals, ref):
    d, m, s = (float(x) for x in vals)
    deg = d + m / 60 + s / 3600
    return -deg if ref in ("S", "W") else deg


def _scan_gps(report: Report, gps: dict, source):
    g = {GPSTAGS.get(k, k): v for k, v in gps.items()}
    try:
        if "GPSLatitude" in g and "GPSLongitude" in g:
            lat = _dms(g["GPSLatitude"], _decode(g.get("GPSLatitudeRef", "N")))
            lon = _dms(g["GPSLongitude"], _decode(g.get("GPSLongitudeRef", "E")))
            if not (lat == 0 and lon == 0):
                url = f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}"
                detail = f"{lat:.6f}, {lon:.6f}  ->  {url}"
                if "GPSHPositioningError" in g:
                    detail += f"  (accuracy ~{float(g['GPSHPositioningError']):.0f} m)"
                report.add("critical", "location", "Exact GPS coordinates", detail, source)
                report.fact("location", f"GPS {lat:.6f}, {lon:.6f} ({url})")
    except Exception as e:  # noqa: BLE001
        report.errors.append(f"GPS decode: {e}")
    if "GPSAltitude" in g:
        try:
            alt = float(g["GPSAltitude"])
            below = g.get("GPSAltitudeRef") in (1, b"\x01")
            report.add("medium", "location", "Altitude",
                       f"{'-' if below else ''}{alt:.1f} m (can reveal floor of a building)", source)
        except Exception:  # noqa: BLE001
            pass
    if "GPSImgDirection" in g:
        report.add("medium", "location", "Compass direction camera faced",
                   f"{float(g['GPSImgDirection']):.0f} deg", source)
    if "GPSSpeed" in g:
        report.add("medium", "location", "Speed at capture",
                   f"{float(g['GPSSpeed']):.1f} {_decode(g.get('GPSSpeedRef', 'K'))}", source)
    if "GPSDateStamp" in g:
        report.add("low", "time", "GPS (UTC) date stamp", _decode(g["GPSDateStamp"]), source)


def _thumbnail(exif_bytes: bytes, exif) -> bytes | None:
    try:
        ifd1 = exif.get_ifd(ExifTags.IFD.IFD1)
        off, ln = ifd1.get(0x0201), ifd1.get(0x0202)
        if off and ln:
            tiff = exif_bytes[6:] if exif_bytes.startswith(b"Exif") else exif_bytes
            data = tiff[off:off + ln]
            if data.startswith(b"\xff\xd8"):
                return data
    except Exception:  # noqa: BLE001
        pass
    return None


def _trailing(raw: bytes, fmt: str) -> bytes:
    if fmt == "PNG":
        i = raw.rfind(b"IEND")
        return raw[i + 8:] if i != -1 else b""
    if fmt == "JPEG":  # MPO legitimately stores extra frames after EOI
        end = _jpeg_end(raw)
        return raw[end:] if end else b""
    return b""


def _jpeg_end(raw: bytes) -> int | None:
    """Walk JPEG markers to find the real end of the primary image (EOI)."""
    i, n = 2, len(raw)
    while i < n - 1:
        if raw[i] != 0xFF:
            return None
        m = raw[i + 1]
        if m == 0xFF:
            i += 1
            continue
        if m == 0xD9:
            return i + 2
        if 0xD0 <= m <= 0xD7 or m == 0x01:
            i += 2
            continue
        seglen = int.from_bytes(raw[i + 2:i + 4], "big")
        i += 2 + seglen
        if m == 0xDA:  # entropy-coded data follows; skip to next real marker
            while True:
                j = raw.find(b"\xff", i)
                if j == -1 or j >= n - 1:
                    return None
                nb = raw[j + 1]
                if nb == 0 or 0xD0 <= nb <= 0xD7 or nb == 0xFF:
                    i = j + 1 if nb == 0xFF else j + 2
                    continue
                i = j
                break
    return None


def scan_image(path_or_bytes, report: Report | None = None, label="image") -> Report:
    raw = path_or_bytes if isinstance(path_or_bytes, bytes) else open(path_or_bytes, "rb").read()
    report = report or Report(label, "image")
    try:
        img = Image.open(io.BytesIO(raw))
    except Exception as e:  # noqa: BLE001
        report.errors.append(f"cannot open image: {e}")
        return report
    fmt = img.format or "?"
    report.filetype = f"image/{fmt.lower()}"
    report.add("info", "structure", f"{fmt} image {img.width}x{img.height}", img.mode, "file")

    # ---------------- EXIF
    exif = img.getexif()
    exif_bytes = img.info.get("exif", b"")
    if exif:
        merged = {TAGS.get(k, k): v for k, v in exif.items()}
        for ifd_id in (ExifTags.IFD.Exif,):
            try:
                merged.update({TAGS.get(k, k): v for k, v in exif.get_ifd(ifd_id).items()})
            except Exception:  # noqa: BLE001
                pass
        report.add("info", "structure", f"EXIF block with {len(merged)} tags", "", "EXIF")
        for name, (bucket, sev, lbl) in EXIF_INTEREST.items():
            if name in merged:
                val = _decode(merged[name])
                if not val or val.startswith("<"):
                    continue
                report.add(sev, bucket, lbl, val, "EXIF")
                if bucket in ("device", "identity"):
                    report.fact(bucket, f"{lbl}: {val}")
                if name == "DateTimeOriginal":
                    tz = _decode(merged.get("OffsetTimeOriginal", ""))
                    report.fact("time", f"photo taken {val}{' UTC' + tz if tz else ''}")
                if name == "Software":
                    report.fact("software", val)
                scan_strings(report, val, "EXIF")
        if "MakerNote" in merged:
            mn = merged["MakerNote"]
            report.add("medium", "device", "Vendor MakerNote blob",
                       f"{len(mn) if isinstance(mn, bytes) else '?'} bytes of proprietary data "
                       "(often serials, shutter count, internal IDs, Apple 'content identifier')",
                       "EXIF")
        try:
            gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
            if gps:
                _scan_gps(report, gps, "EXIF GPS")
        except Exception:  # noqa: BLE001
            pass
        thumb = _thumbnail(exif_bytes, exif)
        if thumb:
            note = "Embedded EXIF thumbnail"
            sev = "low"
            try:
                t = Image.open(io.BytesIO(thumb))
                r_main = img.width / img.height
                r_thumb = t.width / t.height
                # orientation tag may swap axes on the main image but not on the thumb
                mismatch = min(abs(r_main - r_thumb), abs(1 / r_main - r_thumb)) / r_thumb > 0.05
                if mismatch:
                    sev = "high"
                    note += (" has a DIFFERENT aspect ratio than the image - it likely shows the "
                             "original, uncropped photo")
                elif "Software" in merged:
                    sev = "medium"
                    note += " may predate edits made in software"
            except Exception:  # noqa: BLE001
                pass
            report.add(sev, "hidden-content", note, f"{len(thumb)} bytes (extracted)", "EXIF IFD1")
            report.artifact("exif_thumbnail.jpg", thumb, note)
            if sev == "high":
                report.fact("hidden", "thumbnail of the uncropped original")

    # ---------------- XMP / IPTC / comments / text chunks
    xmp = img.info.get("xmp") or img.info.get("XML:com.adobe.xmp") or find_xmp(raw)
    if isinstance(xmp, str):
        xmp = xmp.encode()
    if xmp:
        scan_xmp(report, xmp, "XMP")
    try:
        iptc = IptcImagePlugin.getiptcinfo(img) or {}
    except Exception:  # noqa: BLE001
        iptc = {}
    for key, (bucket, sev, lbl) in IPTC_FIELDS.items():
        if key in iptc:
            val = _decode(iptc[key]) if not isinstance(iptc[key], list) else \
                ", ".join(_decode(x) for x in iptc[key])
            report.add(sev, bucket, lbl, val, "IPTC")
            if bucket in ("identity", "location"):
                report.fact(bucket, f"{lbl}: {val}")
    if img.info.get("comment"):
        c = _decode(img.info["comment"])
        report.add("medium", "hidden-content", "Embedded comment", c[:300], "JPEG COM")
        scan_strings(report, c, "JPEG COM")
    for k, v in img.info.items():
        if k in BENIGN_INFO or not isinstance(v, (str, bytes)):
            continue
        val = _decode(v)
        sev = "high" if k.lower() in ("parameters", "prompt", "workflow") else "medium"
        lbl = "AI-generation prompt/settings" if sev == "high" else f"Text chunk '{k}'"
        report.add(sev, "hidden-content", lbl, val[:300], f"{fmt} text")
        scan_strings(report, val, f"{fmt} text")
    icc = img.info.get("icc_profile")
    if icc:
        try:
            desc = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).strip()
        except Exception:  # noqa: BLE001
            desc = f"{len(icc)} bytes"
        report.add("low", "device", "ICC color profile", f"{desc} (can hint at device/display)", "ICC")

    # ---------------- extra frames / appended data
    n_frames = getattr(img, "n_frames", 1)
    if fmt == "MPO" and n_frames > 1:
        report.add("medium", "hidden-content", f"{n_frames - 1} extra embedded image(s)",
                   "Multi-Picture Format: depth map / HDR gain map / second view", "MPO")
        for i in range(1, n_frames):
            try:
                img.seek(i)
                buf = io.BytesIO()
                img.convert("RGB").save(buf, "JPEG")
                report.artifact(f"embedded_frame_{i}.jpg", buf.getvalue(), "MPO frame")
            except Exception:  # noqa: BLE001
                pass
        img.seek(0)
    tail = _trailing(raw, fmt)
    if len(tail.strip(b"\x00")) > 16:
        is_video = b"ftyp" in tail[:16]
        report.add("critical" if is_video else "high", "hidden-content",
                   "Embedded video (motion photo)" if is_video else "Data appended after end of image",
                   f"{len(tail)} bytes" + (" - a few seconds of video + audio around the shot"
                                           if is_video else " - could be another file hidden inside"),
                   "trailer")
        report.artifact("appended.mp4" if is_video else "appended.bin", tail)
        report.fact("hidden", "embedded motion-photo video" if is_video else "appended hidden data")
    return report


# ====================================================================== sanitize
SAVE_FMT = {"JPEG": "JPEG", "MPO": "JPEG", "PNG": "PNG", "WEBP": "WEBP", "TIFF": "TIFF",
            "GIF": "PNG", "BMP": "PNG", "HEIF": "JPEG", "AVIF": "JPEG"}
EXT = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "TIFF": ".tif"}


def sanitize_image_bytes(raw: bytes, keep_format=True) -> tuple[bytes, str]:
    """Rebuild image from pixels only. Returns (bytes, extension)."""
    img = Image.open(io.BytesIO(raw))
    fmt = img.format or "PNG"
    img.seek(0)
    img = ImageOps.exif_transpose(img)  # bake rotation in before dropping EXIF
    icc = img.info.get("icc_profile")
    if img.mode == "P":
        img = img.convert("RGBA" if "transparency" in img.info else "RGB")
    if img.mode not in ("RGB", "RGBA", "L", "LA", "I;16", "CMYK"):
        img = img.convert("RGBA" if "A" in img.mode else "RGB")
    if icc and img.mode in ("RGB", "RGBA"):
        try:  # convert colours into sRGB so dropping the profile doesn't shift them
            img = ImageCms.profileToProfile(img, ImageCms.ImageCmsProfile(io.BytesIO(icc)),
                                            ImageCms.createProfile("sRGB"),
                                            outputMode=img.mode)
        except Exception:  # noqa: BLE001
            pass
    clean = Image.frombytes(img.mode, img.size, img.tobytes())  # fresh object: zero metadata
    out_fmt = SAVE_FMT.get(fmt, "PNG") if keep_format else "PNG"
    if out_fmt == "JPEG" and clean.mode not in ("RGB", "L", "CMYK"):
        clean = clean.convert("RGB")
    buf = io.BytesIO()
    kw = {}
    if out_fmt == "JPEG":
        kw = {"quality": 95, "optimize": True}
    elif out_fmt == "WEBP":
        kw = {"quality": 95}
    elif out_fmt == "TIFF":
        kw = {"compression": "tiff_lzw"}
    clean.save(buf, out_fmt, **kw)
    return buf.getvalue(), EXT.get(out_fmt, ".png")
