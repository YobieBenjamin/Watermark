# Usage guide

## Install

```bash
git clone https://github.com/yobiebenjamin/Watermark && cd Watermark
./run.sh                      # creates .venv, installs, runs the tests and the full toy sweep
```

Requirements: Python 3.10+, ~200 MB of wheels (numpy, scipy, faiss-cpu, cryptography,
matplotlib). The toy corpus is fetched once from the nltk_data GitHub mirror if the
`data/` text files are missing. For a real model add the `hf` extra:

```bash
. .venv/bin/activate
pip install -e ".[hf]"        # torch, transformers, sentence-transformers, accelerate
```

## CLI reference

All commands are available as `textgrain-ref <cmd>` inside the venv or as
`python -m textgrain_ref.cli <cmd>`.

### `demo` — run the evaluation harness

```
textgrain-ref demo [--backend toy|hf] [--model NAME] [--embedder NAME]
                   [--n 30] [--tokens 300] [--seed 7] [--alpha 0.01]
                   [--beta 0.5] [--blocks 32] [--columns 16] [--context-window 3]
                   [--temperature 1.0] [--top-p 1.0] [--quick] [--llm-attacks] [--out DIR]
```

| flag | meaning |
|---|---|
| `--backend` | `toy` (offline trigram LM) or `hf` (any causal LM from the Hub or a local path) |
| `--embedder` | sentence-transformers model for retrieval; default is the offline hashing embedder |
| `--n`, `--tokens` | samples per condition and tokens per sample |
| `--beta` | entropy budget: fraction of next-token entropy the watermark may remove on average |
| `--blocks`, `--columns` | B and m of the block-OT coupling; max entropy loss is min(H(ρ), log m) |
| `--context-window` | L: preceding tokens hashed with the key (robustness ↔ key-stealing trade-off) |
| `--temperature`, `--top-p` | sampling parameters; the watermark sees P *after* both |
| `--llm-attacks` | add paraphrase and translation round-trip (hf backend only) |
| `--quick` | 5-attack smoke sweep |

Outputs in `--out`: `report.md`, `report.json` (includes per-sample scores),
`roc.png`, `tpr.png`, `zscore.png`, `retrieval_index/`, `registry.sqlite`,
`registry_signing_key.pem`.

### `keygen`, `generate`, `detect`, `localize`

```bash
KEY=$(textgrain-ref keygen)                                   # 256-bit hex key
textgrain-ref generate --key-hex $KEY --prompt "Once upon a time" --tokens 300 > wm.txt
textgrain-ref generate --key-hex $KEY --prompt "Once upon a time" --tokens 300 --no-watermark > plain.txt
textgrain-ref detect   --key-hex $KEY --file wm.txt
textgrain-ref detect   --key-hex $KEY --file wm.txt --no-canonicalize     # the naive detector
textgrain-ref localize --key-hex $KEY --file mixed_document.txt --window 120 --stride 30
```

`detect` prints `n_tokens`, `n_scored`, the Gamma statistic, `p_value`, `z_score`,
the α-threshold and the decision. `localize` prints significant segments with token
and character spans and the segment text. Watermark parameters (`--beta` is not needed
for detection; `--blocks`, `--columns`, `--context-window` are) must match generation.

### `verify` — registry + retrieval verdict

```bash
textgrain-ref verify --out results/toy-20261006-120000 --file suspect.txt
```

Loads that run's registry, signing key and FAISS index and prints
`{"status": "exact" | "tampered" | "unknown", "record_id", "similarity",
"signature_valid", "changed_fraction", "diff": [...]}`.

### `gate` — execution-gate scenario sweep (layer 6)

```bash
textgrain-ref gate --out out-gate            # 24 runtime scenarios, in process and behind a Unix socket
textgrain-ref gate --out out-gate --no-sidecar
textgrain-ref gate --verify-log out-gate     # re-verify the hash-chained, signed decision log
```

Writes `report.md`, `report.json`, `signals.jsonl` (one line per decision, named
signals), `decisions.sqlite` (the log), `policy.json` (the pinned policy) and
`gate_public_key.txt`. Exit status is non-zero if any scenario's verdict differs from
its expectation, if a side effect is not explained by a signed ALLOW, or if the log
does not verify. Design, threat model and the silicon mapping: [`GATE.md`](GATE.md).

## Python API

```python
import numpy as np
from textgrain_ref import TextGrainConfig, TextGrainSampler, Detector, generate
from textgrain_ref.lm.ngram import load_or_train

lm = load_or_train("data/corpus", ".cache")
key = bytes.fromhex("…64 hex chars…")
cfg = TextGrainConfig(beta=0.5, n_blocks=32, n_columns=16, context_window=3)

sampler = TextGrainSampler(key, cfg, lm.vocab_size)
ids = generate(lm, lm.encode("Emma Woodhouse"), 300, sampler, np.random.default_rng(0),
               context_window=cfg.context_window)
text = lm.decode(ids)

det = Detector(key, lm.encode, cfg, canonicalize_input=True, alpha=0.01,
               token_spans=lm.tok.token_spans)
print(det.detect(text))                 # DetectionResult(n_scored=…, p_value=…, z_score=…, detected=True)
print(det.localize(text).segments)
```

Any object with `encode`, `decode`, `vocab_size` and `next_token_dist(ids) -> P`
works as a backend for `generate`. `HFLanguageModel` additionally provides a
KV-cached `generate` and `instruct` / `paraphrase` / `translate_roundtrip`.

Retrieval and registry:

```python
from textgrain_ref.retrieval import RetrievalIndex, build_embedder
from textgrain_ref.registry import Registry

index = RetrievalIndex(build_embedder("paraphrase-multilingual-MiniLM-L12-v2"))
reg = Registry("registry.sqlite", Registry.generate_key())
rec = reg.register(text, model="my-model-v1")
index.add(f"doc-{rec.record_id}", text)
print(reg.verify(edited_text, index, {f"doc-{rec.record_id}": rec.record_id}))
```

### Gate

```python
from textgrain_ref.gate import ExecutionGate, Policy, default_policy, SoftwareSigner
from textgrain_ref.gate.provenance import NullOracle, ProvenanceOracle, attest_content
from textgrain_ref.gate.runtime import Principal, Runtime, approve, open_session, provision
from textgrain_ref.gate.tools import World

root, deployment = SoftwareSigner(label="root"), SoftwareSigner(label="deployment")     # silicon: HSM / TPM
principal = Principal("yobie@example.com", "agent", SoftwareSigner(label="principal"))
policy = Policy(default_policy())
world = World(documents={"inst": "Pay NW-2291 500 USD."})
att = attest_content(principal.signer, world.documents["inst"])                        # the human signs their instruction
world.attestations[att["content_hash"]] = att

oracle = NullOracle()                     # or ProvenanceOracle(detector, registry, index, doc_to_record)
gate = ExecutionGate(policy, {root.key_id: root.public_hex()}, oracle, world, SoftwareSigner(label="gate"),
                     log_path="decisions.sqlite", signals_path="signals.jsonl")

import time
dep = provision(root, deployment, "dep-1", "toy", policy.hash, gate.measurement, "wm-key-id", "registry-key-id", time.time())
session_key, session = open_session(deployment, dep, principal, time.time())
rt = Runtime(dep, session, session_key, time.time)          # the thin signer between model and gate

gate.submit(rt.envelope("read_document", {"doc_id": "inst"})).verdict            # ALLOW; input labelled 'verified'
d = gate.submit(rt.envelope("transfer_funds", {"amount": 500.0, "currency": "USD", "to_account": "NW-2291"}))
d.verdict, d.signal_names                                                        # ALLOW, [... 'decision.allow']

held = gate.submit(rt.envelope("transfer_funds", {"amount": 500.0, "currency": "USD", "to_account": "X"}))
# -> HOLD if the session has read unverifiable content; the principal signs once:
gate.submit(approve(principal, held_envelope, time.time())).verdict             # ALLOW

gate.log.verify(gate.public_hex())                                               # (True, n_rows)
```

Advisory signals from an external sensor: pass `external_signals=[Signal("probe.deception", value=0.9)]`
to `submit`, or register a `SignalSource` with `signal_sources=[...]`; thresholds live under
`advisory` in the policy. As a separate process: `service.start_sidecar(socket_path, factory)` and
`service.GateClient(socket_path).submit(envelope)`.

## Hugging Face backend

```bash
./run.sh hf --model Qwen/Qwen2.5-1.5B-Instruct \
            --embedder paraphrase-multilingual-MiniLM-L12-v2 \
            --llm-attacks --n 40 --tokens 400 --top-p 0.95
```

* Instruct models get the prompt through their chat template; base models get the raw
  prompt. The `DEFAULT_PROMPTS` list in `harness.py` supplies the ELI5-style questions.
* `--llm-attacks` uses the same model, unwatermarked, as the attacker: it paraphrases
  each output and translates it to French and back.
* The entropy of a real model at temperature 1 with top-p is far below the toy model's,
  so expect detection rates closer to OpenAI's published ones (≈80 % at 200 tokens).
  Sweep `--beta` to see the quality/detectability trade-off.
* `tests/test_hf_backend.py` validates the decode loop with a tiny random GPT-2 and a
  locally trained tokenizer; it runs automatically once the `hf` extra is installed.

## Reading the report

* **TPR naive vs hardened** — same detector with canonicalisation off/on. A gap means
  the attack was a tokenizer-desync attack.
* **mean z** — `(S_n − n)/√n`; 2.33 is the α = 1 % line for large n. Watch z rather than
  TPR when TPR saturates.
* **TPR (emp. thr)** — recall at the empirical null threshold; with a small null set this
  threshold is dominated by its single largest value, so prefer large `--n`.
* **retrieval R@1** — the attacked text's nearest registered document is its source.
* **registry exact/tampered/unknown** — integrity verdicts; `tampered` comes with spans.
* **stack attributed** — any layer identified the source.
* **localization** — for the dilution attack: IoU between the true watermarked span and
  the detector's segments, and how often a segment overlapped the truth by > 30 %.

## Extending

* Add an attack: write `def my_attack(text, ctx: AttackContext, **params) -> AttackResult`
  in `attacks.py` and append an `AttackSpec` to `default_sweep`.
* Add a backend: implement the four-method interface above (see `lm/ngram.py`).
* Change the scheme: `TextGrainSampler.distribution` is the only place that knows about
  block OT; a green-list or Gumbel-max sampler is a drop-in replacement, and the
  detector's `position_scores` is the only place that knows how to score.
