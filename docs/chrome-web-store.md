# Chrome Web Store listing

Copy these into the Developer Dashboard. Images are generated into `dist/chrome-store/` (not committed).

## Store listing

**Name** (from manifest): unredact.tools

**Summary** (132 chars max, from manifest):
> See what a file gives away before you upload it. Catches GPS, authors, fake redactions and hidden text, then cleans it. 100% local.

**Category:** Privacy & Security (Tools if that isn't offered)

**Language:** English

**Description:**

```
Files carry more than what's on screen. A phone photo can hold the exact spot it was taken and the serial number of the phone. A "redacted" PDF can still hold the text under its black boxes. A Word file can hold every edit and every name that touched it.

unredact.tools checks your files before they leave your browser, and shows you exactly what they give away.

CLEAN BEFORE SEND
When you attach a file on Gmail, Outlook, Proton, Slack, Discord, X, Reddit, Telegram, WhatsApp, Facebook, LinkedIn, GitHub, Google Drive, Dropbox, OneDrive or Box, the upload is held for a moment and scanned. If anything leaks, you get a short warning such as "3 leaks found (GPS, Author, Fake redaction)" and a choice: Clean & Upload, Upload Original, or Cancel. Works with the file picker and drag-and-drop. Turn any site off, or add your own.

INSPECT FILES YOU FIND ONLINE
Right-click any image, PDF, video or download link and choose "Inspect with unredact.tools". A report shows what the file reveals: where (GPS, place names), who (authors, usernames, camera owner), which device (make, model, serial numbers), when, and what's hidden. Download a clean copy, or the things recovered from inside the file.

WHAT IT FINDS
• Photos (JPEG, PNG, WebP, HEIC, TIFF): GPS, device and lens serial numbers, owner and artist names, place names, uncropped thumbnails, motion-photo video, AI image prompts
• PDFs: fake redactions (text under black boxes), invisible text, unapplied redactions, deleted text in earlier versions, attachments, comments, scripts, form data, author and software, GPS in embedded photos
• Word, Excel, PowerPoint: authors and company, comments, tracked changes, hidden text, hidden sheets and rows, speaker notes, file paths with usernames
• Videos (MP4, MOV): GPS and device
• Text: invisible watermark characters

PRIVATE BY DESIGN
Everything runs inside your browser. No file is uploaded to us or anyone else, there's no account, and there are no analytics. The extension is open source.

For visual checks (faces, license plates, text in screenshots, printer tracking dots), run the free unredact.tools desktop tool and use "Deep scan" from any report.
```

**Homepage URL:** https://unredact.tools

**Support URL:** https://unredact.tools/contact

**Privacy policy URL:** https://unredact.tools/privacy

## Images

| Slot | File |
|---|---|
| Store icon (128×128) | `extension/icons/128.png` |
| Screenshots (1280×800) | `screenshot-1-toast-photo.jpg` … `screenshot-5-options.jpg` |
| Small promo tile (440×280) | `promo-small-440x280.jpg` |
| Marquee promo tile (1400×560, optional) | `promo-marquee-1400x560.jpg` |

## Privacy practices tab

**Single purpose:**
> Detects and removes hidden metadata and recoverable redactions in files: before you upload them on sites you choose, and in files you find online.

**Permission justifications:**

- **contextMenus:** Adds "Inspect with unredact.tools" to the right-click menu on images, links, video, audio and PDF pages.
- **storage:** Saves the user's settings (protected sites, severity, size limit) on the device.
- **scripting:** Registers the upload checker on the sites the user has turned on, and reads `blob:` files the user right-clicks, which only exist inside that page.
- **offscreen:** Runs the file scanner (PDF rendering, image and Office parsing) in an offscreen document so pages and the service worker stay responsive.
- **activeTab:** Lets the toolbar popup show and toggle protection for the current site and inspect the file open in the current tab.
- **Host permissions (`<all_urls>`):** Users right-click files hosted on any domain, and the extension must fetch that file to inspect it. Users can also add any site to upload protection. The extension only acts when the user right-clicks a file or uploads a file on a protected site. It doesn't read or send page content.

**Remote code:** No. All code is packaged in the extension, and the pdf.js, pdf-lib, JSZip and exifr libraries are vendored.

**Data usage:** Check none of the data types. Files are processed locally and never transmitted. Settings stay in `chrome.storage` on the device.

Certify all three:
- Not sold to third parties
- Not used or transferred for purposes unrelated to the item's single purpose
- Not used or transferred to determine creditworthiness or for lending purposes

## Distribution

Public, all regions. Free.
