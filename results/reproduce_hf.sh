#!/usr/bin/env bash
# Regenerate the committed real-model run: Qwen2.5-1.5B-Instruct, the multilingual embedder and the two
# LLM-driven attacks (paraphrase, French round-trip translation), 40 x 400 tokens.
#   bash results/reproduce_hf.sh            # venv + hf extra, pytest, the sweep -> results/hf-<timestamp>/, README table
#   bash results/reproduce_hf.sh --push     # then commit the run and the README and push
# Runs on Apple Silicon (MPS), CUDA or CPU; about an hour on an M4 Max, the model (3 GB) downloads once.
# Seeded; the LLM attacks reproduce up to floating-point and backend differences between machines.
set -euo pipefail
cd "$(dirname "$0")/.."
PUSH=0; [ "${1:-}" = "--push" ] && PUSH=1
MODEL="${MODEL:-Qwen/Qwen2.5-1.5B-Instruct}"
EMBEDDER="${EMBEDDER:-paraphrase-multilingual-MiniLM-L12-v2}"
N="${N:-40}"; TOKENS="${TOKENS:-400}"

./run.sh hf --model "$MODEL" --embedder "$EMBEDDER" --llm-attacks --n "$N" --tokens "$TOKENS"

OUT="$(ls -d results/hf-* | grep -v '\.log$' | sort | tail -1)"
[ -s "$OUT/report.json" ] || { echo "no report in $OUT" >&2; exit 1; }
. .venv/bin/activate
python results/readme_table.py "$OUT" --write README.md
echo "regenerated $OUT and the real-model section of README.md"
if [ "$PUSH" = 1 ]; then
  git add "$OUT" README.md
  git commit -qm "results: real-model run $(basename "$OUT") ($MODEL, $(uname -sm), $(python --version 2>&1))" || echo "nothing to commit"
  git push
fi
