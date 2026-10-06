#!/usr/bin/env bash
# Regenerate the two committed reference runs (toy backend, 30 x 300 tokens, beta 0.5 and 0.2).
#   bash results/reproduce.sh            # just regenerate
#   bash results/reproduce.sh --push     # regenerate, then commit and push results/
# Runs are seeded; numbers reproduce up to floating-point differences between machines.
set -euo pipefail
cd "$(dirname "$0")/.."
PUSH=0; [ "${1:-}" = "--push" ] && PUSH=1
[ -s data/corpus/austen-emma.txt ] || bash data/fetch_corpus.sh
if [ ! -d .venv ]; then
  for p in python3.13 python3.12 python3.11 python3.10 python3; do
    command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' && { "$p" -m venv .venv; break; }
  done
fi
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install -q -U pip
python -m pip install -q -e ".[dev]"
python -m pytest -q tests
python -m textgrain_ref.cli demo --backend toy --n 30 --tokens 300 --beta 0.5 --out results/toy-beta0.5 > results/toy-beta0.5.log 2>&1
python -m textgrain_ref.cli demo --backend toy --n 30 --tokens 300 --beta 0.2 --out results/toy-beta0.2 > results/toy-beta0.2.log 2>&1
echo "regenerated results/toy-beta0.5 and results/toy-beta0.2"
if [ "$PUSH" = 1 ]; then
  git add results/toy-beta0.5 results/toy-beta0.2
  git commit -qm "results: regenerate reference runs ($(uname -sm), $(python --version 2>&1))" || echo "nothing to commit"
  git push
fi
