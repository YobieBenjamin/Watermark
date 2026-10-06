"""textGrain-style watermark embedding (one generation step) and a generic decode loop.

At each position:
  1. keyed partition of the vocabulary into B blocks          (key, context)
  2. block probabilities rho_b = sum of P over the block
  3. keyed Gumbel cost table c(b, j), B x m                   (key, context)
  4. entropy-budgeted OT coupling pi with marginals (rho, u_m)
  5. keyed column J                                            (key, context)
  6. Q(w | J) = m * pi(g(w), J) * P_w / rho_{g(w)}  -> sample the token from Q

Averaging Q over J recovers P exactly (unbiased); the KL of the coupling from
independence is the average sampling entropy removed (the "entropy budget").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from .ot import entropy, solve_block_ot
from .prf import KeyedPRF


@dataclass
class TextGrainConfig:
    n_blocks: int = 32
    n_columns: int = 16
    beta: float = 0.5            # fraction of NTP entropy the watermark may remove (on average)
    context_window: int = 3      # L_ctx: preceding tokens hashed with the key
    mask_repeated_contexts: bool = True
    ot_iterations: int = 40
    ot_tolerance: float = 0.02

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass
class StepInfo:
    masked: bool = False
    beta_requested: float = 0.0
    beta_achieved: float = 0.0
    entropy_p: float = 0.0
    converged: bool = True
    column: int = -1
    extra: dict = field(default_factory=dict)


class TextGrainSampler:
    """Turns an NTP distribution P into the watermarked conditional Q(. | key, context)."""

    def __init__(self, key: bytes, config: TextGrainConfig, vocab_size: int):
        self.prf = KeyedPRF(key)
        self.cfg = config
        self.vocab_size = int(vocab_size)
        self.seen: set = set()
        self.history: list[StepInfo] = []

    def reset(self) -> None:
        self.seen = set()
        self.history = []

    def distribution(self, p: np.ndarray, context: Sequence[int]) -> tuple[np.ndarray, StepInfo]:
        p = np.asarray(p, dtype=np.float64)
        if p.shape[0] != self.vocab_size:
            raise ValueError(f"distribution has {p.shape[0]} entries, vocab_size is {self.vocab_size}")
        ctx = tuple(int(t) for t in context)
        info = StepInfo(entropy_p=entropy(p))
        if len(ctx) == 0:
            info.masked = True
            self.history.append(info)
            return p, info
        if self.cfg.mask_repeated_contexts and ctx in self.seen:
            info.masked = True
            self.history.append(info)
            return p, info
        self.seen.add(ctx)

        b, m = self.cfg.n_blocks, self.cfg.n_columns
        seeds = self.prf.seeds(ctx)
        g = self.prf.partition(seeds.partition, self.vocab_size, b)
        rho = np.bincount(g, weights=p, minlength=b)
        cost = self.prf.cost_table(seeds.cost, b, m)
        res = solve_block_ot(rho, cost, info.entropy_p, self.cfg.beta, m,
                             n_iter=self.cfg.ot_iterations, tol=self.cfg.ot_tolerance)
        col = self.prf.column(seeds.column, m)
        r = m * res.coupling[:, col]                    # conditional block probabilities
        with np.errstate(divide="ignore", invalid="ignore"):
            scale = np.where(rho > 0, r / np.where(rho > 0, rho, 1.0), 0.0)
        q = np.maximum(p * scale[g], 0.0)
        total = q.sum()
        if not np.isfinite(total) or total <= 0:        # numerical safety net: fall back to P
            q = p
        else:
            q = q / total
        info.beta_requested = res.beta_requested
        info.beta_achieved = res.beta_achieved
        info.converged = res.converged
        info.column = col
        self.history.append(info)
        return q, info

    def sample(self, p: np.ndarray, context: Sequence[int], rng: np.random.Generator) -> int:
        q, _ = self.distribution(p, context)
        return int(rng.choice(self.vocab_size, p=q))


def apply_temperature_top_p(p: np.ndarray, temperature: float = 1.0, top_p: float = 1.0) -> np.ndarray:
    """P after temperature scaling and nucleus truncation (the distribution the watermark sees)."""
    p = np.asarray(p, dtype=np.float64)
    if temperature != 1.0:
        if temperature <= 0:
            out = np.zeros_like(p)
            out[int(np.argmax(p))] = 1.0
            return out
        logp = np.log(np.clip(p, 1e-300, None)) / temperature
        logp -= logp.max()
        p = np.exp(logp)
        p /= p.sum()
    if top_p < 1.0:
        order = np.argsort(-p)
        cum = np.cumsum(p[order])
        k = int(np.searchsorted(cum, top_p) + 1)
        keep = order[:k]
        q = np.zeros_like(p)
        q[keep] = p[keep]
        p = q / q.sum()
    return p


def generate(
    lm,
    prompt_ids: Sequence[int],
    max_new_tokens: int,
    sampler: Optional[TextGrainSampler],
    rng: np.random.Generator,
    temperature: float = 1.0,
    top_p: float = 1.0,
    context_window: int = 3,
) -> list[int]:
    """Backend-agnostic autoregressive decoding.  `lm` must expose
    `next_token_dist(ids) -> np.ndarray` and optionally `eos_id`."""
    ids = list(int(t) for t in prompt_ids)
    n_prompt = len(ids)
    eos = getattr(lm, "eos_id", None)
    if sampler is not None:
        sampler.reset()
    for _ in range(max_new_tokens):
        p = apply_temperature_top_p(lm.next_token_dist(ids), temperature, top_p)
        ctx = ids[-context_window:] if context_window > 0 else []
        q = sampler.distribution(p, ctx)[0] if sampler is not None else p
        tok = int(rng.choice(len(q), p=q))
        if eos is not None and tok == eos:
            break
        ids.append(tok)
    return ids[n_prompt:]
