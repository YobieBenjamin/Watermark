#!/usr/bin/env bash
# Build the Word versions of the posts from the Markdown: pandoc, then one pass through
# LibreOffice so the files open cleanly in Word. Idempotent; run it after editing a post.
#   bash blog/build.sh                                   # every post in blog/
#   bash blog/build.sh blog/02-inside-openais-watermark.md
# Needs pandoc and LibreOffice (soffice on PATH, or SOFFICE=/path/to/soffice).
set -euo pipefail
cd "$(dirname "$0")/.."
SOFFICE="${SOFFICE:-$(command -v soffice || echo /Applications/LibreOffice.app/Contents/MacOS/soffice)}"
command -v pandoc >/dev/null || { echo "pandoc not found" >&2; exit 1; }
[ -x "$SOFFICE" ] || { echo "LibreOffice not found (set SOFFICE=/path/to/soffice)" >&2; exit 1; }
POSTS=("$@"); [ ${#POSTS[@]} -gt 0 ] || POSTS=(blog/[0-9]*.md)
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
for md in "${POSTS[@]}"; do
  base="$(basename "${md%.md}")"
  pandoc --from gfm --to docx --resource-path blog -o "$TMP/$base.docx" "$md"
  "$SOFFICE" --headless --convert-to 'docx:MS Word 2007 XML' --outdir "$TMP/out" "$TMP/$base.docx" >/dev/null 2>&1
  [ -s "$TMP/out/$base.docx" ] || { echo "LibreOffice produced nothing for $md" >&2; exit 1; }
  unzip -tq "$TMP/out/$base.docx" >/dev/null && unzip -l "$TMP/out/$base.docx" | grep -q 'word/document.xml'
  mv "$TMP/out/$base.docx" "blog/$base.docx"
  echo "blog/$base.docx  ($(pandoc --from gfm --to plain "$md" | wc -w | tr -d ' ') words)"
done
