#!/bin/sh
# Upload govint site to govint.unredact.tools (Namecheap cPanel, SSH alias "unredact")
set -e
cd "$(dirname "$0")/.."
rsync -av --delete --exclude _headers site/govint/ unredact:sites/govint.unredact.tools/public_html/