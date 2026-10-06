#!/usr/bin/env bash
# Fetch the public-domain Jane Austen texts used by the toy model (data/corpus) and as the
# human-text pool (data/human) from the nltk_data Gutenberg corpus mirror on GitHub.
set -euo pipefail
cd "$(dirname "$0")"
URL="https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages/corpora/gutenberg.zip"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "fetching $URL"
curl -sSL -o "$TMP/gutenberg.zip" "$URL"
( cd "$TMP" && unzip -q gutenberg.zip )
mkdir -p corpus human
cp "$TMP/gutenberg/austen-emma.txt" "$TMP/gutenberg/austen-persuasion.txt" corpus/
cp "$TMP/gutenberg/austen-sense.txt" human/
ls -la corpus human
