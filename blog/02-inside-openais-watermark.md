# Inside OpenAI's Watermark: What the Study Found

*Yobie Benjamin · 7 October 2026*

This is the technical companion to [Why an Invisible Stamp Won't Keep AI Safe](01-why-an-invisible-stamp-wont-keep-ai-safe.md). It stays in plain words, but it goes all the way down: how OpenAI's textGrain watermark actually works, how our test copy of it is built, what 13 attacks did to it, and what that tells us about where AI safety has to live. Every number comes from code you can run in one command, and every code excerpt links to the exact lines on GitHub.

- The study: [github.com/YobieBenjamin/Watermark](https://github.com/YobieBenjamin/Watermark) (commit `71119df`)
- OpenAI's design: *textGrain: Entropy-Calibrated Watermarking for Language Model Text*, technical report (Li, Wen, Chen, Long, Jain, Joly, Lam, Song, Su), 5 October 2026

One thing to be clear about up front: OpenAI built the watermark. We did not. We built a test copy of the published design so we could attack it in the open, with numbers anyone can check, and then we wrote down what survived.

## The one idea behind it

Every time a language model writes a word, it first produces a list: every word in its vocabulary, each with a probability. "The cat sat on the…" might give *mat* 40 percent, *floor* 20 percent, *couch* 10 percent, and so on down a list of 50,000 entries. Then it rolls the dice and picks one.

A watermark tilts those dice in a secret pattern. The reader can't see the tilt. The key holder can measure it.

The hard part is doing that without making the text worse. Earlier schemes (the "green list" watermark of Kirchenbauer et al., 2023, for instance) tilted every word by a fixed amount. That costs quality where the model was sure and buys little where it was already unsure. textGrain's contribution is to spend the tilt where it is cheap: a lot where the model is torn between many good words, almost none where one word is obviously right. The amount it spends is set by a single number, the entropy budget, and that number has an exact information-theoretic meaning. We'll get to it.

## The five keyed ingredients

Everything the watermark needs at a given position is computed from two things: the secret key and the last few tokens of context (we use a window of 3). The code derives three seeds from them with HMAC-SHA256 and expands each seed into what it needs with a fast random-access mixer.
([prf.py, lines 72–82](https://github.com/YobieBenjamin/Watermark/blob/71119df/textgrain_ref/prf.py#L72-L82))

```python
def seeds(self, context: Sequence[int]) -> Seeds:
    ctx = np.asarray(list(context), dtype=np.uint32)
    msg = len(ctx).to_bytes(4, "little") + ctx.tobytes()
    d = hmac.new(self.key, msg, hashlib.sha256).digest()
    return Seeds(
        partition=int.from_bytes(d[0:8], "little"),
        cost=int.from_bytes(d[8:16], "little"),
        column=int.from_bytes(d[16:24], "little"),
    )
```

From those seeds come the five ingredients:

1. **A partition.** Every word in the vocabulary is assigned to one of B blocks (we use 32). Think of each word getting a hidden color, chosen freshly at every position, that only the key holder can see.
2. **A cost table.** A B-by-m table of random numbers (m is 16 columns) drawn from a Gumbel distribution. This is the "shape" of the tilt.
3. **A column.** One of the m columns is secretly chosen for this position. Call it J.
4. **A context window.** The last 3 tokens. Because the seeds depend only on the key and this window, detection needs no state: cut a paragraph out and paste it anywhere, and the keyed values at each position are still the same.
5. **A repeat mask.** If the same 3-token window shows up twice in one response, the second occurrence is sampled with no watermark at all. The detector mirrors this and scores only first occurrences. That removes a classic way to inflate or spoof a score with repeated phrases.

Random access is what makes this practical. The detector doesn't rebuild the whole table; it recomputes the one block a word fell in and the one cell it needs.
([prf.py, lines 84–122](https://github.com/YobieBenjamin/Watermark/blob/71119df/textgrain_ref/prf.py#L84-L122))

```python
@staticmethod
def block_of(seed: int, token: int, n_blocks: int) -> int:
    return int(_mix(seed, np.array([token], dtype=np.uint64))[0]
               % np.uint64(n_blocks))

@staticmethod
def cost_uniform(seed, block, column, n_columns) -> float:
    idx = np.array([block * n_columns + column], dtype=np.uint64)
    return float(_to_uniform(_mix(seed, idx))[0])

@staticmethod
def column(seed: int, n_columns: int) -> int:
    return int(_mix(seed, np.array([0], dtype=np.uint64))[0]
               % np.uint64(n_columns))
```

A note the code itself makes: the mixer (splitmix64) is fine for a reference implementation because its input is a secret HMAC output, but a production system should expand seeds with a keyed block cipher (AES-CTR or ChaCha20) so the expansion is a real pseudorandom function.

## The entropy budget, in plain words

Here is the step that makes textGrain different. The model's list of word probabilities is first summed up by block: block b holds probability ρ_b (the Greek letter rho; just "how much of the model's belief sits in block b"). The sampler then has to decide how to couple blocks to columns: a B-by-m table π (pi) whose rows sum to ρ and whose columns each sum to 1/m. It wants to make the keyed column J "prefer" certain blocks, as the cost table says, while changing the model's choices as little as possible.

That is an optimal-transport problem with a knob on it:

```text
minimize   <π, cost>  +  λ · KL( π || ρ ⊗ uniform )
```

The first term pushes toward the watermark. The second term is a penalty for moving away from "no watermark" (rows independent of columns), and λ (lambda) is the knob. The code solves it with Sinkhorn iterations in the log domain, which is the standard fast method (Cuturi, 2013), and then applies a marginal correction (Altschuler, Niles-Weed, Rigollet, 2017) so the row and column sums come out exact before anything is sampled.

Now the key fact. The KL penalty is not just a convenient regularizer. It equals, exactly, the average amount of sampling entropy the watermark removes from the model's choice. So the budget β (beta) is defined as

```text
β  =  KL / H(P)
```

the fraction of the model's uncertainty at this position that the watermark is allowed to consume. β = 0 means no watermark. β = 1 would mean the model's choice is fully determined by the key, which is what the older Gumbel-max scheme (Aaronson, 2023) does at every position. The solver adjusts λ until the achieved fraction lands within a tolerance of the requested β.
([ot.py, lines 65–127](https://github.com/YobieBenjamin/Watermark/blob/71119df/textgrain_ref/ot.py#L65-L127))

```python
# caps from the information bound (Theorem A.4)
beta = min(beta, (1.0 - 1e-8) * h_rho / entropy_p,
           np.log(n_columns) / entropy_p)
...
for s in range(n_iter):
    log_k = gain / lam
    logu = log_rho - logsumexp(log_k + logv[None, :], axis=1)
    logv = log_um - logsumexp(log_k + logu[:, None], axis=0)
    ...
    pi = marginal_correction(np.exp(logpi), rho_k, um)
    beta_hat = kl_from_independence(pi, rho_k, n_columns) / entropy_p
    if abs(beta_hat - beta) <= tol:
        break
    lam_new = float(np.clip(lam * (max(beta_hat, delta) / beta) ** step,
                            lam_min, lam_max))
    logv = (lam / lam_new) * logv
    lam = lam_new
```

Two caps appear on the first line. You cannot remove more entropy than the blocks carry, and you cannot signal more than log(m) nats through a choice among m columns. Both come from the report's information bound.

Once π is solved, the sampler reads off the column J it secretly drew, scales every block by how much column J prefers it, and scales each word inside a block by the same factor as its block. Then it samples.
([watermark.py, lines 64–102](https://github.com/YobieBenjamin/Watermark/blob/71119df/textgrain_ref/watermark.py#L64-L102))

```python
b, m = self.cfg.n_blocks, self.cfg.n_columns
seeds = self.prf.seeds(ctx)
# every word gets a block
g = self.prf.partition(seeds.partition, self.vocab_size, b)
# probability mass per block
rho = np.bincount(g, weights=p, minlength=b)
cost = self.prf.cost_table(seeds.cost, b, m)
res = solve_block_ot(rho, cost, info.entropy_p, self.cfg.beta, m,
                     n_iter=self.cfg.ot_iterations,
                     tol=self.cfg.ot_tolerance)
col = self.prf.column(seeds.column, m)
# conditional block probabilities given the secret column
r = m * res.coupling[:, col]
scale = np.where(rho > 0, r / np.where(rho > 0, rho, 1.0), 0.0)
# nudge every word by its block's factor
q = np.maximum(p * scale[g], 0.0)
q = q / q.sum()
```

Two identities hold exactly, and the test suite checks both:

- **Unbiased.** Average the tilted distribution over all m columns and you get the model's original distribution back. Without the key, the text is drawn from the model's own distribution; the watermark is invisible in expectation.
- **Entropy identity.** The model's entropy minus the average entropy of the tilted distributions equals the KL penalty. That is why β has the meaning it has.

And one more consequence worth noticing: the watermark does not make the model deterministic. In our runs, the same prompt with the same key produced a distinct output 100 percent of the time. A Gumbel-max watermark would produce the identical text every time.

## Detection

The detector needs the key and the tokenizer. It does not need the model, and it does not need to know the budget. For every scored position it recomputes the seeds from the context window, finds which block the observed word fell in, finds the secret column, pulls the one uniform random number behind that cell of the cost table, and converts it to a score.
([detector.py, lines 97–131](https://github.com/YobieBenjamin/Watermark/blob/71119df/textgrain_ref/detector.py#L97-L131))

```python
for t in range(L, len(ids)):
    ctx = tuple(ids[t - L:t]) if L > 0 else ()
    if L > 0 and ctx in seen:      # repeat mask, mirrored
        continue
    seen.add(ctx)
    seeds = self.prf.seeds(ctx)
    b = self.prf.block_of(seeds.partition, ids[t], B)
    col = self.prf.column(seeds.column, m)
    u = self.prf.cost_uniform(seeds.cost, b, col, m)
    y[t] = detection_score_from_uniform(u)   # -log(1 - u)
```

Why that score? If the text has nothing to do with the key, the number u behind the cell is just a uniform random draw, and −log(1 − u) is then a standard exponential random variable. Add n of them and you get a Gamma(n, 1) distribution. So the test is clean:

```python
# chance a human scored this high
p = float(gamma.sf(statistic, a=n_scored, scale=1.0))
# signal strength
z = float((statistic - n_scored) / np.sqrt(n_scored))
thr = float(gamma.ppf(1.0 - alpha, a=n_scored, scale=1.0))
```

Watermarked text lands on high-u cells far more often than chance, the sum runs ahead of n, and the p-value collapses. The z value is the "signal strength" you'll see in the tables below: roughly, how many standard deviations above chance the text sits.

The report warns that a fixed deployed key needs empirical calibration, and our runs show why: at a nominal 1 percent false-alarm rate, the realized rate on human text was 1.7 percent. The harness therefore also reports an empirical threshold from the null sample (−log10 p = 3.14 in both runs) and scores every attack against it too.

## Hardening: two things a real detector needs

**Clean up before counting.** Attackers can change the bytes of a text without changing what a reader sees: insert zero-width characters, swap in look-alike letters from Cyrillic or Greek, or just let a word processor convert quotes and dashes. Each of those shifts every downstream context window, and the keyed values stop lining up. Canonicalizing the text first makes all of them no-ops. The registry hashes canonicalized text for the same reason.
([canonicalize.py, lines 69–77](https://github.com/YobieBenjamin/Watermark/blob/71119df/textgrain_ref/canonicalize.py#L69-L77))

```python
def canonicalize(text: str) -> str:
    """NFKC -> zero-width -> confusables -> typography -> whitespace."""
    text = unicodedata.normalize("NFKC", text)
    text = strip_zero_width(text)
    text = map_confusables(text)
    text = normalize_typography(text)
    text = _WS.sub(" ", text)
    text = _MULTI_NL.sub("\n", text)
    return text.strip()
```

**Find the stamped part.** Paste two watermarked paragraphs into a long human essay and the passage-level statistic looks normal. The localizer slides a 120-token window in steps of 30, tests each window with the same Gamma null, corrects for the number of windows (Bonferroni), merges the windows that fire, and maps them back to character positions.
([detector.py, lines 137–183](https://github.com/YobieBenjamin/Watermark/blob/71119df/textgrain_ref/detector.py#L137-L183))

```python
for st in starts:
    seg = y[st:st + window]
    scored = seg[~np.isnan(seg)]
    ...
    p, _, _ = self.test(s, n, alpha)
    p_corr = min(1.0, p * n_windows)     # Bonferroni
    if p_corr < alpha:
        hits.append((st, min(st + window, n_tok), n, s, p_corr))
```

In the dilution attack below (one watermarked passage buried in three times as much human text) the localizer found the stamped span every time, with a 0.73 to 0.77 overlap with the true span.

## Two more layers: retrieval and a signed registry

A token-level watermark provably cannot survive a good paraphrase (Zhang et al., 2024, *Watermarks in the Sand*; Krishna et al., 2023). So the study adds the two things a provider would deploy around the detector.

**Semantic retrieval.** Every registered output is split into sentences, embedded, and stored in a FAISS inner-product index. A query embeds the suspect text's sentences and scores each candidate document by the mean of its best per-sentence similarities, plus coverage and a count of near-identical sentences so diluted documents still resolve. Offline, the harness uses a character n-gram hashing embedder (lexical overlap only); the real thing uses a multilingual sentence encoder, selectable with `--embedder`.
([retrieval.py, lines 95–140](https://github.com/YobieBenjamin/Watermark/blob/71119df/textgrain_ref/retrieval.py#L95-L140))

**A signed registry.** `register(text)` stores the SHA-256 of the canonicalized text with an Ed25519 signature over the hash, the model name and a timestamp. `verify(text)` returns one of three verdicts: exact, tampered (with the diff), or unknown.
([registry.py, lines 148–170](https://github.com/YobieBenjamin/Watermark/blob/71119df/textgrain_ref/registry.py#L148-L170))

```python
def verify(self, text, index=None, doc_to_record=None,
           similarity_threshold=0.5) -> Verdict:
    rec = self.lookup_exact(text)
    if rec is not None:
        return Verdict("exact", rec.record_id, 1.0,
                       self.verify_signature(rec), 0.0, [])
    if index is None:
        return Verdict("unknown")
    hits = index.query(text, k=1)
    strong = bool(hits) and (hits[0].score >= similarity_threshold
                             or hits[0].n_strong >= 2)
    if not strong:
        return Verdict("unknown",
                       similarity=hits[0].score if hits else 0.0)
    ...
    diff, changed = token_diff(original, text)
    return Verdict("tampered", rec_id, float(hit.score), ...,
                   changed, diff)
```

This closes the piggyback hole: edit three tokens of a genuine output and the watermark is still there, but the text no longer hashes to anything the provider signed, and the diff shows exactly which tokens moved.

## The 13 attacks

The harness generates 30 watermarked samples of 300 tokens, runs each attack, and pushes the result through the naive detector, the hardened detector, retrieval and the registry. It calibrates on human text and on unwatermarked model text. The two committed runs differ only in the budget: β = 0.5 and β = 0.2. All thirteen attacks are short functions in [attacks.py](https://github.com/YobieBenjamin/Watermark/blob/71119df/textgrain_ref/attacks.py); the full tables are in [results/toy-beta0.5](https://github.com/YobieBenjamin/Watermark/blob/71119df/results/toy-beta0.5/report.md) and [results/toy-beta0.2](https://github.com/YobieBenjamin/Watermark/blob/71119df/results/toy-beta0.2/report.md).

| Attack | Words changed | Hardened detection, β=0.5 | Hardened detection, β=0.2 | Signal z, β=0.5 | Signal z, β=0.2 | Registry |
|---|---|---|---|---|---|---|
| Copy-paste | 0% | 100% | 100% | 26.9 | 19.7 | exact |
| Word autoformat | 0% | 100% | 100% | 26.9 | 19.7 | exact |
| Zero-width characters | 0% | 100% (naive: 100%) | 100% (naive: 83%) | 26.9 (naive 5.3) | 19.7 (naive 3.8) | exact |
| Look-alike letters | 0% | 100% | 100% | 26.9 (naive 8.4) | 19.7 (naive 6.3) | exact |
| Rewrite 10% of words | 16% | 100% | 100% | 17.8 | 12.8 | tampered + diff |
| Rewrite 25% of words | 34% | 100% | 100% (97% at empirical threshold) | 8.7 | 6.4 | tampered + diff |
| Rewrite 50% of words | 73% | 20% (7% at empirical threshold) | 17% (0% at empirical threshold) | 1.6 | 1.1 | unknown |
| Delete 10% | 15% | 100% | 100% | 18.6 | 13.5 | tampered |
| Insert 10% | 13% | 100% | 100% | 18.9 | 13.7 | tampered |
| Cut to half | 50% | 100% | 100% | 19.3 | 14.0 | tampered |
| Cut to a quarter | 73% | 100% | 100% | 13.8 | 10.0 | tampered |
| Bury in 3× human text | 54% | 100%, span found | 100%, span found | 17.9 | 13.1 | tampered |
| Piggyback spoof (3-token edit) | 1% | 100% | 100% | 25.9 | 18.8 | tampered + diff |

Retrieval found the original document first, 100 percent of the time, in every row of both runs, including the heavy rewrite. That is the layer that survives paraphrase. But it only works for text the provider indexed.

Four things to read off the table.

**Formatting attacks are solved problems.** The naive detector lost most of its signal to zero-width characters (z from 27 to 5) and look-alike letters (27 to 8). Canonicalization restored all of it. At the smaller budget the naive detector actually missed 17 percent of zero-width samples; the hardened one missed none.

**Rewriting is the real attack, and no setting fixes it.** Change 10 or 25 percent of the words and the signal survives comfortably. Change half and detection drops to 20 percent at the nominal threshold, 7 percent at the calibrated one, with a z of 1.6, which is noise. The stamp is physically gone from the words that were replaced; there is nothing left to detect.

**The budget is a dial between quality and robustness.** Halving β from 0.5 to 0.2 lowers every z by about a quarter and pushes the 25-percent-rewrite row down to 97 percent at the calibrated threshold. A provider will run below 0.5 to protect quality, which means the real-world margins are thinner than the top line here.

**The other layers only help if you were there first.** The registry's "tampered + diff" verdicts and retrieval's perfect recall both depend on the provider having signed and indexed the text at generation time. For text that never passed through the provider, both return "unknown."

## What transfers from the toy model, and what doesn't

The offline runs use a small n-gram model with about 3.7 nats of entropy per token, two to three times what an instruction-tuned LLM has at temperature 1 with top-p. More entropy means more room for the watermark, so the absolute detection rates here are optimistic. What transfers directly: the ordering of the attacks, the hardening effect, the dilution localization, and the registry and retrieval behavior. The harness has an `hf` backend for real models, and two attacks that only exist there: paraphrase by an unwatermarked instruct model, and a round trip through a pivot language.

Two things the study deliberately does not implement are watermark stealing and detector hill-climbing. Both need either tens of thousands of queries or oracle access to the detector, which is exactly why a detector has to be gated, rate-limited and logged. The hooks to study them are there.

## Why a watermark is limited by design

None of the limits below are bugs. They follow from what a watermark is.

1. **Only the key holder can check.** OpenAI can check OpenAI's text. No one else can, and OpenAI cannot check anyone else's.
2. **It is applied while the text is written, inside the provider's sampler.** A model running on someone else's machine applies no tilt unless its owner chooses to. Owners who want to hide have no reason to choose to.
3. **Open weights carry no stamp.** Thousands of models are free to download. Many have had their safety training stripped out on purpose by "abliteration": find the direction in activation space that encodes refusal, subtract it from the weights, done in an afternoon on a consumer GPU. Those models are unmarked and unmarkable.
4. **A paraphrase by any unmarked model erases it.** The attacker never needs the key. Our 50-percent rewrite is a crude version of that attack and already pushes detection to noise.

So a watermark answers one narrow question: did this provider's model write these exact words, unchanged? That is provenance. It is useful for attribution and for the EU AI Act's Article 50 duty to make AI text detectable. It is irrelevant to the question people are actually afraid of: can this model take an action it shouldn't?

## Where this goes

The study ends with a design decision that points past watermarks. The watermark layer never decides anything. It only emits a signed statement, a provenance assertion of the form

```text
{ type: "provenance-assertion", registry, content_digest,
  status: exact | tampered | unknown, issued_at, expires_at }
```

and hands it to something below the model that *does* decide. In our architecture that something is a control plane rooted in silicon: a gate on the only path from the model to the outside world, which opens for an outgoing action only when a signed rulebook allows it, when the chip has proved what it is running, and when the witnesses the rule demands (a provenance verdict, an observer's verdict) have signed off. A watermark verdict is one witness among several. The lock is the chip.

That is the subject of the next post, [A Multi-Layer Approach to AI Safety](https://github.com/YobieBenjamin/hardware-and-silicon/blob/main/blog/01-a-multi-layer-approach-to-ai-safety.md), and of the repository it lives in, [hardware-and-silicon](https://github.com/YobieBenjamin/hardware-and-silicon), which is built to sit on NVIDIA's platform, because that is where the models run. The one after that is the comprehensive silicon design.

## Run it yourself

```bash
git clone https://github.com/YobieBenjamin/Watermark
# offline toy model: venv, tests, full sweep
cd Watermark && ./run.sh
# regenerates both committed runs
bash results/reproduce.sh
./run.sh hf --model Qwen/Qwen2.5-1.5B-Instruct \
  --embedder paraphrase-multilingual-MiniLM-L12-v2 --llm-attacks
```

The last command runs a real model with the paraphrase and translation attacks included (see [docs/USAGE.md](https://github.com/YobieBenjamin/Watermark/blob/71119df/docs/USAGE.md)). The design is audited module by module against the report in [docs/DESIGN.md](https://github.com/YobieBenjamin/Watermark/blob/71119df/docs/DESIGN.md).

*References: OpenAI, textGrain technical report (5 October 2026); Aaronson (2023), Gumbel-max watermark; Kirchenbauer et al. (2023), green-list watermark; Dathathri et al. (2024), SynthID-Text; Kuditipudi et al. (2024), distortion-free watermarks; Christ, Gunn, Zamir (2024), undetectable watermarks; Zhang et al. (2024), Watermarks in the Sand; Krishna et al. (2023), paraphrasing evades detectors; Jovanović et al. (2024), watermark stealing; Pang et al. (2024), no free lunch in LLM watermarking; Cuturi (2013), Sinkhorn distances; Altschuler, Niles-Weed, Rigollet (2017), Sinkhorn rounding. This post is documentation under CC BY-NC 4.0; the code is under the PolyForm Noncommercial License 1.0.0; attribution required, commercial use by separate license (see [LICENSING.md](https://github.com/YobieBenjamin/autonomic-graph-regulation/blob/main/LICENSING.md)).*
