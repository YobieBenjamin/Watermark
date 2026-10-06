#!/usr/bin/env bash
# Create the GitHub repository and push this directory in one command.
# Needs the GitHub CLI authenticated on *your* machine (`gh auth login`); no token is
# ever handled by anything in this repo.
#
#   ./publish_to_github.sh yobiebenjamin Watermark [public|private]
set -euo pipefail
OWNER="${1:?usage: $0 <github-user-or-org> [repo-name] [public|private]}"
REPO="${2:-Watermark}"
VIS="${3:-public}"
cd "$(dirname "$0")"
command -v gh >/dev/null || { echo "install the GitHub CLI first: https://cli.github.com"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "run: gh auth login"; exit 1; }
if [ ! -d .git ]; then git init -q -b main; fi
git add -A
git commit -qm "textgrain-ref: watermark, hardened detector, retrieval, signed registry, attack harness" || true
if gh repo view "$OWNER/$REPO" >/dev/null 2>&1; then
  git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$OWNER/$REPO.git"
  git push -u origin main
else
  gh repo create "$OWNER/$REPO" "--$VIS" --source=. --remote=origin --push \
    --description "Reference stack: textGrain-style LLM watermark, hardened detector, semantic retrieval, signed registry, attack harness"
fi
echo "https://github.com/$OWNER/$REPO"
