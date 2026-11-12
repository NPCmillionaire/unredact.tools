"""Generate deliberately leaky sample files for testing. Usage: python make_fixtures.py OUTDIR"""
import io
import os
import re
import subprocess
import sys
import zipfile

import piexif
import pymupdf
from docx import Document
from docx.shared import Inches
from openpyxl import Workbook
from openpyxl.comments import Comment
from PIL import Image, ImageDraw
from pptx import Presentation

out = sys.argv[1]
os.makedirs(out, exist_ok=True)
P = lambda n: os.path.join(out, n)  # noqa: E731


def leaky_jpeg(cropped=True):
    full = Image.new("RGB", (800, 600), (90, 140, 200))
    d = ImageDraw.Draw(full)
    d.rectangle([600, 400, 780, 580], fill=(255, 0, 0))  # "secret" region that gets cropped out
    d.text((620, 480), "HOUSE #42", fill=(255, 255, 255))
    thumb = full.copy()
    thumb.thumbnail((160, 120))
    tb = io.BytesIO()
    thumb.save(tb, "JPEG")
    img = full.crop((0, 0, 500, 500)) if cropped else full
    exif = {
        "0th": {piexif.ImageIFD.Make: b"Apple", piexif.ImageIFD.Model: b"iPhone 15 Pro",
                piexif.ImageIFD.Software: b"Adobe Photoshop 25.0 (Macintosh)",
                piexif.ImageIFD.Artist: b"Cameron Preston",
                piexif.ImageIFD.DateTime: b"2026:11:10 21:14:03"},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: b"2026:11:10 21:14:03",
                 piexif.ExifIFD.OffsetTimeOriginal: b"-05:00",
                 piexif.ExifIFD.BodySerialNumber: b"F2LXK9ABC123",
                 piexif.ExifIFD.CameraOwnerName: b"cam",
                 piexif.ExifIFD.LensSerialNumber: b"LS-998877"},
        "GPS": {piexif.GPSIFD.GPSLatitudeRef: b"N",
                piexif.GPSIFD.GPSLatitude: ((41, 1), (5, 1), (5123, 100)),
                piexif.GPSIFD.GPSLongitudeRef: b"W",
                piexif.GPSIFD.GPSLongitude: ((80, 1), (39, 1), (1234, 100)),
                piexif.GPSIFD.GPSAltitude: (312, 1),
                piexif.GPSIFD.GPSImgDirection: (271, 1)},
        "1st": {}, "thumbnail": tb.getvalue(),
    }
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=piexif.dump(exif), quality=90,
             comment=b"saved from C:\\Users\\jsmith\\Desktop\\pics")
    data = buf.getvalue()
    xmp = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           b'<rdf:Description xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/" '
           b'xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/" xmp:CreatorTool="Adobe Photoshop 25.0" '
           b'photoshop:City="Youngstown" xmpMM:OriginalDocumentID="xmp.did:ABC-123"/></rdf:RDF></x:xmpmeta>')
    app1 = b"http://ns.adobe.com/xap/1.0/\x00" + xmp
    seg = b"\xff\xe1" + (len(app1) + 2).to_bytes(2, "big") + app1
    first_end = 4 + int.from_bytes(data[4:6], "big")  # keep APP0/APP1 first for parsers
    data = data[:first_end] + seg + data[first_end:]
    return data


jpg = leaky_jpeg()
open(P("photo.jpg"), "wb").write(jpg + b"\x00\x00\x00\x18ftypmp42" + os.urandom(3000))
open(P("clean_photo.jpg"), "wb").write(
    (lambda b: (Image.new("RGB", (64, 64), "green").save(b, "JPEG"), b.getvalue())[1])(io.BytesIO()))

# PNG with AI prompt
png = Image.new("RGB", (100, 100), "purple")
from PIL.PngImagePlugin import PngInfo  # noqa: E402
pi = PngInfo()
pi.add_text("parameters", "photo of my apartment at 123 Main St, Steps: 30, Seed: 1234")
pi.add_text("Author", "jsmith@acme-corp.com")
png.save(P("ai.png"), pnginfo=pi)

# ---------------- PDF: failed redaction + revision + attachment + white text + annot
doc = pymupdf.open()
pg = doc.new_page()
pg.insert_text((72, 100), "Witness name: Jane Doe, SSN 123-45-6789", fontsize=12)
pg.insert_text((72, 130), "Project codename: BLUEBIRD", fontsize=12)
pg.insert_text((72, 400), "internal-only: ref ticket 4471", fontsize=12, color=(1, 1, 1))
pg.insert_image(pymupdf.Rect(72, 450, 272, 650), stream=jpg)
doc.set_metadata({"author": "John Smith", "creator": "Microsoft Word for Microsoft 365",
                  "producer": "Acrobat Distiller 23",
                  "title": "C:\\Users\\jsmith\\OneDrive - Acme\\Case 2231 draft.docx",
                  "creationDate": "D:20261101093000-05'00'"})
doc.embfile_add("salaries.csv", b"name,salary\njsmith,120000\n", filename="salaries.csv")
doc.save(P("_tmp.pdf"))
doc.close()
# incremental update: black-box the SSN line, drop codename, add a comment
doc = pymupdf.open(P("_tmp.pdf"))
pg = doc[0]
pg.draw_rect(pymupdf.Rect(70, 86, 330, 104), color=(0, 0, 0), fill=(0, 0, 0))
for inst in pg.search_for("Project codename: BLUEBIRD"):
    pg.add_redact_annot(inst)
pg.apply_redactions()
a = pg.add_text_annot((400, 100), "double-check SSN before filing")
a.set_info(title="Jane Paralegal")
a.update()
doc.saveIncr()
doc.close()
os.replace(P("_tmp.pdf"), P("filing.pdf"))

# ---------------- DOCX: tracked changes, comments, hidden text, canary, embedded image
d = Document()
d.core_properties.author = "John Smith"
d.core_properties.last_modified_by = "jsmith"
d.core_properties.comments = "draft for legal review"
p = d.add_paragraph("The settlement amount is ")
r = p.add_run("CONFIDENTIAL")
p.add_run(" and the client is Acme Corp.")
d.add_comment([r], text="Do we really want to disclose this?", author="Sarah Lawyer", initials="SL")
d.add_paragraph("Hidden note: HIDDENMARKER call opposing counsel")
d.add_paragraph("Our offer is final\u200b\u200c\u200b\u200b\u200c and n\u043en-negotiable.")
img_buf = io.BytesIO(jpg)
d.add_picture(img_buf, width=Inches(2))
d.save(P("_tmp.docx"))

zin = zipfile.ZipFile(P("_tmp.docx"))
parts = {n: zin.read(n) for n in zin.namelist()}
x = parts["word/document.xml"].decode()
x = x.replace("<w:t>Hidden note: HIDDENMARKER call opposing counsel</w:t>",
              '<w:rPr><w:vanish/></w:rPr><w:t>Hidden note: HIDDENMARKER call opposing counsel</w:t>', 1)
# tracked deletion of a sentence + insertion
x = x.replace(
    '<w:r><w:t xml:space="preserve"> and the client is Acme Corp.</w:t></w:r>',
    '<w:del w:id="91" w:author="John Smith" w:date="2026-11-09T10:00:00Z"><w:r><w:delText xml:space="preserve">'
    ' The real number is $4.2M.</w:delText></w:r></w:del>'
    '<w:ins w:id="92" w:author="Sarah Lawyer" w:date="2026-11-09T11:00:00Z"><w:r><w:t xml:space="preserve">'
    ' and the client is Acme Corp.</w:t></w:r></w:ins>', 1)
assert "w:del " in x and "vanish" in x, "fixture injection failed"
parts["word/document.xml"] = x.encode()
app = parts["docProps/app.xml"].decode()
app = re.sub(r"</Properties>", "<Company>Acme Legal LLP</Company><Manager>Bob Boss</Manager>"
             "<TotalTime>312</TotalTime></Properties>", app)
parts["docProps/app.xml"] = app.encode()
with zipfile.ZipFile(P("memo.docx"), "w", zipfile.ZIP_DEFLATED) as z:
    for n, b in parts.items():
        z.writestr(n, b)
os.remove(P("_tmp.docx"))

# ---------------- XLSX
wb = Workbook()
ws = wb.active
ws["A1"] = "Budget"
ws["A1"].comment = Comment("numbers are fake, real ones in hidden tab", "Mike Finance")
h = wb.create_sheet("RealNumbers")
h["A1"] = 4200000
h.sheet_state = "hidden"
ws.row_dimensions[5].hidden = True
wb.properties.creator = "mike.f"
wb.properties.lastModifiedBy = "Mike Finance"
wb.save(P("budget.xlsx"))

# ---------------- PPTX with notes
prs = Presentation()
s = prs.slides.add_slide(prs.slide_layouts[1])
s.shapes.title.text = "Q4 plan"
s.notes_slide.notes_text_frame.text = "Don't mention the layoffs until legal signs off"
prs.core_properties.author = "Exec Team"
prs.save(P("deck.pptx"))

# ---------------- text with canaries
open(P("leak.txt"), "w", encoding="utf-8").write(
    "Internal memo\u200b\u200b\u200c\u200b: the merger cl\u043eses on Friday. "
    "Only shared with the \u0435xec team.\U000E0049\U000E0044\U000E0037\n")

# ---------------- video with GPS
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
                "-metadata", "location=+41.0985-080.6532/",
                "-metadata", "com.apple.quicktime.make=Apple",
                "-metadata", "com.apple.quicktime.model=iPhone 15 Pro",
                "-metadata", "artist=Cameron", "-metadata", "creation_time=2026-11-10T21:14:03Z",
                "-movflags", "use_metadata_tags", P("clip.mp4")], check=True)
print("fixtures in", out)
