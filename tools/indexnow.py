#!/usr/bin/env python3
"""indexnow: tell Bing (and other IndexNow engines) which pages changed.

    python tools/indexnow.py                     # every URL in the live sitemap
    python tools/indexnow.py /guides /about      # just these paths

Run it after a deploy that adds or changes pages. The key is served by the app at
/<key>.txt, which is how the engines check the ping came from this site.

Standard library only. Exits 1 if the ping is rejected.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request

SITE = "https://unredact.tools"
KEY = "2dec584e80c69d02f5f37e8360239dd4"  # must match INDEXNOW_KEY in unredact_tools/web/app.py  # gitleaks:allow
ENDPOINT = "https://api.indexnow.org/indexnow"


def sitemap_urls() -> list[str]:
    with urllib.request.urlopen(f"{SITE}/sitemap.xml", timeout=30) as r:
        return re.findall(r"<loc>([^<]+)</loc>", r.read().decode())


def main(paths: list[str]) -> int:
    urls = [SITE + p if p.startswith("/") else p for p in paths] or sitemap_urls()
    body = json.dumps({"host": SITE.split("//", 1)[1], "key": KEY,
                       "keyLocation": f"{SITE}/{KEY}.txt", "urlList": urls}).encode()
    req = urllib.request.Request(ENDPOINT, data=body, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    # 200 = accepted, 202 = accepted while the key is still being verified
    print(f"IndexNow: {status} for {len(urls)} URL(s)")
    return 0 if status in (200, 202) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
