# Design notes

This document explains what each module computes and why, in enough detail to audit
the implementation against OpenAI's textGrain technical report (Li, Wen, Chen, Long,
Jain, Joly, Lam, Song, Su; 5 October 2026) and the surrounding literature.

## 1. Embedding (`textgrain_ref/watermark.py`, `ot.py`, `prf.py`)

At every decoding step the model produces a next-token distribution `P` over the
vocabulary `V` (after temperature and top-p: the watermark sees exactly what the
sampler would have sampled from).

1. **Keyed partition.** `seeds = HMAC-SHA256(key, last L_ctx tokens)`. A splitmix64
   expansion of `seeds.partition` maps each token id to one of `B` blocks,
   `g : V -> [B]`. Block probabilities are `rho_b = sum_{w in block b} P_w`.
2. **Keyed cost table.** `seeds.cost` expands to `B x m` uniforms `u_bj`, turned into
   standard Gumbels `Z_bj = -log(-log u_bj)`, standardised `G_bj = (Z_bj - gamma)/(pi/sqrt 6)`,
   and `c(b, j) = -G_bj`.
3. **Entropy-budgeted coupling.** Solve

       min_{pi in Pi(rho, u_m)}  <pi, c> + lambda * KL(pi || rho (x) u_m)

   by log-domain Sinkhorn, with `lambda` adjusted (Algorithm A.1 of the report:
   multiplicative update with exponent 1/2, clipped to [1e-2, 1e2]) until
   `KL / H(P) ~= beta`. The caps `beta <= H(rho)/H(P)` and `beta <= log(m)/H(P)` come
   from the information bound `I(C; J) <= min(H(rho), log m)`. Marginal correction
   (Altschuler et al. 2017) restores exact marginals before sampling.
4. **Keyed column and token.** `J = seeds.column mod m`. Conditional block law
   `r(b | J) = m * pi(b, J)`; token law `Q(w | J) = r(g(w) | J) * P_w / rho_{g(w)}`.
   Sample the token from `Q`.

Two identities are what the tests check (`tests/test_stack.py::test_unbiasedness_and_entropy_identity`):

* **Unbiasedness.** `(1/m) sum_j Q(. | j) = P` exactly.
* **Entropy identity.** `H(P) - (1/m) sum_j H(Q(. | j)) = KL(pi || rho (x) u_m)`.
  The KL penalty *is* the average sampling entropy removed, so `beta` has a direct
  information-theoretic meaning. With `beta -> 0` the coupling is independent (no
  watermark); with the maximum attainable loss the scheme approaches a Gumbel-max
  style deterministic choice.

**Repeated contexts.** If a context window recurs during one generation, the position
is *masked* (sampled from `P` directly) so the keyed values are never reused within a
response. The detector mirrors this by scoring only the first occurrence of each
distinct window.

**Randomness.** Only the sampling step uses true randomness; everything keyed is a
deterministic function of `(key, context)`. That is what makes detection stateless
and self-synchronising under cut-and-paste.

## 2. Detection (`textgrain_ref/detector.py`)

For each scored position `t` (complete window, first occurrence):

    seeds = PRF(key, window)        b = g(w_t)        J = column(seeds)
    u     = cost_uniform(seeds, b, J)
    Y_t   = -log(1 - u)

Under the null (text independent of the key) `u ~ U(0,1)` so `Y_t ~ Exp(1)` and
`S_n = sum Y_t ~ Gamma(n, 1)`. The detector reports `p = P[Gamma(n,1) > S_n]`,
`z = (S_n - n)/sqrt(n)` and the decision `S_n > Gamma(n,1)_{1-alpha}`. It needs the
tokenizer and the key, not the model and not the entropy budget.

`tests/test_stack.py::test_null_scores_are_unit_exponential` runs a KS test of the
per-position scores on 3,000+ human tokens against Exp(1);
`test_false_positive_rate_is_controlled` checks the realised FPR on 120 human passages.
The report itself warns that a fixed deployed key needs empirical calibration, which is
why the harness also reports an empirical threshold from the null sample.

### Hardening

* **Canonicalisation before tokenisation** (`canonicalize.py`): NFKC, zero-width
  removal, a confusables map (Cyrillic/Greek look-alikes), typographic quotes and
  dashes, whitespace collapse. Attacks that change bytes but not glyphs (homoglyphs,
  zero-width joiners, word-processor auto-formatting) otherwise shift every downstream
  context window; after canonicalisation they are no-ops. The same pass is applied
  before hashing in the registry so hashes are stable across copy-paste.
* **Segment localisation** (`Detector.localize`): sliding windows (default 120 tokens,
  stride 30), each tested with the Gamma null and Bonferroni-corrected by the number
  of windows; overlapping significant windows are merged and mapped back to character
  spans. This defeats dilution: a watermarked paragraph buried in three times as much
  human text no longer hides behind a passage-level statistic.

## 3. Semantic retrieval (`textgrain_ref/retrieval.py`)

Every registered output is split into sentences, embedded, and stored in a FAISS
inner-product index. A query embeds the suspect text's sentences, takes the top-k
neighbours of each, and scores each candidate document by the mean of its best
per-sentence similarities (plus coverage and a count of near-identical sentences, so
diluted documents still resolve). Because the match is on meaning, this is the layer
that survives paraphrase and translation, which the token-level watermark provably
cannot (Zhang et al. 2024, "Watermarks in the Sand"; Krishna et al. 2023).

Offline the harness uses a character n-gram feature-hashing embedder, which captures
lexical overlap only. Use `--embedder paraphrase-multilingual-MiniLM-L12-v2` (or
`intfloat/multilingual-e5-small`) for real paraphrase/translation recall.

## 4. Signed-output registry (`textgrain_ref/registry.py`)

`register(text)` stores the SHA-256 of the canonicalised text with an Ed25519
signature over `(hash, model, timestamp)`. `verify(text)` returns

| verdict | meaning |
|---|---|
| `exact` | canonical hash matches a registered output; signature verified |
| `tampered` | no exact match, but retrieval resolves a near original; differing spans and the changed-token fraction are returned |
| `unknown` | nothing registered resembles the text |

This closes the piggyback-spoofing gap: a three-token edit of a genuine output still
carries the watermark, but it no longer hashes to anything the provider signed, and
the diff shows exactly which tokens were changed.

## 5. Attacks (`textgrain_ref/attacks.py`)

| name | tier | what it models |
|---|---|---|
| identity | baseline | copy-paste through any number of editors |
| word_autoformat | 2 | smart quotes, em-dashes, ellipses, space collapse |
| zero_width, homoglyph | 2 | tokenizer desynchronisation with no visible change |
| rewrite p | 1 | resample a fraction `p` of tokens from the unwatermarked model (synonym / paraphrase proxy) |
| delete, insert | 2 | random edits |
| truncate | 2 | chunking below a length threshold |
| dilute | 2 | hide the output inside human text |
| piggyback | 1 | minimal spoof edit of a genuine output |
| paraphrase_llm, translate_roundtrip | 1 | HF backend only: an unwatermarked instruct model rewrites or round-trips through a pivot language |

Not implemented (deliberately): watermark stealing and detector hill-climbing. Both
need either tens of thousands of queries or oracle access, and the point of gating the
detector is to make them expensive. Hooks exist (`Detector.position_scores`,
`TextGrainSampler.history`) if you want to study them.

## 6. What transfers from the toy model and what does not

The toy n-gram model has roughly 3.7 nats of entropy per token, two to three times an
instruction-tuned LLM at temperature 1 with top-p. Absolute detection rates here are
therefore optimistic; the *ordering* of attacks, the hardening effect, the dilution
localisation and the registry/retrieval behaviour transfer directly. Run the `hf`
backend to get numbers for a real model.
