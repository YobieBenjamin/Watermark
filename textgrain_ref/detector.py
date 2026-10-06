"""Watermark detection (textGrain Sec. 3.2) with hardening.

Per scored position t (first occurrence of each distinct context window, with a
complete window of `context_window` preceding tokens):

    seeds       = PRF(key, window)
    b           = block of observed token under the keyed partition
    J           = keyed column
    u           = uniform behind the Gumbel cost c(b, J)
    Y_t         = -log(1 - u)            ~ Exp(1) under the null

    S_n = sum_t Y_t  ~ Gamma(n, 1) under the null;  reject when S_n > quantile(1 - alpha).

Hardening on top of the paper's detector:
  * canonicalisation before tokenisation (kills homoglyph / zero-width / typography desync)
  * sliding-window localisation with Bonferroni correction, so a watermarked span
    hidden inside a long human document is found even when the whole-document test
    is diluted below threshold.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

import numpy as np
from scipy.stats import gamma

from .canonicalize import canonicalize
from .prf import KeyedPRF, detection_score_from_uniform
from .watermark import TextGrainConfig


@dataclass
class DetectionResult:
    n_tokens: int
    n_scored: int
    statistic: float
    p_value: float
    z_score: float
    threshold: float
    detected: bool
    alpha: float

    @property
    def neg_log10_p(self) -> float:
        return float(-np.log10(max(self.p_value, 1e-300)))

    def as_dict(self) -> dict:
        return {
            "n_tokens": self.n_tokens, "n_scored": self.n_scored, "statistic": self.statistic,
            "p_value": self.p_value, "z_score": self.z_score, "threshold": self.threshold,
            "detected": self.detected, "alpha": self.alpha,
        }


@dataclass
class Segment:
    token_start: int
    token_end: int          # exclusive
    n_scored: int
    statistic: float
    p_value: float          # Bonferroni-corrected
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    text: str = ""


@dataclass
class LocalizationResult:
    segments: list[Segment] = field(default_factory=list)
    n_windows: int = 0
    window: int = 0
    stride: int = 0


class Detector:
    def __init__(
        self,
        key: bytes,
        encode: Callable[[str], list[int]],
        config: TextGrainConfig,
        canonicalize_input: bool = True,
        alpha: float = 0.01,
        token_spans: Optional[Callable[[str], list[tuple[int, int]]]] = None,
    ):
        self.prf = KeyedPRF(key)
        self.encode = encode
        self.cfg = config
        self.canon = canonicalize_input
        self.alpha = alpha
        self.token_spans = token_spans

    # -- per-position scores ---------------------------------------------------
    def prepare(self, text: str) -> str:
        return canonicalize(text) if self.canon else text

    def position_scores(self, ids: Sequence[int]) -> np.ndarray:
        """Y_t for every position; NaN where the position is not scored."""
        ids = [int(t) for t in ids]
        L, B, m = self.cfg.context_window, self.cfg.n_blocks, self.cfg.n_columns
        y = np.full(len(ids), np.nan)
        seen: set = set()
        for t in range(L, len(ids)):
            ctx = tuple(ids[t - L:t]) if L > 0 else ()
            if L > 0 and ctx in seen:
                continue
            seen.add(ctx)
            seeds = self.prf.seeds(ctx)
            b = self.prf.block_of(seeds.partition, ids[t], B)
            col = self.prf.column(seeds.column, m)
            u = self.prf.cost_uniform(seeds.cost, b, col, m)
            y[t] = detection_score_from_uniform(u)
        return y

    @staticmethod
    def test(statistic: float, n_scored: int, alpha: float) -> tuple[float, float, float]:
        if n_scored <= 0:
            return 1.0, 0.0, float("inf")
        p = float(gamma.sf(statistic, a=n_scored, scale=1.0))
        z = float((statistic - n_scored) / np.sqrt(n_scored))
        thr = float(gamma.ppf(1.0 - alpha, a=n_scored, scale=1.0))
        return p, z, thr

    # -- whole-passage test ----------------------------------------------------
    def detect_ids(self, ids: Sequence[int]) -> DetectionResult:
        y = self.position_scores(ids)
        scored = y[~np.isnan(y)]
        n = int(scored.size)
        s = float(scored.sum()) if n else 0.0
        p, z, thr = self.test(s, n, self.alpha)
        return DetectionResult(len(ids), n, s, p, z, thr, bool(n > 0 and s > thr), self.alpha)

    def detect(self, text: str) -> DetectionResult:
        return self.detect_ids(self.encode(self.prepare(text)))

    # -- localisation ----------------------------------------------------------
    def localize(self, text: str, window: int = 120, stride: int = 30, alpha: Optional[float] = None) -> LocalizationResult:
        alpha = self.alpha if alpha is None else alpha
        prepared = self.prepare(text)
        ids = self.encode(prepared)
        y = self.position_scores(ids)
        n_tok = len(ids)
        if n_tok == 0:
            return LocalizationResult([], 0, window, stride)
        starts = list(range(0, max(n_tok - window, 0) + 1, stride))
        if starts[-1] + window < n_tok:
            starts.append(n_tok - window if n_tok > window else 0)
        n_windows = len(starts)
        hits = []
        for st in starts:
            seg = y[st:st + window]
            scored = seg[~np.isnan(seg)]
            n = int(scored.size)
            if n < max(10, window // 10):
                continue
            s = float(scored.sum())
            p, _, _ = self.test(s, n, alpha)
            p_corr = min(1.0, p * n_windows)
            if p_corr < alpha:
                hits.append((st, min(st + window, n_tok), n, s, p_corr))
        # merge overlapping / adjacent significant windows
        merged: list[list] = []
        for h in hits:
            if merged and h[0] <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], h[1])
                merged[-1][4] = min(merged[-1][4], h[4])
            else:
                merged.append(list(h))
        spans = self.token_spans(prepared) if self.token_spans else None
        segments = []
        for st, en, n, s, p in merged:
            seg_y = y[st:en]
            scored = seg_y[~np.isnan(seg_y)]
            stat = float(scored.sum())
            p_seg, _, _ = self.test(stat, int(scored.size), alpha)
            cs = ce = None
            snippet = ""
            if spans and st < len(spans) and en - 1 < len(spans):
                cs, ce = spans[st][0], spans[en - 1][1]
                snippet = prepared[cs:ce]
            segments.append(Segment(st, en, int(scored.size), stat, min(1.0, p_seg * n_windows), cs, ce, snippet))
        return LocalizationResult(segments, n_windows, window, stride)


def empirical_threshold(null_scores: Sequence[float], alpha: float) -> float:
    """Threshold on -log10(p) that yields an empirical false-positive rate <= alpha."""
    s = np.sort(np.asarray(null_scores, dtype=np.float64))
    if s.size == 0:
        return float("inf")
    k = int(np.ceil((1.0 - alpha) * s.size))
    k = min(max(k, 0), s.size - 1)
    return float(s[k])
