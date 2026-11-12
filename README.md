<div align="center">

# unredact-file

**See what your files give away before someone else does. Then scrub it.**

Part of the [unredact.tools](https://unredact.tools) suite &nbsp;·&nbsp; Python 3.10+ &nbsp;·&nbsp; local-first, no uploads

</div>

---

Every photo, PDF, and Office document carries more than what's on screen: GPS coordinates, camera serial numbers, usernames, earlier drafts, deleted text, comments, and invisible watermarks that identify exactly whose copy leaked. `unredact-file` does two things:

| Command | What it does |
|---|---|
| **`scan`** | Shows **what's leaking** from a file, plus a plain-English summary of what an adversary learns from it |
| **`sanitize`** | Writes a **clean copy** with all of it removed, then re-scans the copy to prove it |

```console
$ unredact-file scan vacation.jpg

=== vacation.jpg
    image/jpeg  |  9,933 bytes  |  sha256 ef46020ec70bae88...
    2 critical  7 high  8 medium  3 low

  WHAT THIS FILE GIVES AWAY
  WHERE                  GPS 41.097564, -80.653428 (https://www.google.com/maps?q=41.097564,-80.653428)
                         City: Youngstown
  WHO                    Camera owner name: cam
                         Artist: Cameron Preston
                         username 'jsmith'
  DEVICE                 Camera / phone model: iPhone 15 Pro
                         Camera body serial number: F2LXK9ABC123
  WHEN                   photo taken 2026:11:10 21:14:03 UTC-05:00
  SOFTWARE               Adobe Photoshop 25.0 (Macintosh)
  HIDDEN CONTENT         thumbnail of the uncropped original
                         embedded motion-photo video

  FINDINGS
  [CRITICAL] Exact GPS coordinates  (EXIF GPS)
  [CRITICAL] Embedded video (motion photo)  (trailer)
  [  HIGH  ] Camera body serial number  (EXIF)
  [  HIGH  ] Embedded EXIF thumbnail has a DIFFERENT aspect ratio than the image -
             it likely shows the original, uncropped photo  (EXIF IFD1)
  ...

$ unredact-file sanitize vacation.jpg
+ vacation.jpg -> vacation.clean.jpg
  leaks: 20 before -> 0 after
```

---

## Contents

- [Why this exists](#why-this-exists)
- [Install](#install)
- [Usage](#usage)
- [What it detects: the four layers](#what-it-detects-the-four-layers)
  - [Layer 1: Hard metadata](#layer-1-hard-metadata)
  - [Layer 2: Broken redactions and hidden content](#layer-2-broken-redactions-and-hidden-content)
  - [Layer 3: Visual content OSINT](#layer-3-visual-content-osint)
  - [Layer 4: Fingerprinting and tracking](#layer-4-fingerprinting-and-tracking)
- [How sanitizing works](#how-sanitizing-works)
- [Supported formats](#supported-formats)
- [Architecture](#architecture)
- [Limitations](#limitations)
- [Roadmap](#roadmap)
- [Development](#development)
- [Prior art](#prior-art)

---

## Why this exists

People have been caught by metadata for as long as files have carried it:

- **2012:** John McAfee's location was given away by GPS EXIF data in a photo a journalist posted while he was hiding from authorities.
- **2017:** The NSA document Reality Winner leaked was published as a scan. The **printer tracking dots** on it identified the printer and the time it was printed.
- **2019:** Lawyers for Paul Manafort filed a PDF with "redactions" that were black rectangles drawn over text that was still there. Reporters copied the text out within minutes.
- **Forever:** Word documents ship with tracked changes, comments, and the author's username. PDFs keep every earlier version when they're saved incrementally.

Good tools exist: [ExifTool](https://exiftool.org) (2003), [mat2](https://0xacab.org/jvoisin/mat2), [Dangerzone](https://dangerzone.rocks). But each one does a single job, and none of them **explains the risk**. `unredact-file` is built around that question: *if I send this, what does the person receiving it learn about me?*

---

## Install

```bash
git clone https://github.com/NPCmillionaire/unredact.tools
cd unredact.tools
pip install -e .              # pillow, pymupdf, pikepdf, lxml

# optional
pip install -e .[heic]        # iPhone HEIC/HEIF photos
brew install ffmpeg           # audio/video  (apt install ffmpeg on Linux)
```

---

## Usage

### `scan`: show what's leaking

```bash
unredact-file scan photo.jpg                     # human-readable report
unredact-file scan *.pdf *.docx                  # many files at once
unredact-file scan memo.docx --extract ./loot    # also save recovered artifacts
unredact-file scan filing.pdf --json             # machine-readable
unredact-file scan photo.jpg --all               # include info-level findings
```

`--extract` writes out everything the scan recovered, such as embedded thumbnails, earlier PDF revisions, attachments, motion-photo video, and embedded documents, to `./loot/<file>_artifacts/`.

**Exit codes:** `0` means nothing high or critical was found. `2` means at least one high or critical leak, so you can use it as a pre-publish gate in CI:

```yaml
- run: unredact-file scan public/**/*.{pdf,jpg,png,docx} --no-color
```

### `sanitize`: strip all of it

```bash
unredact-file sanitize photo.jpg                       # -> photo.clean.jpg
unredact-file sanitize a.pdf b.docx c.mp4              # batch
unredact-file sanitize report.pdf -o public.pdf        # custom output path
unredact-file sanitize report.pdf --pdf-mode text      # keep selectable text
```

The original is never modified. Each output is **re-scanned automatically**, and anything that survived is listed:

```
+ memo.docx -> memo.clean.docx
  leaks: 39 before -> 0 after
```

---

## What it detects: the four layers

Findings are graded **critical / high / medium / low / info** and grouped into the summary buckets **WHERE · WHO · DEVICE · WHEN · SOFTWARE · INTERNAL/ORG · HIDDEN CONTENT · TRACKING**.

✅ = shipped &nbsp;&nbsp; 🚧 = partial &nbsp;&nbsp; 🗺️ = planned

### Layer 1: Hard metadata

*Deterministic fields that sit next to the content. These are the easiest to check and the most often forgotten.*

| | Leak | Where it hides |
|---|---|---|
| ✅ | **GPS coordinates** (with map link), altitude (which floor you were on), compass heading, speed | EXIF GPS IFD, XMP, video `©xyz` / ISO 6709 atoms |
| ✅ | **Timestamps + timezone offset** | EXIF `DateTimeOriginal` / `OffsetTime`, PDF Info, `docProps/core.xml`, zip entry times |
| ✅ | **Device make, model, body serial, lens serial** | EXIF, XMP `aux:SerialNumber`, QuickTime `com.apple.quicktime.*` |
| ✅ | **Owner / author / artist / last-modified-by / manager / company** | EXIF, IPTC, XMP `dc:creator`, PDF Info, Office core/app props |
| ✅ | **Software versions and edit history** | EXIF `Software`, XMP `xmpMM:History`, PDF `Producer`/`Creator`, Office `AppVersion` |
| ✅ | **Usernames in file paths** (`C:\Users\jsmith\…`, `/Users/cam/…`, `/home/…`) | PDF titles, JPEG comments, Excel `absPath`, Word template paths |
| ✅ | **Internal infrastructure**: UNC shares, SharePoint/OneDrive URLs, private IPs, `file://` links | Office `.rels`, data connections, metadata strings |
| ✅ | **Microsoft 365 tenant ID** | Office sensitivity-label custom props (`MSIP_Label_*_SiteId`) |
| ✅ | **Total editing time, revision count, last printed** | `docProps/app.xml`, `docProps/core.xml` |
| ✅ | **Vendor MakerNote blobs** (shutter count, internal IDs, Apple content identifiers) | EXIF `MakerNote` |
| ✅ | **Document IDs that link copies and versions** | XMP `DocumentID` / `OriginalDocumentID`, PDF trailer `/ID` |
| ✅ | **AI-generation prompts and settings** | PNG `tEXt` `parameters` chunk (Stable Diffusion, ComfyUI) |

### Layer 2: Broken redactions and hidden content

*Content the author believes is gone or invisible but isn't. This layer gives the project its name.*

| | Leak | How it's found |
|---|---|---|
| ✅ | **Failed PDF redactions**: black rectangles, black images, or unapplied redaction annotations with the **text still underneath** | Detects dark filled shapes and dark images, then extracts the words beneath each one |
| ✅ | **Earlier PDF versions** saved incrementally, with a diff of the **text deleted since** | Splits the file at each `%%EOF` and re-parses every earlier revision |
| ✅ | **Tracked deleted text in Word** (`w:delText`) and change authors | Walks `w:del` / `w:moveFrom` |
| ✅ | **Comments**, commenters, collaborator IDs and emails | `comments*.xml`, `people.xml`, `xl/persons`, `commentAuthors.xml` |
| ✅ | **Hidden text**: Word `w:vanish`, white-on-white text, microscopic fonts, transparent text, text placed off the page | Run properties and span color/size/alpha |
| ✅ | **Hidden sheets** (`hidden` / `veryHidden`), hidden rows and columns, pivot caches holding deleted source data | `xl/workbook.xml`, sheet XML, `pivotCache` |
| ✅ | **Speaker notes and hidden slides** | `ppt/notesSlides`, `show="0"` |
| ✅ | **Embedded EXIF thumbnail showing the original, uncropped photo** | Compares the thumbnail's aspect ratio with the main image |
| ✅ | **Motion-photo video** (a few seconds of video and audio around the shot) and other data appended after the image ends | Walks JPEG markers to the real end-of-image and inspects what follows |
| ✅ | **Attachments and embedded objects**: PDF attachments, OLE objects, and whole spreadsheets inside a Word document (scanned recursively) | `EmbeddedFiles`, `word/embeddings/` |
| ✅ | **Hidden PDF layers** (optional content turned off) | OCG state |
| ✅ | **Extra image frames**: depth maps, HDR gain maps | MPO frames |
| ✅ | **Metadata inside embedded images**: a JPEG placed in a PDF or Word document keeps its GPS | Scans every embedded image as its own file |
| 🗺️ | **Reversible pixelation and blur**: flag mosaics weak enough to reverse | Depix-style block analysis |
| 🗺️ | **Readable text in screenshots** that the sender didn't notice: browser tabs, notifications, URLs, chat names | OCR (Tesseract / PaddleOCR) |

### Layer 3: Visual content OSINT

*What a human analyst, or a vision model, can work out from the pixels themselves. Stripping metadata doesn't touch this.*

| | Leak |
|---|---|
| 🗺️ | **Reflections** in glasses, windows, screens, and chrome |
| 🗺️ | **Readable text in the scene**: street signs, mail labels, ID badges, license plates, whiteboards, monitors |
| 🗺️ | **Geolocation clues**: architecture, vegetation, power outlets, road markings, utility poles, plus **sun angle combined with the timestamp** (GeoGuessr-style inference) |
| 🗺️ | **Bystanders and children**: detect faces and offer one-click blur |
| 🗺️ | **QR codes and barcodes**, especially boarding passes, which encode name, PNR, and frequent-flyer number |
| 🗺️ | **Cross-file correlation**: "the camera serial in this photo also appears in 3 photos on your public Flickr" (via [unredact.me](https://unredact.me)) |

### Layer 4: Fingerprinting and tracking

*Marks that tie a file to a specific person, device, or copy, often added deliberately to catch leakers.*

| | Leak | Notes |
|---|---|---|
| ✅ | **Zero-width character watermarks** (canary traps) | `U+200B`–`U+200F`, `U+2060`–`U+2064`, `U+FEFF`, and others |
| ✅ | **Unicode tag smuggling**: invisible `U+E0000` text, **decoded and shown** | Also used for prompt-injection payloads |
| ✅ | **Homoglyph substitution**: Cyrillic or Greek look-alikes swapped into Latin words, a different pattern per recipient | Mixed-script word detection |
| ✅ | **Odd-width space patterns** | Weak signal; flagged as low |
| ✅ | **Word rsid session IDs** that fingerprint editing sessions and link drafts | `w:rsid*` attributes |
| ✅ | **Phone-home objects**: PDF JavaScript, Launch, SubmitForm, remote references, external links | Object scan |
| ✅ | **Persistent document IDs** | PDF `/ID`, XMP instance IDs |
| 🗺️ | **Printer tracking dots** (Machine Identification Code), yellow dot grids on scanned pages | Yellow-channel isolation + DocuColor grid decoding |
| 🗺️ | **Camera sensor noise (PRNU)**: links photos to the physical camera that took them | Analysis only; survives re-encoding |
| 🗺️ | **Whitespace and line-break fingerprinting** in plain text | Statistical |

---

## How sanitizing works

Sanitizing means **rebuilding the file from its visible content**, not deleting known-bad fields. A rebuild never copies a field the tool doesn't know about.

### Images
1. Bake EXIF rotation into the pixels, so removing the orientation tag doesn't leave the photo sideways.
2. Convert the embedded ICC profile to sRGB, so colors don't shift when the profile is dropped.
3. Copy **raw pixel data only** into a brand-new image object and re-encode it (JPEG q95 / PNG / WebP / TIFF).

**Removed:** EXIF, GPS, XMP, IPTC, thumbnails, MakerNotes, comments, text chunks, ICC, appended data and motion video, extra frames.

### PDF: two modes

| Mode | What happens | Use when |
|---|---|---|
| **`raster`** *(default)* | Every page is rendered to an image and rebuilt into a new PDF (the [Dangerzone](https://dangerzone.rocks) approach). **Nothing survives except pixels.** | Maximum safety. You don't need selectable text. |
| **`text`** | Burns in **real** redactions under every detected black box, removes hidden/white/microscopic text, deletes annotations, JavaScript, attachments, thumbnails, XMP and Info, strips EXIF from embedded JPEGs, rewrites the file in full (old revisions are gone), and assigns a fresh random `/ID`. | You need searchable text, such as court filings or published reports. |

### Office (docx / xlsx / pptx)
- Empties core properties. Strips app properties (company, manager, template, editing time, app version) and custom properties.
- **Accepts all tracked changes**: deletions are removed and insertions are kept as plain text.
- Deletes comments, threaded comments, people and author lists, and speaker notes.
- Removes hidden text, rsid fingerprints, document variables, and mail-merge sources.
- Points template, data-source, and linked-object paths at `about:blank`, and removes the Excel `absPath`.
- **Unhides** hidden sheets, rows, columns, and slides. They're surfaced, not deleted, so formulas don't break and you see exactly what you're sending.
- Sanitizes every embedded image, and recurses into embedded Office documents.
- Strips text-level canary marks.
- Writes every zip entry with a 1980 timestamp, so the save time doesn't leak.

### Audio / video
`ffmpeg -c copy` (no quality loss). Keeps only the video, audio, and subtitle streams, which **drops the timed GPS and gyro data tracks**. Wipes global and per-stream metadata and chapters, and sets bitexact flags so no encoder tag is written.

### Text
Removes zero-width and tag characters, normalizes odd spaces, and converts homoglyphs back to Latin, but only inside words that mix scripts, so genuine Cyrillic or Greek text is left alone.

---

## Supported formats

| Type | Scan | Sanitize | Needs |
|---|:-:|:-:|---|
| JPEG, PNG, WebP, TIFF, GIF, BMP | ✅ | ✅ | |
| HEIC / HEIF / AVIF | ✅ | ✅ (→ JPEG) | `pillow-heif` |
| PDF | ✅ | ✅ | |
| DOCX / XLSX / PPTX (+ `m` macro variants) | ✅ | ✅ | |
| MP4, MOV, M4A, MKV, WebM, MP3, FLAC, WAV… | ✅ | ✅ | `ffmpeg` |
| Plain text (txt, md, csv, json…) | ✅ | ✅ | |
| ZIP | ✅ (names, comment) | ❌ | |
| ODT / ODS, camera RAW | ❌ | ❌ | planned |

---

## Architecture

```
unredact_file/
├── cli.py        argparse front-end: scan / sanitize, report rendering, exit codes
├── scanner.py    magic-byte type detection → dispatch to the format modules
├── model.py      Report, Finding, Artifact; "facts" that feed the GIVES-AWAY summary
├── common.py     shared detectors: username/host/email/IP regexes, canary-trap text analysis
├── xmp.py        generic XMP packet walker (used by images + PDF)
├── image.py      EXIF / GPS / IPTC / XMP / thumbnail / trailer analysis + pixel-rebuild sanitizer
├── pdf.py        PyMuPDF + pikepdf: redaction checks, revisions, hidden text, JS, raster/text sanitize
├── office.py     OOXML part-level XML analysis (lxml) + package rewriter
└── media.py      ffprobe / ffmpeg wrapper
```

**Design principles**

- **Local-first.** Nothing leaves your machine. A privacy tool that asks you to upload the file defeats its own purpose.
- **Explain, don't just list.** Every finding is translated into what an adversary learns from it.
- **Rebuild, don't patch.** Sanitizers construct new files from visible content.
- **Prove it.** Every sanitize re-runs the scanner on its own output.
- **Recursive.** An image inside a Word document inside a PDF attachment gets the same scrutiny as a standalone file.

**Planned web version:** Layers 1, 2, and 4 run client-side in the browser (ExifTool compiled to WASM, pdf.js, a JS zip/XML stack), so files never leave the device. Layer 3 runs server-side **opt-in only**, in ephemeral containers that delete the input immediately.

---

## Limitations

- **Layer 3 isn't built yet.** A photo with no metadata can still show your street sign, your face in a reflection, or your boarding pass.
- **PRNU sensor noise survives re-encoding.** A clean photo can still be statistically linked to the camera that took it.
- **Raster PDF mode loses text selection and accessibility.** Use `--pdf-mode text` if you need them.
- **Excel sanitize removes comment VML**, which also removes legacy form controls.
- **Encrypted PDFs** are reported but not analyzed.
- **Detection is heuristic.** A redaction box drawn in an unusual way, such as a dark-gray pattern fill or a clipping-path trick, could be missed. When the stakes are real, use `raster` mode.

---

## Roadmap

- [x] Layer 1: hard metadata (images, PDF, Office, media)
- [x] Layer 2: failed redactions, revisions, tracked changes, hidden content
- [x] Layer 4: text canaries, rsids, phone-home objects
- [x] `sanitize` with self-verifying re-scan
- [ ] Layer 2: reversible pixelation/blur detection, OCR of screenshots
- [ ] Layer 3: vision-model scene analysis, faces/plates/QR detection, sun-angle geolocation
- [ ] Layer 4: printer tracking dots, PRNU linkage
- [ ] `--blur-faces`, `--redact-regions` for visual sanitizing
- [ ] ODF and camera RAW support
- [ ] Browser/WASM build on unredact.tools
- [ ] unredact.me integration for cross-referencing a found serial, username, or document ID against public data
- [ ] Batch API for newsrooms and law firms

---

## Development

```bash
pip install -e ".[dev]"   # pytest + fixture generators
pytest -q
```

`tests/make_fixtures.py` generates a set of **deliberately leaky** files: a GPS-tagged photo with an uncropped thumbnail and an appended video, a court-style PDF with a failed redaction and an earlier revision, a Word memo with deleted tracked changes and canary characters, a spreadsheet with a hidden sheet, a deck with speaker notes, a watermarked text file, and a GPS-tagged MP4. The tests assert that **every fixture leaks before sanitizing and leaks nothing after.**

Generate them yourself to see the tool in action:

```bash
python tests/make_fixtures.py ./demo
unredact-file scan ./demo/* --extract ./demo/loot
```

---

## Prior art

| Tool | Focus |
|---|---|
| [ExifTool](https://exiftool.org) | The reference metadata reader/writer; supports almost every format |
| [mat2](https://0xacab.org/jvoisin/mat2) | Metadata removal, Tails/Tor ecosystem |
| [Dangerzone](https://dangerzone.rocks) | Convert untrusted documents to safe pixel-only PDFs |
| [Depix](https://github.com/spipm/Depix) | Recover text from pixelated screenshots (the attack Layer 2 will warn about) |
| [x-ray](https://github.com/freelawproject/x-ray) | Free Law Project's bad-redaction finder for court PDFs |

`unredact-file` combines detection, explanation, and sanitizing in one place, and its scope runs past metadata into hidden content, redaction failures, and tracking marks.

---

<div align="center">
<sub>Built by <a href="https://github.com/NPCmillionaire">NPCmillionaire</a> · Cloudsculpting Labs · part of <b>unredact.tools</b></sub>
</div>
