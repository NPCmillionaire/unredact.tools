"""Synthetic images that exercise the visual-leak detectors. Usage: python make_vision_fixtures.py OUTDIR"""
import io
import os
import sys

import numpy as np
import zxingcpp
from PIL import Image, ImageDraw, ImageFont

out = sys.argv[1]
os.makedirs(out, exist_ok=True)
P = lambda n: os.path.join(out, n)  # noqa: E731
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def font(size):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default()


BCBP = ("M1DOE/JANE            EABC123 CLEPHXAA 1234 316Y012A0045 100")


def barcode(fmt, text, scale=4):
    bc = zxingcpp.create_barcode(text, getattr(zxingcpp.BarcodeFormat, fmt))
    img = zxingcpp.write_barcode_to_image(bc, scale=scale)
    return Image.fromarray(np.asarray(img)).convert("RGB")


# 1. "screenshot" of a chat/email with sensitive text + a boarding-pass barcode
shot = Image.new("RGB", (1100, 760), (250, 250, 252))
d = ImageDraw.Draw(shot)
d.rectangle([0, 0, 1100, 48], fill=(32, 33, 36))
d.text((16, 12), "Inbox - jane.doe@example.com - Mail", font=font(20), fill=(230, 230, 230))
lines = [
    "From: Jane Doe <jane.doe@example.com>",
    "Call me at (330) 555-0142 when you land.",
    "Ship it to 1420 Market Street, Youngstown OH",
    "SSN for the form: 123-45-6789",
    "Card: 4111 1111 1111 1111",
    "Server: 10.0.4.22  key AKIAIOSFODNN7EXAMPLE",
    "Docs: https://intranet.example.com/hr/payroll",
]
for i, t in enumerate(lines):
    d.text((24, 80 + i * 42), t, font=font(24), fill=(20, 20, 20))
bp = barcode("Aztec", BCBP, scale=5)
shot.paste(bp, (800, 420))
d.text((800, 395), "Boarding pass", font=font(18), fill=(80, 80, 80))
shot.save(P("screenshot.png"))

# 2. QR code linking somewhere + plain photo-ish background
qr = Image.new("RGB", (900, 700), (120, 160, 120))
qr.paste(barcode("QRCode", "https://wa.me/13305550142?text=hi", scale=8), (300, 180))
qr.save(P("qr_photo.jpg"), quality=90)

# 3. pixelated (mosaic) text - the reversible kind
pix = Image.new("RGB", (900, 300), "white")
d = ImageDraw.Draw(pix)
d.text((30, 40), "password: hunter2-secret", font=font(40), fill="black")
d.text((30, 150), "Account 0047-2231-9981", font=font(40), fill="black")
region = pix.crop((20, 30, 620, 220))
b = 12
small = region.resize((region.width // b, region.height // b), Image.BOX)
pix.paste(small.resize(region.size, Image.NEAREST), (20, 30))
pix.save(P("pixelated.jpg"), quality=92)

# 4. scanned page with printer tracking dots (faint yellow, repeating grid)
W, H = 2480, 3508  # A4 @ 300 dpi
page = Image.new("RGB", (W, H), (252, 252, 250))
d = ImageDraw.Draw(page)
for i in range(40):
    d.text((200, 300 + i * 70), "Lorem ipsum dolor sit amet, consectetur adipiscing elit sed do "
           "eiusmod tempor.", font=font(34), fill=(30, 30, 30))
rng = np.random.default_rng(7)
pattern = rng.random((8, 15)) > 0.55  # 15x8 DocuColor-style matrix
arr = np.asarray(page).copy()
step, tile_w, tile_h = 8, 15 * 8, 8 * 8
for ty in range(0, H - tile_h, 256):
    for tx in range(0, W - tile_w, 512):
        for r in range(8):
            for c in range(15):
                if pattern[r, c] or r == 0 or c == 0:
                    y, x = ty + r * step, tx + c * step
                    arr[y:y + 2, x:x + 2] = (250, 245, 190)
noise = rng.normal(0, 2, arr.shape)
Image.fromarray(np.clip(arr + noise, 0, 255).astype(np.uint8)).save(P("scan_with_dots.png"))

# 5. two "cameras" for PRNU: fixed multiplicative sensor pattern per camera
def camera(seed):
    return np.random.default_rng(seed).normal(0, 0.008, (900, 1200)).astype(np.float32)


def shoot(K, seed):
    r = np.random.default_rng(seed)
    base = r.normal(0, 1, (45, 60))
    scene = np.kron(base, np.ones((20, 20)))
    import cv2
    scene = cv2.GaussianBlur(scene.astype(np.float32), (0, 0), 12)
    scene = 60 + 140 * (scene - scene.min()) / (np.ptp(scene) + 1e-6)
    img = scene * (1 + K) + r.normal(0, 2.5, scene.shape)
    rgb = np.clip(np.stack([img] * 3, -1), 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "JPEG", quality=85)
    return buf.getvalue()


KA, KB = camera(1), camera(2)
for i in range(4):
    open(P(f"camA_{i}.jpg"), "wb").write(shoot(KA, 100 + i))
open(P("camA_test.jpg"), "wb").write(shoot(KA, 999))
open(P("camB_test.jpg"), "wb").write(shoot(KB, 998))
print("vision fixtures in", out)
