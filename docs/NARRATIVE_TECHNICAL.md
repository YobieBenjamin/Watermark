# The Watermark Project: A Narrative with Technical Depth

*Plain English, but with the mechanisms, numbers, and limits spelled out. The non-technical version is [NARRATIVE_PLAIN_ENGLISH.md](NARRATIVE_PLAIN_ENGLISH.md); the formal design notes are [DESIGN.md](DESIGN.md).*

---

## 1. Problem statement

### The regulatory trigger

Article 50(2) of the EU AI Act requires providers of AI systems that generate text, images, audio, or video to mark their outputs in a machine-readable format and make them detectable as artificially generated. The obligation has applied since 2 August 2026; a July 2026 amendment (the "digital omnibus") gave systems already on the market until 2 December 2026 to comply. The accompanying Code of Practice exempts very short text, defined as under 200 tokens, but otherwise expects marking even where it is known to be unreliable.

On 5 October 2026 OpenAI published its answer for text: **textGrain**, a statistical watermark embedded in the model's word choices, rolling out by default to ChatGPT and Codex users in the EU, available to API customers worldwide as an opt-in that is off by default, with detector access initially restricted to approved researchers. OpenAI's own figures, at a 1% false-positive target: about 80% detection on 200-token passages, 95% on 400-token psychology passages, roughly 60% on mathematical content, 42–69% across the other official EU languages, and a collapse from 92% to 66% when 10% of words are replaced with synonyms and to 17% at 25%.

### The misconception

The intuitive model of a watermark is post-hoc: generate the content, then stamp it. For images that is often literally true, because pixels have perceptual slack in which to hide a signal. Text has almost none. A sentence carries only a few bits of free choice per word, and the only place to spend them is the moment the model chooses the word. Every production text watermark (Kirchenbauer's green-list scheme, Aaronson's Gumbel-max, Google's SynthID-Text, OpenAI's textGrain) therefore operates **inside the sampling step**. Post-hoc tricks such as zero-width Unicode characters or homoglyph substitution exist, but they die on copy-paste or normalization, and OpenAI explicitly does not use them.

### What a watermark cannot do

Three gaps follow from the design, and they are not bugs.

1. **It is a presence signal, not an integrity signal.** It says "a keyed sampler influenced these words," never "these are exactly the words." Edit three tokens of a genuine output to flip its meaning and it still detects.
2. **It is fragile to rewriting.** The keyed values at a position depend on the preceding few tokens, so one edit destroys several scored positions. Paraphrase with any unwatermarked model and the signal is gone. Zhang et al. (2024) proved the general case: given a quality oracle and a perturbation oracle, a random walk removes any watermark while preserving quality.
3. **It needs entropy.** The signal lives only where the model had a real choice. Code, mathematics, structured output, and deterministic decoding (temperature 0) leave little or nothing to tilt.

## 2. Purpose

The project builds a faithful, open reference implementation of a textGrain-style watermark; attacks it with the full range of realistic manipulations; and wraps it in the two additional layers that cover the gaps above, measuring which layer holds against which attack. Design constraints: one command, no model download, no GPU, no API key, and self-validation of the mathematics before any result is reported. The stated goal is not to beat OpenAI's numbers but to make the behaviour of this class of system inspectable and reproducible.

## 3. Methodology

### 3.1 Embedding: keyed blocks and an entropy budget

At each decoding step the model produces a next-token distribution **P** over the vocabulary, after temperature scaling and nucleus (top-p) truncation. The watermark intervenes as follows.

- **Keyed randomness.** A secret key and the last *L* tokens (the *context window*, default 3) are hashed with HMAC-SHA256 to produce seeds. Everything keyed is a deterministic function of (key, context); the only true randomness left is the sampling itself.
- **Partition.** The seeds assign every vocabulary token to one of *B* blocks (default 32). Block probabilities are the sums of **P** over each block.
- **Cost table.** The seeds also produce a *B* × *m* table of Gumbel-distributed costs (default *m* = 16 columns).
- **Coupling under a budget.** An optimal-transport problem picks a joint distribution over (block, column) whose row sums match the block probabilities and whose column sums are uniform, favouring low-cost pairs, subject to a penalty on the Kullback–Leibler divergence from independence. That divergence equals the average sampling entropy removed, so a single parameter β (the *entropy budget*, default 0.5) states directly what fraction of the model's randomness the watermark may consume. The solver is log-domain Sinkhorn with a multiplicative update on the regularisation strength (the report's Algorithm A.1) and a marginal-correction step so the marginals are exact.
- **Sampling.** The key selects a column *J*; the token is sampled from the column's conditional block law, and within the chosen block tokens keep their original relative probabilities.

Two identities make this scheme honest, and both are checked numerically to 10⁻⁹ in the tests: averaging the watermarked distribution over columns recovers **P** exactly (*unbiasedness*), and the entropy removed equals the KL divergence (*the budget means what it says*). Unlike Gumbel-max, which is deterministic for a fixed key and context and so returns the same answer to the same prompt every time, textGrain retains residual randomness; the harness verifies that the same prompt with the same key yields distinct outputs.

If a context window recurs within one generation, the position is *masked* (sampled from **P** directly) so keyed values are never reused in a response, and the detector mirrors this by scoring only the first occurrence of each window.

### 3.2 Detection: a Gamma test

For each scored position the detector recomputes the seeds from the key and the observed window, finds the block of the observed token, the keyed column, and the uniform variate *u* behind that cell's cost, and forms the score *Y* = −log(1 − *u*). Under the null hypothesis that the text is independent of the key, *u* is uniform, so *Y* is a unit exponential and the sum over *n* positions is Gamma(*n*, 1). The detector reports a *p*-value, a *z*-score (*S*ₙ − *n*)/√*n*, and the decision at a chosen false-positive rate α. It needs the tokenizer and the key, not the model and not the budget.

Because an edit at position *t* changes the token at *t* and the windows of the next *L* positions, each edit costs *L* + 1 scored positions. That is why detection falls off a cliff as the fraction of edited words rises, in our curves and in OpenAI's.

### 3.3 Hardening the detector

- **Canonicalisation** (NFKC, zero-width removal, a confusables map, typographic quotes and dashes, whitespace) runs before tokenisation. Attacks that change bytes but not glyphs become no-ops.
- **Localisation**: sliding windows of 120 tokens, each tested against the Gamma null with Bonferroni correction, merged into segments with character offsets. A watermarked paragraph buried in a long human document is found even when the whole-document statistic is diluted below threshold.

### 3.4 Semantic retrieval

Every output is split into sentences, embedded, and stored in a FAISS inner-product index. A suspect text is embedded the same way; each candidate source is scored by the mean of its best per-sentence similarities, with coverage and a count of near-identical sentences as tie-breakers so diluted documents still resolve. Because the match is on meaning, this layer survives paraphrase and (with a multilingual embedder) translation. Offline the harness uses a character n-gram hashing embedder, which captures lexical overlap only; a sentence-transformers model is one flag away.

### 3.5 Signed registry

Each output's canonicalised text is hashed with SHA-256 and the hash signed with Ed25519. Verification returns `exact` (hash match, signature valid), `tampered` (no exact match, but retrieval resolves a near original, with the differing spans and the changed-token fraction), or `unknown`. This closes the spoofing gap: a three-token edit still carries the watermark, but it no longer hashes to anything the provider signed, and the diff shows what changed.

### 3.6 The attack harness

Thirteen attacks run against every layer: identity (copy-paste); word-processor auto-formatting; zero-width insertion; homoglyph substitution; resampling 10%, 25%, and 50% of tokens from the unwatermarked model (the synonym/paraphrase proxy); random deletion and insertion; truncation to 150 and 80 tokens; dilution inside three times as much human text; and a three-token piggyback edit. With a real model, two more are available: LLM paraphrase and round-trip translation through French. Null calibration uses human text plus *unwatermarked* model text, so the false-positive rate is measured on both kinds of innocent input. Reported metrics: true-positive rate at the analytic and the empirically calibrated threshold, ROC AUC, mean *z* for the naive and hardened detectors, retrieval recall@1, registry verdict shares, a combined "stack attributed" rate, and localisation IoU for the dilution attack.

## 4. Implementation

The repository (`textgrain_ref/`) is about a dozen modules: `prf.py` (HMAC seeds and random-access expansion), `ot.py` (Sinkhorn with budget calibration), `watermark.py` (the sampler and a backend-agnostic decode loop), `canonicalize.py`, `detector.py`, `retrieval.py`, `registry.py`, `attacks.py`, `harness.py`, and `cli.py`, plus two language-model backends.

The **toy backend** is a trigram model with interpolated absolute discounting, trained in seconds on two public-domain Jane Austen novels (9,667-token vocabulary). It exists so that the entire pipeline runs offline on one CPU; it produces semi-coherent prose with real entropy, which is all the watermark cares about. The **Hugging Face backend** wraps any causal language model with KV-cached watermarked decoding, chat templates for instruct models, and unwatermarked `paraphrase` and `translate_roundtrip` helpers that play the attacker. Its decode loop is validated by a test that builds a tiny randomly initialised GPT-2 and a locally trained tokenizer, so no download is needed.

Fifteen tests gate every run: PRF determinism and uniformity; OT marginals and budget; the unbiasedness and entropy identities; a Kolmogorov–Smirnov test of 3,000+ null scores against Exp(1); false-positive control on 120 human passages; detection power and wrong-key nulls; output diversity under a fixed key; canonicalisation defeating desync attacks; localisation of a buried span; retrieval recall; registry verdicts and signature tamper-evidence. `./run.sh` creates a virtual environment, installs, runs the tests, and runs the sweep. The full sweep takes about two and a half minutes on one sandbox CPU and 72 seconds on an Apple M4 Max.

Deliberately left as reference-grade: the per-element pseudorandom expansion is splitmix64 seeded by HMAC rather than a block cipher; keys are derived from a seed for reproducibility; the detector, retrieval, and registry endpoints are unauthenticated oracles. The README lists the production replacements.

## 5. Practical results

Reference run: toy backend, 30 samples × 300 tokens per condition, α = 0.01, β = 0.5. Full tables and plots are in `results/`.

| attack | tokens changed | watermark TPR (naive → hardened) | mean *z* (naive → hardened) | retrieval R@1 | registry verdict |
|---|---|---|---|---|---|
| copy-paste | 0% | 100% → 100% | 26.9 → 26.9 | 100% | exact |
| word-processor auto-format | 0% | 100% → 100% | 24.4 → 26.9 | 100% | exact |
| zero-width characters | 0% | 100% → 100% | 5.3 → 26.9 | 100% | exact |
| homoglyphs | 0% | 100% → 100% | 8.4 → 26.9 | 100% | exact |
| resample 10% of tokens | 16% | 100% → 100% | 17.8 | 100% | tampered |
| resample 25% | 34% | 100% → 100% | 8.7 | 100% | tampered |
| resample 50% | 73% | 20% → 20% | 1.6 | 100% | unknown |
| delete 10% / insert 10% | 15% / 13% | 100% | 18.6 / 18.9 | 100% | tampered |
| truncate to 150 / 80 tokens | — | 100% | 19.3 / 13.8 | 100% | tampered |
| dilute in 3× human text | — | 100% | 17.9 | 100% | tampered; localisation IoU 0.73, found 100% |
| piggyback edit (3 tokens) | 1% | 100% → 100% | 25.9 | 100% | tampered, diff returned |

Null calibration on 60 innocent passages: one false positive (1.7%, consistent with α = 1% in a sample that size). The registry returned `unknown` for all human text. The achieved entropy budget averaged 0.464 of the requested 0.5. Same prompt and same key produced distinct outputs 100% of the time. A second run at β = 0.2 behaves the same way with a weaker signal (copy-paste *z* = 19.7; the naive detector's recall on the zero-width attack drops to 83% while the hardened detector stays at 100%). The run was regenerated on an Apple M4 Max under Python 3.13 and reproduced every figure exactly.

Reading the table:

- Copy-paste, through any number of editors, is a non-event; the registry additionally returns `exact`.
- Tokenizer-desync attacks cost a naive detector three quarters of its signal with zero visible change and cost the hardened detector nothing.
- Rewriting is the attack that works. At 50% resampling the watermark is near chance, and only retrieval still identifies the source.
- Dilution defeats the passage-level test's intent but not localisation.
- Piggyback spoofing defeats the watermark *by design* (the text is still "detected") and is caught by the integrity layer, which names the changed span.

What transfers and what does not: the toy model has roughly 3.7 nats of entropy per token, two to three times an instruction-tuned LLM at temperature 1 with top-p, so absolute detection rates here are optimistic. OpenAI's reported 80% at 200 tokens and 17% after 25% synonym replacement are the realistic regime. The ordering of attacks, the value of canonicalisation, the behaviour of localisation, and the retrieval and registry verdicts are properties of the architecture, not of the model, and transfer directly. The HF backend exists to produce the real-model numbers.

## 6. Impact

**On how provenance systems should be built.** The results draw a layer map: the watermark covers copy-paste and light edits; canonicalisation covers desync tricks for free; localisation covers dilution; retrieval covers rewriting and translation and is the only layer that does; the signed registry covers integrity and is the only layer that does. A provider deploying a watermark alone has deployed a compliance artefact that catches lazy misuse. A provider deploying all four layers can turn "watermark present" into attribution with tamper localisation.

**On how verdicts should be read.** Absence of a watermark is not evidence of human authorship: the text may be short, rewritten, translated, from an unwatermarked model, or from a provider whose mark was off. Presence of a watermark is not evidence that the text is unedited. And the false-positive arithmetic is unforgiving: at α = 1%, scanning a million human documents produces ten thousand false accusations. Any use of a detector in a decision about a person needs an empirically calibrated operating point for that language and domain, and the registry and retrieval verdicts alongside the watermark one.

**On the regulatory design.** Article 50 obliges marking; it does not define what "detectable" must mean in the hands of a teacher or an editor. OpenAI's choice to gate its detector is defensible as oracle hygiene and sits in tension with the Code of Practice's access expectations for regulators, media, researchers, and civil society. The API opt-in shifts the obligation to integrators: under the Commission's guidelines, a company that ships a product on an upstream model under its own name is itself a provider, and leaving the default in place means it cannot rely on the upstream mark. Nothing technical closes the coverage gap left by open-weights models; that is a policy question.

**On research.** The harness is a common yardstick. A new watermark scheme is a drop-in replacement for one method (`TextGrainSampler.distribution`) and one scoring function; a new attack is one function and one line in the sweep. Watermark-stealing and detector-oracle attacks are the two notable omissions, left as extension points because they need either tens of thousands of queries or detector access, which is precisely what gating is meant to make expensive.

**The bottom line.** A text watermark is a keyed bias in word choice. It is self-synchronising, so copying and reformatting preserve it; it is context-hashed, so every edit costs several positions and rewriting erases it; and it is zero-bit, so forging around it is cheap. These are properties of the whole class, not of any vendor's implementation. The durable design is not a stronger mark but a stack: a watermark for presence, retrieval for meaning, a signed registry for integrity, and calibration discipline for the verdicts. This repository is a working, reproducible instance of that stack.
