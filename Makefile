.PHONY: demo quick test hf gate clean
demo:      ; ./run.sh toy
quick:     ; ./run.sh toy --quick --n 8 --tokens 200
test:      ; python -m pytest -q tests
hf:        ; ./run.sh hf --model $(MODEL) --embedder paraphrase-multilingual-MiniLM-L12-v2 --llm-attacks
gate:      ; ./run.sh gate
clean:     ; rm -rf .venv .cache results/*/registry.sqlite results/*/retrieval_index results/*/registry_signing_key.pem results/*/decisions.sqlite results/*/sidecar
