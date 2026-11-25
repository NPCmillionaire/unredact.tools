# unredact.tools for Chrome

Checks files before they leave your browser. Everything runs on your device; no file is sent anywhere.

- **Right-click → "Inspect with unredact.tools"** on any image, PDF, video or download link. A report opens with what the file gives away (GPS, authors, text under black boxes, deleted text in earlier revisions, attachments, comments, thumbnails, motion-photo video). From there you can download a clean copy or the recovered artifacts.
- **Clean before send.** On Gmail, Outlook, Proton, Slack, Discord, X, Reddit, Telegram, WhatsApp, Facebook, LinkedIn, GitHub, Google Drive, Dropbox, OneDrive and Box (each can be turned off, and you can add other sites), uploads through the file picker or drag-and-drop are held for a moment and scanned. If anything leaks, a toast offers **Clean & Upload**, **Upload Original** or **Cancel**.

## Install (development)

1. `chrome://extensions` → turn on Developer mode → **Load unpacked** → choose this `extension/` folder.
2. Reload any tabs that were already open on protected sites.

## What it covers

| Type | Detects | Cleans |
|---|---|---|
| JPEG / PNG / WebP | EXIF GPS, device, serials, owner, XMP/IPTC places & IDs, thumbnails (and uncropped ones), PNG text / AI prompts, appended data | lossless metadata strip (keeps orientation and ICC) |
| HEIC / TIFF | same metadata | no (use the CLI) |
| PDF | fake redactions (text under boxes), invisible text, unapplied redaction annots, earlier revisions + deleted text, metadata/XMP, attachments, comments, JS, form data, EXIF in embedded photos | rebuilds from visible pages, or flattens pages to images when hidden text is found |
| DOCX / XLSX / PPTX | properties, company/manager, comments, tracked changes, hidden text, hidden sheets/rows, speaker notes, rsids, canaries, EXIF in embedded photos | everything except hidden sheets/rows |
| MP4 / MOV | GPS, device make/model, artist | no (use the CLI) |
| Text | zero-width / tag-character / homoglyph watermarks | yes |

Visual checks (faces, plates, OCR of screenshots, printer dots, camera fingerprint) need the Python tool. Run `unredact.tools web` locally and use **Deep scan** on a report.

## Layout

- `engine/`: scanners and cleaners (pdf.js, pdf-lib, JSZip, exifr in `vendor/`)
- `background.js`: context menu, registers content scripts for enabled sites, starts the offscreen engine
- `offscreen/`: hosts the engine for content scripts
- `content/shim.js` (page world): makes detached `<input type=file>` visible to the interceptor
- `content/intercept.js`: holds back picker/drop uploads and shows the toast
- `report/`, `popup/`, `options/`: UI
