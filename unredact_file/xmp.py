"""Generic XMP packet analysis (used by images and PDFs)."""
from __future__ import annotations

import re

from lxml import etree

from .common import scan_strings
from .model import Report

XMP_RX = re.compile(rb"<x:xmpmeta.*?</x:xmpmeta>", re.S)

# localname -> (bucket, severity, label)
INTERESTING = {
    "creator": ("identity", "high", "Author"),
    "Artist": ("identity", "high", "Artist"),
    "Rights": ("identity", "medium", "Rights / copyright"),
    "Owner": ("identity", "high", "Owner"),
    "CameraOwnerName": ("identity", "high", "Camera owner"),
    "PersonInImage": ("identity", "high", "Person tagged in image"),
    "Name": ("identity", "high", "Name (face/region tag)"),
    "CreatorTool": ("software", "low", "Creator tool"),
    "softwareAgent": ("software", "low", "Edit history: software"),
    "History": ("software", "medium", "Edit history"),
    "DocumentID": ("tracking", "low", "Document ID (links copies/versions)"),
    "OriginalDocumentID": ("tracking", "medium", "Original document ID (links to source file)"),
    "InstanceID": ("tracking", "low", "Instance ID"),
    "DerivedFrom": ("tracking", "medium", "Derived-from reference"),
    "City": ("location", "high", "City"),
    "State": ("location", "high", "State / province"),
    "Country": ("location", "high", "Country"),
    "Location": ("location", "high", "Location"),
    "Sublocation": ("location", "high", "Sub-location"),
    "GPSLatitude": ("location", "critical", "GPS latitude"),
    "GPSLongitude": ("location", "critical", "GPS longitude"),
    "CreateDate": ("time", "low", "Created"),
    "ModifyDate": ("time", "low", "Modified"),
    "DateCreated": ("time", "low", "Date created"),
    "SerialNumber": ("device", "high", "Device serial number"),
    "LensSerialNumber": ("device", "high", "Lens serial number"),
    "description": ("hidden", "medium", "Description"),
    "title": ("hidden", "low", "Title"),
    "subject": ("hidden", "low", "Keywords"),
    "RawFileName": ("identity", "medium", "Original raw filename"),
    "OriginalFileName": ("identity", "medium", "Original filename"),
}


def find_xmp(raw: bytes) -> bytes | None:
    m = XMP_RX.search(raw)
    return m.group(0) if m else None


def _text(el):
    parts = [t.strip() for t in el.itertext() if t and t.strip()]
    return " | ".join(parts)


def scan_xmp(report: Report, packet: bytes, source="XMP"):
    try:
        root = etree.fromstring(packet, etree.XMLParser(recover=True, huge_tree=True))
    except Exception as e:  # noqa: BLE001
        report.errors.append(f"XMP parse failed: {e}")
        return
    if root is None:
        return
    report.add("info", "structure", "XMP metadata packet present", f"{len(packet)} bytes", source)
    seen = set()

    def handle(local, value):
        if local not in INTERESTING or not value:
            return
        key = (local, value)
        if key in seen:
            return
        seen.add(key)
        bucket, sev, label = INTERESTING[local]
        report.add(sev, bucket, label, value[:300], source)
        if bucket in ("identity", "location", "device", "software"):
            report.fact(bucket, f"{label}: {value[:100]}")

    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        local = etree.QName(el).localname
        # Attribute-style properties: <rdf:Description xmp:CreatorTool="...">
        for k, v in el.attrib.items():
            handle(etree.QName(k).localname, v.strip())
        if local in INTERESTING and local != "Name":
            handle(local, _text(el))
        elif local == "Name" and el.text and el.text.strip():
            handle(local, el.text.strip())
    scan_strings(report, packet.decode("utf-8", "replace"), source)
