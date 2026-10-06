"""Attack library.  Every attack maps watermarked text -> (text, metadata).

Threat tiers (see README):
  Tier 1  regeneration            rewrite / paraphrase_llm / translate_roundtrip
  Tier 1  piggyback spoofing      piggyback            (expect: still detected, registry says TAMPERED)
  Tier 2  tokenizer desync         homoglyph / zero_width / word_autoformat
  Tier 2  entropy & threshold      truncate / dilute
  Tier 2  edits                     delete / insert
  --      copy-paste               identity             (expect: fully detected)

`rewrite(p)` resamples a fraction p of tokens from the *unwatermarked* model
conditioned on the left context.  With the toy LM it is the stand-in for synonym
substitution and paraphrase at a controllable strength; with the HF backend the
LLM-driven attacks (`paraphrase_llm`, `translate_roundtrip`) are the real thing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

import numpy as np

from .canonicalize import HOMOGLYPHS_FOR
from .retrieval import split_sentences


@dataclass
class AttackResult:
    text: str
    meta: dict = field(default_factory=dict)


@dataclass
class AttackContext:
    lm: object                                   # exposes encode / decode / next_token_dist
    rng: np.random.Generator
    human_pool: Sequence[str] = ()
    context_window: int = 3


AttackFn = Callable[[str, AttackContext], AttackResult]

# --------------------------------------------------------------------------- #
# copy / paste and auto-formatting
# --------------------------------------------------------------------------- #

def identity(text: str, ctx: AttackContext) -> AttackResult:
    return AttackResult(text, {"tier": "baseline"})


def word_autoformat(text: str, ctx: AttackContext) -> AttackResult:
    """What a word processor does on paste: smart quotes, em-dashes, ellipses, space collapse."""
    out = re.sub(r'"(.*?)"', lambda m: "\u201c" + m.group(1) + "\u201d", text)
    out = re.sub(r"(?<=\w)'(?=\w)", "\u2019", out)
    out = re.sub(r"(?<!\w)'", "\u2018", out)
    out = out.replace("--", "\u2014").replace("...", "\u2026").replace("  ", " ")
    return AttackResult(out, {"tier": 2})


# --------------------------------------------------------------------------- #
# tokenizer desynchronisation
# --------------------------------------------------------------------------- #

def zero_width(text: str, ctx: AttackContext, p: float = 0.5) -> AttackResult:
    words = text.split(" ")
    zw = ["\u200b", "\u200c", "\u2060"]
    out = []
    n = 0
    for w in words:
        if len(w) > 2 and ctx.rng.random() < p:
            k = int(ctx.rng.integers(1, len(w)))
            w = w[:k] + zw[int(ctx.rng.integers(len(zw)))] + w[k:]
            n += 1
        out.append(w)
    return AttackResult(" ".join(out), {"tier": 2, "inserted": n, "p": p})


def homoglyph(text: str, ctx: AttackContext, p: float = 0.3) -> AttackResult:
    words = text.split(" ")
    out = []
    n = 0
    for w in words:
        if ctx.rng.random() < p:
            cands = [i for i, ch in enumerate(w) if ch in HOMOGLYPHS_FOR]
            if cands:
                i = cands[int(ctx.rng.integers(len(cands)))]
                opts = HOMOGLYPHS_FOR[w[i]]
                w = w[:i] + opts[int(ctx.rng.integers(len(opts)))] + w[i + 1:]
                n += 1
        out.append(w)
    return AttackResult(" ".join(out), {"tier": 2, "replaced": n, "p": p})


# --------------------------------------------------------------------------- #
# token-level edits using the (unwatermarked) model
# --------------------------------------------------------------------------- #

def _resample(ids: list[int], pos: int, ctx: AttackContext, temperature: float = 1.0) -> int:
    p = ctx.lm.next_token_dist(ids[:pos])
    if temperature != 1.0:
        p = p ** (1.0 / temperature)
        p = p / p.sum()
    old = ids[pos]
    for _ in range(4):                      # try to actually change the token
        tok = int(ctx.rng.choice(len(p), p=p))
        if tok != old:
            return tok
    return tok


def rewrite(text: str, ctx: AttackContext, p: float = 0.25) -> AttackResult:
    """Resample a fraction p of tokens from the unwatermarked model (synonym / paraphrase proxy)."""
    ids = ctx.lm.encode(text)
    L = max(ctx.context_window, 2)
    n = len(ids)
    if n <= L + 1:
        return AttackResult(text, {"tier": 1, "p": p, "changed": 0})
    k = int(round(p * (n - L)))
    positions = np.sort(ctx.rng.choice(np.arange(L, n), size=min(k, n - L), replace=False))
    for pos in positions:
        ids[int(pos)] = _resample(ids, int(pos), ctx)
    return AttackResult(ctx.lm.decode(ids), {"tier": 1, "p": p, "changed": int(len(positions))})


def delete(text: str, ctx: AttackContext, p: float = 0.1) -> AttackResult:
    ids = ctx.lm.encode(text)
    keep = [t for t in ids if ctx.rng.random() >= p]
    return AttackResult(ctx.lm.decode(keep), {"tier": 2, "p": p, "removed": len(ids) - len(keep)})


def insert(text: str, ctx: AttackContext, p: float = 0.1) -> AttackResult:
    ids = ctx.lm.encode(text)
    out: list[int] = []
    n_ins = 0
    for t in ids:
        out.append(t)
        if len(out) >= 2 and ctx.rng.random() < p:
            out.append(int(ctx.rng.choice(ctx.lm.vocab_size, p=ctx.lm.next_token_dist(out))))
            n_ins += 1
    return AttackResult(ctx.lm.decode(out), {"tier": 2, "p": p, "inserted": n_ins})


def piggyback(text: str, ctx: AttackContext, k: int = 3) -> AttackResult:
    """Spoofing: minimal edit of a genuine output (a flipped negation, a changed number...)."""
    ids = ctx.lm.encode(text)
    L = max(ctx.context_window, 2)
    if len(ids) <= L + k:
        return AttackResult(text, {"tier": 1, "k": 0})
    positions = ctx.rng.choice(np.arange(L, len(ids)), size=k, replace=False)
    for pos in positions:
        ids[int(pos)] = _resample(ids, int(pos), ctx, temperature=1.5)
    return AttackResult(ctx.lm.decode(ids), {"tier": 1, "k": k, "spoof": True})


# --------------------------------------------------------------------------- #
# length / mixture attacks
# --------------------------------------------------------------------------- #

def truncate(text: str, ctx: AttackContext, n_tokens: int = 150) -> AttackResult:
    ids = ctx.lm.encode(text)[:n_tokens]
    return AttackResult(ctx.lm.decode(ids), {"tier": 2, "n_tokens": n_tokens})


def dilute(text: str, ctx: AttackContext, ratio: float = 3.0) -> AttackResult:
    """Embed the watermarked text inside `ratio` times as much human text (sentence boundary)."""
    if not ctx.human_pool:
        return AttackResult(text, {"tier": 2, "ratio": 0})
    n_wm = len(ctx.lm.encode(text))
    target = int(ratio * n_wm)
    human = ctx.human_pool[int(ctx.rng.integers(len(ctx.human_pool)))]
    sents = split_sentences(human)
    acc: list[str] = []
    total = 0
    for s in sents:
        acc.append(s)
        total += len(ctx.lm.encode(s))
        if total >= target:
            break
    cut = int(ctx.rng.integers(0, len(acc) + 1)) if acc else 0
    before = " ".join(acc[:cut])
    after = " ".join(acc[cut:])
    out = " ".join(x for x in (before, text, after) if x)
    start_tok = len(ctx.lm.encode(before)) if before else 0
    return AttackResult(out, {"tier": 2, "ratio": ratio, "span_tokens": [start_tok, start_tok + n_wm]})


# --------------------------------------------------------------------------- #
# LLM-driven attacks (HF backend only)
# --------------------------------------------------------------------------- #

def paraphrase_llm(text: str, ctx: AttackContext) -> AttackResult:
    if not hasattr(ctx.lm, "paraphrase"):
        raise NotImplementedError("paraphrase_llm needs the HF backend")
    return AttackResult(ctx.lm.paraphrase(text, seed=int(ctx.rng.integers(1 << 30))), {"tier": 1, "llm": True})


def translate_roundtrip(text: str, ctx: AttackContext, pivot: str = "French") -> AttackResult:
    if not hasattr(ctx.lm, "translate_roundtrip"):
        raise NotImplementedError("translate_roundtrip needs the HF backend")
    return AttackResult(ctx.lm.translate_roundtrip(text, pivot=pivot, seed=int(ctx.rng.integers(1 << 30))),
                        {"tier": 1, "llm": True, "pivot": pivot})


# --------------------------------------------------------------------------- #
# sweep definition
# --------------------------------------------------------------------------- #

@dataclass
class AttackSpec:
    name: str
    fn: AttackFn
    params: dict = field(default_factory=dict)
    requires_llm: bool = False

    def run(self, text: str, ctx: AttackContext) -> AttackResult:
        return self.fn(text, ctx, **self.params)


def default_sweep(quick: bool = False, include_llm: bool = False) -> list[AttackSpec]:
    sweep = [
        AttackSpec("identity (copy-paste)", identity),
        AttackSpec("word_autoformat", word_autoformat),
        AttackSpec("zero_width p=0.5", zero_width, {"p": 0.5}),
        AttackSpec("homoglyph p=0.3", homoglyph, {"p": 0.3}),
        AttackSpec("rewrite p=0.10", rewrite, {"p": 0.10}),
        AttackSpec("rewrite p=0.25", rewrite, {"p": 0.25}),
        AttackSpec("rewrite p=0.50", rewrite, {"p": 0.50}),
        AttackSpec("delete p=0.10", delete, {"p": 0.10}),
        AttackSpec("insert p=0.10", insert, {"p": 0.10}),
        AttackSpec("truncate 150 tok", truncate, {"n_tokens": 150}),
        AttackSpec("truncate 80 tok", truncate, {"n_tokens": 80}),
        AttackSpec("dilute 3x human", dilute, {"ratio": 3.0}),
        AttackSpec("piggyback k=3 (spoof)", piggyback, {"k": 3}),
    ]
    if quick:
        keep = {"identity (copy-paste)", "homoglyph p=0.3", "rewrite p=0.25", "dilute 3x human", "piggyback k=3 (spoof)"}
        sweep = [s for s in sweep if s.name in keep]
    if include_llm:
        sweep += [
            AttackSpec("paraphrase_llm", paraphrase_llm, requires_llm=True),
            AttackSpec("translate_roundtrip fr", translate_roundtrip, {"pivot": "French"}, requires_llm=True),
        ]
    return sweep
