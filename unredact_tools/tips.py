"""Rotating OPSEC advice shown under the banner (and via `unredact.tools tip`)."""
from __future__ import annotations

import os
import random

TIPS = [
    # files & photos
    "Phones embed GPS in every photo by default. Turn off camera location access, "
    "or strip it before you share.",
    "Cropping a photo doesn't always crop the embedded EXIF thumbnail. The original "
    "can survive inside the file.",
    "A black box drawn in a PDF editor is not a redaction. The text underneath is "
    "still selectable.",
    "PDFs saved incrementally keep every earlier version. 'Save As' or flatten "
    "before you publish.",
    "Word's 'Inspect Document' misses things. Tracked changes, rsids and embedded "
    "images can still leak.",
    "Screenshots leak too: browser tabs, notification banners, the clock, the battery "
    "level and your username in the corner.",
    "Pixelation and blur can be reversed (see Depix). Use solid boxes, applied as "
    "real redactions.",
    "Printers stamp near-invisible yellow tracking dots on color pages. Scans of "
    "printouts can carry them.",
    "Your camera's sensor noise (PRNU) can link photos to one physical device, even "
    "after metadata is stripped.",
    "Reflections in eyes, glasses, windows and screens have identified photographers "
    "and locations.",
    "Boarding-pass barcodes encode your name, booking reference and frequent-flyer "
    "number. Never post them.",
    "Zero-width characters can watermark a document per recipient. Paste leaked text "
    "through a sanitizer first.",
    "Motion photos (iPhone Live, Pixel, Samsung) hide seconds of video and audio "
    "inside the 'still' image.",
    "File names leak too: 'Final_v3_jsmith_DRAFT.docx' tells a story before anyone "
    "opens it.",
    # accounts & identity
    "Reusing a username across sites is the easiest way to get your accounts linked "
    "together.",
    "Use a unique email alias per service (SimpleLogin, Addy, Fastmail, iCloud Hide My "
    "Email). Breaches then can't be linked to you.",
    "Data brokers re-list you after you opt out. Re-check every 60-90 days.",
    "Your phone number is a de facto national ID. Give out a VoIP number instead.",
    "SMS 2FA can be SIM-swapped. Use an authenticator app or hardware key, and set a "
    "carrier port-out PIN.",
    "Old forum posts age badly. Search your own handles regularly, the way an "
    "adversary would.",
    "Your writing style is a fingerprint. Stylometry has unmasked anonymous authors "
    "since the Federalist Papers.",
    "Venmo transactions are public by default. Set them to private and hide your "
    "friends list.",
    "Fitness apps publish run routes that start and end at your front door. Set up "
    "privacy zones.",
    "Property records list your name, address and purchase price. In many states a "
    "land trust or LLC keeps your name off them.",
    "Voter rolls are sold to anyone who asks in many states. Check whether yours has "
    "an address-confidentiality program.",
    # devices & network
    "Your phone broadcasts the names of every Wi-Fi network it remembers. Forget old "
    "networks.",
    "Device names like 'Camerons-iPhone' are visible to everyone on public Wi-Fi. "
    "Rename them.",
    "MAC randomization doesn't hide your hostname, mDNS name or Bluetooth name.",
    "Smart TVs fingerprint what's on screen for ad targeting (ACR). Turn it off in "
    "settings.",
    "A VPN moves trust from your ISP to the VPN company. It doesn't make you "
    "anonymous.",
    "Browser fingerprinting works without cookies. Test yours at coveryourtracks.eff.org.",
    "Link previews in chat apps can leak your IP to whoever controls the link.",
    "Email open-tracking pixels reveal when, where and on what device you read a "
    "message. Block remote images.",
    "Tracking parameters (utm_, fbclid, si=) tie a shared link back to your account. "
    "Strip them before sharing.",
    # mindset
    "OPSEC is about patterns, not single mistakes. Adversaries connect small, "
    "harmless-looking facts.",
    "Before you post, ask what it gives away: where, who, when, and on what device.",
    "Assume anything you upload is permanent. Deletion is a request, not a guarantee.",
    "Compartmentalize: different identities need different emails, browsers, devices "
    "and schedules.",
    "Time zones leak. Posting at 3 AM local time every night is a pattern.",
    "The most common deanonymization is self-disclosure: 'my hometown', 'my "
    "employer', 'my dog's name'.",
]


def random_tip(rng: random.Random | None = None) -> str:
    pinned = os.environ.get("UNREDACT_TIP")  # pin a tip by number (used for the demo GIF)
    if pinned and pinned.isdigit() and 1 <= int(pinned) <= len(TIPS):
        return TIPS[int(pinned) - 1]
    return (rng or random).choice(TIPS)
