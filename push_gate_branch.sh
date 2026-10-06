#!/usr/bin/env bash
# Push the execution-gate work to GitHub and open a PR, using YOUR GitHub login.
# No token is ever handled by this script or by Claude; auth stays on your machine.
#
#   ./push_gate_branch.sh                       # push branch 'gate', open a PR against main
#   ./push_gate_branch.sh main                  # commit straight onto main and push instead
set -euo pipefail
cd "$(dirname "$0")"
TARGET="${1:-gate}"
command -v git >/dev/null || { echo "git not found"; exit 1; }
git rev-parse --git-dir >/dev/null 2>&1 || { echo "run this inside the Watermark repo"; exit 1; }

echo "== self-validation before pushing =="
./run.sh gate >/dev/null && echo "gate sweep: ALL OK" || { echo "gate sweep FAILED — not pushing"; exit 1; }

if [ "$TARGET" = "main" ]; then
  git add -A
  git commit -m "Layer 6: execution gate + 8th-grade explainers" || echo "nothing to commit"
  git push origin main
  echo "pushed to main"
  exit 0
fi

git rev-parse --verify gate >/dev/null 2>&1 || git checkout -b gate
git checkout gate
git add -A
git commit -m "Layer 6: execution gate + rewritten 8th-grade explainers" || echo "nothing new to commit"
git push -u origin gate
if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
  gh pr create --fill --base main --head gate || echo "branch pushed; open the PR in the web UI"
else
  echo "branch 'gate' pushed. Open a PR at:"
  echo "  https://github.com/yobiebenjamin/Watermark/compare/main...gate"
fi
