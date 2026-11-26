# Chrome Web Store listing

Copy these into the Developer Dashboard. Don't list site or brand names in the description or image captions: the store rejected v0.1.2 for keyword spam (Yellow Argon) over the list of supported sites. Images are generated into `dist/chrome-store/` (not committed).

## Store listing

**Name** (from manifest): unredact.tools

**Summary** (132 chars max, from manifest):
> See what a file gives away before you upload it. Catches GPS, authors, fake redactions and hidden text, then cleans it. 100% local.

**Category:** Privacy & Security (Tools if that isn't offered)

**Language:** English

**Description:**

```
your files talk. this shuts them up.

a phone photo knows where you were standing and which phone took it. a "redacted" PDF still has the text under the black box. a word doc remembers every edit and every name that touched it. you can't see any of it. everyone you send it to can.

unredact.tools catches it before it leaves your browser. then it cleans it.

no uploads. no account. no analytics. everything runs on your machine.


//CLEAN BEFORE SEND

attach a file on a site you've protected. common email, chat, social and cloud storage sites are on out of the box. the upload is held for a second and scanned.

if it leaks, you're told exactly what:
    "3 leaks found (GPS, Author, Fake redaction)"
then you pick: Clean & Upload. Upload Original. Cancel.

works with the file picker and drag-and-drop. turn any site off. add your own.


//INSPECT ANYTHING YOU FIND ONLINE

right-click any image, PDF, video or download link → "Inspect with unredact.tools".

the report tells you what the file gives away:
>>> where: GPS, place names
>>> who: authors, usernames, camera owner
>>> which device: make, model, serial numbers
>>> when: capture and edit times
>>> what's hidden: text under black boxes, deleted text, attachments, thumbnails

download a clean copy, or pull out what was recovered from inside it.


//WHAT IT CATCHES

>>> photos (JPEG, PNG, WebP, HEIC, TIFF): GPS, device and lens serials, owner and artist names, place names, uncropped thumbnails, motion-photo video, AI image prompts
>>> PDFs: fake redactions, invisible text, unapplied redactions, deleted text in earlier versions, attachments, comments, scripts, form data, author and software, GPS in embedded photos
>>> word, excel, powerpoint: authors and company, comments, tracked changes, hidden text, hidden sheets and rows, speaker notes, file paths with usernames
>>> video (MP4, MOV): GPS and device
>>> text: invisible watermark characters


//WHAT IT CLEANS

>>> JPEG, PNG, WebP: metadata stripped losslessly. orientation and color profile kept.
>>> PDF: rebuilt from what's visible. if hidden text is found, pages are flattened so nothing underneath survives.
>>> word, excel, powerpoint: everything above except hidden sheets and rows.
>>> text: watermark characters removed.

HEIC, TIFF and video are detected, not cleaned. use the desktop tool for those. we'd rather tell you than pretend.


//PRIVATE BY DESIGN

>>> no file leaves your device. not to us. not to anyone.
>>> it only acts when you right-click a file or upload on a site you protect. it doesn't read or send page content.
>>> no account. no analytics. no tracking.
>>> open source: github.com/NPCmillionaire/unredact.tools
>>> free. forever.


//NEED MORE

faces, license plates, text in screenshots, printer tracking dots: run the free unredact.tools desktop tool and hit "Deep scan" from any report.

built by legit{biz} --research. we publish the code. we publish the receipts.
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
- **Host permissions:** Only the 16 built-in upload-protection sites (Gmail, Outlook, Slack and so on), where the upload checker runs, plus 127.0.0.1/localhost for the optional local Deep scan. Any other site is an optional permission requested at the moment the user adds it to upload protection or right-clicks a file hosted there. The extension doesn't read or send page content.

**Remote code:** No. All code is packaged in the extension, and the pdf.js, pdf-lib, JSZip and exifr libraries are vendored.

**Data usage:** Check none of the data types. Files are processed locally and never transmitted. Settings stay in `chrome.storage` on the device.

Certify all three:
- Not sold to third parties
- Not used or transferred for purposes unrelated to the item's single purpose
- Not used or transferred to determine creditworthiness or for lending purposes

## Distribution

Public, all regions. Free.
