# The Watermark Project: The Story with the Numbers In

*Plain English, with the mechanisms, the numbers and the limits spelled out. The version with no jargon is [NARRATIVE_PLAIN_ENGLISH.md](NARRATIVE_PLAIN_ENGLISH.md); the math and the code map are in [DESIGN.md](DESIGN.md).*

---

## 1. The problem

### The regulatory trigger

Article 50(2) of the EU AI Act requires providers of AI systems that generate text, images, audio or video to mark their outputs in a machine-readable form and make them detectable as artificially generated. The obligation has applied since 2 August 2026; a July 2026 amendment (the "digital omnibus") gave systems already on the market until 2 December 2026. The Code of Practice that goes with it exempts very short text, under 200 tokens, and otherwise expects marking even where everybody knows it is unreliable.

On 5 October 2026 OpenAI published its answer for text: **textGrain**, a statistical watermark embedded in the model's word choices, on by default for ChatGPT and Codex users in the EU, available to API customers worldwide as an opt-in that is off by default, with detector access limited at first to approved researchers. OpenAI's own figures, at a 1% false-positive target: about 80% detection on 200-token passages, 95% on 400-token psychology passages, roughly 60% on mathematical content, 42% to 69% across the other official EU languages, and a collapse from 92% to 66% when 10% of words are replaced with synonyms and to 17% at 25%.

### The misconception

Everybody's first mental model of a watermark is post-hoc: generate the content, then stamp it. For images that is often literally true, because pixels have perceptual slack to hide a signal in. Text has almost none. A sentence carries only a few bits of free choice per word, and the only place to spend them is the moment the model picks the word. So every production text watermark (Kirchenbauer's green list, Aaronson's Gumbel-max, Google's SynthID-Text, OpenAI's textGrain) lives **inside the sampling step**. Post-hoc tricks like zero-width Unicode characters or homoglyph substitution exist. They die on copy-paste or normalisation, and OpenAI explicitly does not use them.

### What a watermark cannot do

Three gaps follow from the design, and they are not bugs.

1. **It is a presence signal, not an integrity signal.** It says "a keyed sampler influenced these words." It never says "these are exactly the words." Edit three tokens of a genuine output to flip its meaning and it still detects.
2. **It is fragile to rewriting.** The keyed values at a position depend on the few tokens before it, so one edit wrecks several scored positions. Paraphrase with any unwatermarked model and the signal is gone. Zhang et al. (2024) proved the general case: given a quality oracle and a perturbation oracle, a random walk removes any watermark while preserving quality.
3. **It needs entropy.** The signal only lives where the model had a real choice. Code, mathematics, structured output and deterministic decoding (temperature 0) leave little or nothing to tilt.

## 2. What I set out to do

Build a faithful, open copy of a textGrain-style watermark. Hit it with every realistic manipulation. Wrap it in the two extra layers that cover the gaps above, and measure which layer holds against which attack. Constraints: one command, no model download, no GPU, no API key, and the math checks itself before any result is printed. The goal was never to beat OpenAI's numbers. It was to make this class of system inspectable and reproducible by anyone with a laptop.

## 3. Method

### 3.1 Embedding: keyed blocks and an entropy budget

At each decoding step the model produces a next-token distribution **P** over the vocabulary, after temperature scaling and nucleus (top-p) truncation. The watermark steps in like this.

- **Keyed randomness.** A secret key and the last *L* tokens (the *context window*, default 3) are hashed with HMAC-SHA256 to produce seeds. Everything keyed is a deterministic function of (key, context); the only true randomness left is the sampling itself.
- **Partition.** The seeds assign every vocabulary token to one of *B* blocks (default 32). Block probabilities are the sums of **P** over each block.
- **Cost table.** The seeds also produce a *B* × *m* table of Gumbel-distributed costs (default *m* = 16 columns).
- **Coupling under a budget.** An optimal-transport problem picks a joint distribution over (block, column) whose row sums match the block probabilities and whose column sums are uniform, favouring low-cost pairs, subject to a penalty on the Kullback-Leibler divergence from independence. That divergence equals the average sampling entropy removed, so one parameter β (the *entropy budget*, default 0.5) states directly what fraction of the model's randomness the watermark may eat. The solver is log-domain Sinkhorn with a multiplicative update on the regularisation strength (the report's Algorithm A.1) and a marginal-correction step so the marginals come out exact.
- **Sampling.** The key picks a column *J*; the token is sampled from that column's conditional block law, and within the chosen block tokens keep their original relative probabilities.

Two identities keep this scheme honest, and both are checked numerically to 10⁻⁹ in the tests: averaging the watermarked distribution over columns gives back **P** exactly (*unbiasedness*), and the entropy removed equals the KL divergence (*the budget means what it says*). Unlike Gumbel-max, which is deterministic for a fixed key and context and so answers the same prompt the same way every time, textGrain keeps some real randomness; the harness checks that the same prompt with the same key gives different outputs.

If a context window repeats inside one generation, the position is *masked* (sampled from **P** directly) so keyed values are never reused within a response, and the detector mirrors this by scoring only the first occurrence of each window.

### 3.2 Detection: a Gamma test

For each scored position the detector recomputes the seeds from the key and the observed window, finds the block of the observed token, the keyed column, and the uniform variate *u* behind that cell's cost, and forms the score *Y* = −log(1 − *u*). Under the null hypothesis that the text has nothing to do with the key, *u* is uniform, so *Y* is a unit exponential and the sum over *n* positions is Gamma(*n*, 1). The detector reports a *p*-value, a *z*-score (*S*ₙ − *n*)/√*n*, and the decision at a chosen false-positive rate α. It needs the tokenizer and the key. It does not need the model or the budget.

An edit at position *t* changes the token at *t* and the windows of the next *L* positions, so each edit costs *L* + 1 scored positions. That is why detection falls off a cliff as the fraction of edited words rises, in my curves and in OpenAI's.

### 3.3 Hardening the detector

- **Canonicalisation** (NFKC, zero-width removal, a confusables map, typographic quotes and dashes, whitespace) runs before tokenisation. Attacks that change bytes but not glyphs become no-ops.
- **Localisation**: sliding windows of 120 tokens, each tested against the Gamma null with Bonferroni correction, merged into segments with character offsets. A watermarked paragraph buried in a long human document gets found even when the whole-document statistic is diluted below threshold.

### 3.4 Semantic retrieval

Every output is split into sentences, embedded, and stored in a FAISS inner-product index. A suspect text is embedded the same way; each candidate source is scored by the mean of its best per-sentence similarities, with coverage and a count of near-identical sentences as tie-breakers so diluted documents still resolve. Because the match is on meaning, this layer survives paraphrase and (with a multilingual embedder) translation. Offline the harness uses a character n-gram hashing embedder, which only sees lexical overlap; a sentence-transformers model is one flag away.

### 3.5 Signed registry

Each output's canonicalised text is hashed with SHA-256 and the hash signed with Ed25519. Verification returns `exact` (hash match, signature valid), `tampered` (no exact match, but retrieval finds a near original, with the differing spans and the changed-token fraction), or `unknown`. This closes the spoofing gap: a three-token edit still carries the watermark, but it no longer hashes to anything the provider signed, and the diff shows what moved.

### 3.6 The attack harness

Thirteen attacks run against every layer: identity (copy-paste); word-processor auto-formatting; zero-width insertion; homoglyph substitution; resampling 10%, 25% and 50% of tokens from the unwatermarked model (the synonym and paraphrase proxy); random deletion and insertion; truncation to 150 and 80 tokens; dilution inside three times as much human text; and a three-token piggyback edit. With a real model two more come on: LLM paraphrase and round-trip translation through French. Null calibration uses human text plus *unwatermarked* model text, so the false-positive rate is measured on both kinds of innocent input. Reported: true-positive rate at the analytic and the empirically calibrated threshold, ROC AUC, mean *z* for the naive and the hardened detector, retrieval recall@1, registry verdict shares, a combined "stack attributed" rate, and localisation IoU for the dilution attack.

## 4. Implementation

The repository (`textgrain_ref/`) is about a dozen modules: `prf.py` (HMAC seeds and random-access expansion), `ot.py` (Sinkhorn with budget calibration), `watermark.py` (the sampler and a backend-agnostic decode loop), `canonicalize.py`, `detector.py`, `retrieval.py`, `registry.py`, `attacks.py`, `harness.py` and `cli.py`, plus two language-model backends.

The **toy backend** is a trigram model with interpolated absolute discounting, trained in seconds on two public-domain Jane Austen novels (9,667-token vocabulary). It exists so the whole pipeline runs offline on one CPU; it writes semi-coherent prose with real entropy, which is all the watermark cares about. The **Hugging Face backend** wraps any causal language model with KV-cached watermarked decoding, chat templates for instruct models, and unwatermarked `paraphrase` and `translate_roundtrip` helpers that play the attacker. Its decode loop is checked by a test that builds a tiny randomly initialised GPT-2 and a locally trained tokenizer, so no download is needed.

Fifteen tests gate every run: PRF determinism and uniformity; OT marginals and budget; the unbiasedness and entropy identities; a Kolmogorov-Smirnov test of 3,000+ null scores against Exp(1); false-positive control on 120 human passages; detection power and wrong-key nulls; output diversity under a fixed key; canonicalisation defeating desync attacks; localisation of a buried span; retrieval recall; registry verdicts and signature tamper-evidence. `./run.sh` creates a virtual environment, installs, runs the tests, and runs the sweep. The full sweep takes about two and a half minutes on one sandbox CPU and 72 seconds on an Apple M4 Max.

Left at reference grade on purpose: the per-element pseudorandom expansion is splitmix64 seeded by HMAC rather than a block cipher; keys are derived from a seed so runs reproduce; the detector, retrieval and registry endpoints are unauthenticated oracles. The README lists the production replacements.

## 5. What came out

Reference run: toy backend, 30 samples × 300 tokens per condition, α = 0.01, β = 0.5. Full tables and plots in `results/`.

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
| truncate to 150 / 80 tokens | 50% / 73% | 100% | 19.3 / 13.8 | 100% | tampered |
| dilute in 3× human text | 54% | 100% | 17.9 | 100% | tampered; localisation IoU 0.73, found 100% |
| piggyback edit (3 tokens) | 1% | 100% → 100% | 25.9 | 100% | tampered, diff returned |

Null calibration on 60 innocent passages: one false positive (1.7%, which is what α = 1% looks like in a sample that size). The registry said `unknown` for all human text. The achieved entropy budget averaged 0.464 of the requested 0.5. Same prompt and same key produced distinct outputs 100% of the time. A second run at β = 0.2 behaves the same way with a weaker signal (copy-paste *z* = 19.7; the naive detector's recall on the zero-width attack drops to 83% while the hardened detector stays at 100%). The run was regenerated on an Apple M4 Max under Python 3.13 and reproduced every figure exactly.

Reading the table:

- Copy-paste, through any number of editors, is a non-event; the registry adds `exact`.
- Tokenizer-desync tricks cost a naive detector three quarters of its signal with nothing visible changed, and cost the hardened detector nothing.
- Rewriting is the attack that works. At 50% resampling the watermark is near chance, and only retrieval still names the source.
- Dilution defeats the passage-level test and not localisation.
- Piggyback spoofing beats the watermark *by design* (the text is still "detected") and the integrity layer catches it and names the changed span.

What carries over and what does not: the toy model has roughly 3.7 nats of entropy per token, two to three times an instruction-tuned LLM at temperature 1 with top-p, so the absolute detection rates above are flattering. OpenAI's reported 80% at 200 tokens and 17% after 25% synonym replacement is the realistic regime. The ordering of attacks, the value of canonicalisation, the behaviour of localisation, and the retrieval and registry verdicts are properties of the architecture, not of the model, and carry over as they are. The HF backend exists to get the real-model numbers, and the committed run with Qwen2.5-1.5B-Instruct (`results/hf-20261013-074938/`, 40 × 400 tokens) bears the prediction out: half the signal (z 12 against 27, 98% detection on untouched text); tokenizer-desync tricks that now cut the naive detector to 40% and 65% while canonicalisation holds 98%; chance-level detection once 70% of tokens are resampled; and 57% and 70% detection after LLM paraphrase and French round-trip translation, with the multilingual retrieval index attributing 100% of both. The paraphraser there is the same 1.5B model, so those two numbers are a floor on the damage, not a ceiling.

## 6. What it means

**For how provenance systems get built.** The results draw a layer map: the watermark covers copy-paste and light edits; canonicalisation covers desync tricks for free; localisation covers dilution; retrieval covers rewriting and translation and is the only layer that does; the signed registry covers integrity and is the only layer that does. A provider that ships a watermark alone has shipped a compliance artefact that catches lazy misuse. A provider that ships all four layers can turn "watermark present" into attribution with tamper localisation.

**For how verdicts get read.** Absence of a watermark is not evidence of human authorship: the text may be short, rewritten, translated, from an unwatermarked model, or from a provider whose mark was off. Presence of a watermark is not evidence that the text is unedited. And the false-positive arithmetic is brutal: at α = 1%, scanning a million human documents produces ten thousand false accusations. Any use of a detector in a decision about a person needs an empirically calibrated operating point for that language and domain, and the registry and retrieval verdicts next to the watermark one.

**For the regulation.** Article 50 obliges marking; it does not say what "detectable" has to mean in the hands of a teacher or an editor. OpenAI's choice to gate its detector is defensible as oracle hygiene and sits awkwardly next to the Code of Practice's access expectations for regulators, media, researchers and civil society. The API opt-in shifts the obligation to integrators: under the Commission's guidelines, a company that ships a product on an upstream model under its own name is itself a provider, and leaving the default in place means it cannot lean on the upstream mark. Nothing technical closes the coverage gap left by open-weights models. That is a policy question, and I am not going to pretend otherwise.

**For research.** The harness is a common yardstick. A new watermark scheme is a drop-in replacement for one method (`TextGrainSampler.distribution`) and one scoring function; a new attack is one function and one line in the sweep. Watermark stealing and detector hill-climbing are the two deliberate omissions, left as extension points because they need either tens of thousands of queries or detector access, which is what gating is supposed to make expensive.

**Bottom line.** A text watermark is a keyed bias in word choice. It self-synchronises, so copying and reformatting keep it; it is context-hashed, so every edit costs several positions and rewriting erases it; and it is zero-bit, so forging around it is cheap. Those are properties of the whole class, not of any one vendor's implementation. The durable design is not a stronger mark. It is a stack: a watermark for presence, retrieval for meaning, a signed registry for integrity, and calibration discipline for the verdicts. This repository is a working, reproducible instance of that stack. What it is not is safety; that argument is in the hardware-and-silicon repository.
