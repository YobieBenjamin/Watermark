#!/usr/bin/env bash
# One command: create a venv, install, self-validate (pytest), run the full evaluation.
#
#   ./run.sh                      # toy backend, offline, a few minutes on one CPU
#   ./run.sh toy --quick          # smoke sweep, under a minute
#   ./run.sh hf --model Qwen/Qwen2.5-1.5B-Instruct \
#              --embedder paraphrase-multilingual-MiniLM-L12-v2 --llm-attacks --n 40 --tokens 400
#
# Everything after the backend name is passed to `textgrain-ref demo`.
# Requires Python >= 3.10; the newest python3.x on PATH is used unless PYTHON is set.
set -euo pipefail
cd "$(dirname "$0")"
BACKEND="${1:-toy}"
[ $# -gt 0 ] && shift

pick_python() {
  if [ -n "${PYTHON:-}" ]; then echo "$PYTHON"; return; fi
  for p in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
      echo "$p"; return
    fi
  done
  echo "no Python >= 3.10 found on PATH (set PYTHON=/path/to/python3.x)" >&2; exit 1
}
PY="$(pick_python)"

# corpus for the toy model and the human-text pool (public domain, fetched once if absent)
[ -s data/corpus/austen-emma.txt ] || bash data/fetch_corpus.sh

if [ ! -d .venv ]; then "$PY" -m venv .venv; fi
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install -q -U pip
if [ "$BACKEND" = "hf" ]; then
  python -m pip install -q -e ".[hf,dev]"
else
  python -m pip install -q -e ".[dev]"
fi
echo "== self-validation =="
python -m pytest -q tests
echo "== evaluation (backend: $BACKEND) =="
OUT="results/${BACKEND}-$(date +%Y%m%d-%H%M%S)"
python -m textgrain_ref.cli demo --backend "$BACKEND" --out "$OUT" "$@"
echo "report: $OUT/report.md   plots: $OUT/roc.png $OUT/tpr.png $OUT/zscore.png"
