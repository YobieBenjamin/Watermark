"""Self-validation of the reference stack (runs offline in ~1 minute on one CPU)."""
from __future__ import annotations

import numpy as np
import pytest
from scipy.stats import kstest

from textgrain_ref.attacks import AttackContext, dilute, homoglyph, piggyback, rewrite, word_autoformat, zero_width
from textgrain_ref.canonicalize import canonicalize
from textgrain_ref.detector import Detector
from textgrain_ref.harness import ROOT
from textgrain_ref.lm.ngram import load_or_train
from textgrain_ref.ot import entropy, kl_from_independence, solve_block_ot
from textgrain_ref.prf import KeyedPRF, detection_score_from_uniform
from textgrain_ref.registry import Registry
from textgrain_ref.retrieval import HashingEmbedder, RetrievalIndex
from textgrain_ref.watermark import TextGrainConfig, TextGrainSampler, generate

KEY = bytes(range(32))


@pytest.fixture(scope="session")
def lm():
    return load_or_train(ROOT / "data" / "corpus", ROOT / ".cache")


@pytest.fixture(scope="session")
def human_text():
    return canonicalize((ROOT / "data" / "human" / "austen-sense.txt").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def cfg():
    return TextGrainConfig(n_blocks=32, n_columns=16, beta=0.5, context_window=3)


# ----------------------------------------------------------------------------- PRF

def test_prf_is_deterministic_and_context_sensitive():
    prf = KeyedPRF(KEY)
    s1, s2 = prf.seeds([1, 2, 3]), prf.seeds([1, 2, 3])
    assert s1 == s2
    assert prf.seeds([1, 2, 4]) != s1
    assert KeyedPRF(bytes(range(1, 33))).seeds([1, 2, 3]) != s1


def test_partition_block_of_matches_full_partition():
    prf = KeyedPRF(KEY)
    seeds = prf.seeds([5, 6, 7])
    g = prf.partition(seeds.partition, 5000, 32)
    for tok in (0, 17, 4999, 2048):
        assert prf.block_of(seeds.partition, tok, 32) == g[tok]
    counts = np.bincount(g, minlength=32)
    assert counts.min() > 5000 / 32 * 0.6 and counts.max() < 5000 / 32 * 1.4


def test_cost_uniforms_are_uniform_and_random_access():
    prf = KeyedPRF(KEY)
    seeds = prf.seeds([9, 9, 9])
    u = prf.cost_uniforms(seeds.cost, 64, 64)
    assert u.min() > 0 and u.max() < 1
    assert kstest(u.ravel(), "uniform").pvalue > 1e-4
    assert prf.cost_uniform(seeds.cost, 3, 5, 64) == pytest.approx(u[3, 5])


# ------------------------------------------------------------------------------ OT

def test_ot_marginals_and_entropy_budget():
    rng = np.random.default_rng(0)
    p = rng.dirichlet(np.full(500, 0.3))
    prf = KeyedPRF(KEY)
    seeds = prf.seeds([1, 2, 3])
    g = prf.partition(seeds.partition, 500, 32)
    rho = np.bincount(g, weights=p, minlength=32)
    cost = prf.cost_table(seeds.cost, 32, 16)
    res = solve_block_ot(rho, cost, entropy(p), 0.3, 16, n_iter=80, tol=0.01)
    pi = res.coupling
    assert np.allclose(pi.sum(1), rho, atol=1e-9)
    assert np.allclose(pi.sum(0), 1 / 16, atol=1e-9)
    assert pi.min() >= 0
    assert abs(res.beta_achieved - 0.3) < 0.03
    assert kl_from_independence(pi, rho, 16) == pytest.approx(res.beta_achieved * entropy(p))


def test_zero_budget_is_independent_coupling():
    rho = np.array([0.5, 0.3, 0.2])
    res = solve_block_ot(rho, np.zeros((3, 4)), 1.0, 0.0, 4)
    assert np.allclose(res.coupling, np.outer(rho, np.full(4, 0.25)))


# ------------------------------------------------------------------------ embedding

def test_unbiasedness_and_entropy_identity(cfg):
    """m^-1 sum_j Q(.|j) = P and H(P) - mean_j H(Q(.|j)) = KL(pi || rho x u_m) (Theorem A.4)."""
    rng = np.random.default_rng(1)
    v = 2000
    p = rng.dirichlet(np.full(v, 0.2))
    sampler = TextGrainSampler(KEY, cfg, v)
    ctx = [10, 20, 30]
    seeds = sampler.prf.seeds(ctx)
    g = sampler.prf.partition(seeds.partition, v, cfg.n_blocks)
    rho = np.bincount(g, weights=p, minlength=cfg.n_blocks)
    cost = sampler.prf.cost_table(seeds.cost, cfg.n_blocks, cfg.n_columns)
    res = solve_block_ot(rho, cost, entropy(p), cfg.beta, cfg.n_columns)
    qs = []
    for j in range(cfg.n_columns):
        r = cfg.n_columns * res.coupling[:, j]
        scale = np.where(rho > 0, r / np.where(rho > 0, rho, 1), 0)
        q = p * scale[g]
        assert q.sum() == pytest.approx(1.0, abs=1e-9)
        qs.append(q)
    avg = np.mean(qs, axis=0)
    assert np.allclose(avg, p, atol=1e-9)
    mean_h = np.mean([entropy(q) for q in qs])
    assert entropy(p) - mean_h == pytest.approx(kl_from_independence(res.coupling, rho, cfg.n_columns), abs=1e-6)
    # and the sampler's Q for the keyed column is one of them
    q_keyed, info = sampler.distribution(p, ctx)
    assert np.allclose(q_keyed, qs[info.column], atol=1e-9)


def test_repeated_context_is_masked(cfg):
    sampler = TextGrainSampler(KEY, cfg, 100)
    p = np.full(100, 0.01)
    _, i1 = sampler.distribution(p, [1, 2, 3])
    _, i2 = sampler.distribution(p, [1, 2, 3])
    assert not i1.masked and i2.masked


# ------------------------------------------------------------------------- detector

def test_null_scores_are_unit_exponential(lm, cfg, human_text):
    det = Detector(KEY, lm.encode, cfg)
    ids = lm.encode(human_text[20000:60000])
    y = det.position_scores(ids)
    y = y[~np.isnan(y)]
    assert y.size > 3000
    assert kstest(y, "expon").pvalue > 1e-3
    assert abs(y.mean() - 1.0) < 0.05


def test_false_positive_rate_is_controlled(lm, cfg, human_text):
    det = Detector(KEY, lm.encode, cfg, alpha=0.01)
    hits = 0
    n = 120
    for i in range(n):
        chunk = human_text[1000 + i * 2200: 1000 + i * 2200 + 2000]
        hits += det.detect(chunk).detected
    assert hits / n <= 0.05


def test_watermark_is_detected_and_plain_text_is_not(lm, cfg):
    rng = np.random.default_rng(3)
    sampler = TextGrainSampler(KEY, cfg, lm.vocab_size)
    det = Detector(KEY, lm.encode, cfg)
    prompt = lm.encode("The family of Dashwood had long been settled")
    ids = generate(lm, prompt, 200, sampler, rng, context_window=cfg.context_window)
    r = det.detect(lm.decode(ids))
    assert r.detected and r.p_value < 1e-6 and r.z_score > 8
    plain = generate(lm, prompt, 200, None, rng, context_window=cfg.context_window)
    assert det.detect(lm.decode(plain)).p_value > 1e-3
    wrong_key = Detector(bytes(range(32, 64)), lm.encode, cfg)
    assert wrong_key.detect(lm.decode(ids)).p_value > 1e-3


def test_same_prompt_same_key_gives_distinct_outputs(lm, cfg):
    sampler = TextGrainSampler(KEY, cfg, lm.vocab_size)
    prompt = lm.encode("Emma Woodhouse, handsome, clever, and rich")
    a = generate(lm, prompt, 60, sampler, np.random.default_rng(1), context_window=3)
    b = generate(lm, prompt, 60, sampler, np.random.default_rng(2), context_window=3)
    assert a != b


# ------------------------------------------------------------------------ hardening

def _wm_text(lm, cfg, seed=5, n=220):
    sampler = TextGrainSampler(KEY, cfg, lm.vocab_size)
    prompt = lm.encode("Marianne was silent; it was impossible")
    return lm.decode(generate(lm, prompt, n, sampler, np.random.default_rng(seed), context_window=cfg.context_window))


def test_canonicalization_defeats_desync_attacks(lm, cfg):
    text = _wm_text(lm, cfg)
    actx = AttackContext(lm=lm, rng=np.random.default_rng(0), context_window=3)
    hardened = Detector(KEY, lm.encode, cfg, canonicalize_input=True)
    naive = Detector(KEY, lm.encode, cfg, canonicalize_input=False)
    base = hardened.detect(text).z_score
    for attack in (homoglyph, zero_width, word_autoformat):
        attacked = attack(text, actx).text if attack is not word_autoformat else attack(text, actx).text
        assert canonicalize(attacked) == canonicalize(text) or attack is word_autoformat
        z_hard = hardened.detect(attacked).z_score
        z_naive = naive.detect(attacked).z_score
        assert z_hard > 0.8 * base, attack.__name__
        if attack is not word_autoformat:
            assert z_naive < 0.6 * base, attack.__name__


def test_rewrite_degrades_and_piggyback_does_not(lm, cfg):
    text = _wm_text(lm, cfg)
    det = Detector(KEY, lm.encode, cfg)
    base = det.detect(text).z_score
    actx = AttackContext(lm=lm, rng=np.random.default_rng(0), context_window=3)
    heavy = rewrite(text, actx, p=0.5).text
    assert det.detect(heavy).z_score < 0.5 * base
    spoof = piggyback(text, actx, k=3).text
    assert det.detect(spoof).detected


def test_localization_finds_watermarked_span_in_human_document(lm, cfg, human_text):
    text = _wm_text(lm, cfg, n=200)
    pool = [human_text[50000:90000]]
    actx = AttackContext(lm=lm, rng=np.random.default_rng(4), human_pool=pool, context_window=3)
    diluted = dilute(text, actx, ratio=4.0)
    det = Detector(KEY, lm.encode, cfg, token_spans=lm.tok.token_spans)
    loc = det.localize(diluted.text, window=80, stride=20)
    assert loc.segments, "no segment found"
    a, b = diluted.meta["span_tokens"]
    best = max(min(b, s.token_end) - max(a, s.token_start) for s in loc.segments)
    assert best > 0.5 * (b - a)
    assert all(s.text for s in loc.segments)


# ---------------------------------------------------------------- retrieval / registry

def test_retrieval_and_registry_verdicts(lm, cfg, tmp_path, human_text):
    texts = [_wm_text(lm, cfg, seed=s, n=150) for s in range(4)]
    index = RetrievalIndex(HashingEmbedder())
    reg = Registry(tmp_path / "r.sqlite", Registry.generate_key())
    d2r = {}
    for i, t in enumerate(texts):
        index.add(f"wm-{i}", t)
        d2r[f"wm-{i}"] = reg.register(t, model="toy").record_id
    # exact
    v = reg.verify(texts[1], index, d2r)
    assert v.status == "exact" and v.signature_valid and v.record_id == d2r["wm-1"]
    # tampered: small edit -> nearest original + diff spans
    actx = AttackContext(lm=lm, rng=np.random.default_rng(0), context_window=3)
    spoof = piggyback(texts[2], actx, k=2).text
    v = reg.verify(spoof, index, d2r)
    assert v.status == "tampered" and v.record_id == d2r["wm-2"] and v.diff and v.changed_fraction < 0.1
    # unknown: human text
    assert reg.verify(human_text[3000:4500], index, d2r).status == "unknown"
    # retrieval recall for a moderate rewrite (lexical embedder keeps ~75% of tokens)
    r = rewrite(texts[3], actx, p=0.25).text
    assert index.query(r, k=1)[0].doc_id == "wm-3"
    # signature tamper-evident
    rec = reg.get(d2r["wm-0"])
    rec.model = "forged"
    assert not reg.verify_signature(rec)
    # persistence
    index.save(tmp_path / "idx")
    loaded = RetrievalIndex.load(tmp_path / "idx", HashingEmbedder())
    assert loaded.query(texts[1], k=1)[0].doc_id == "wm-1"
