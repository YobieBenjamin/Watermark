"""Keyed pseudorandom functions for textGrain-style watermarking.

Everything the watermark needs at a position is a deterministic function of
(secret key, context window):

  * a keyed partition g : V -> [B]  (which block each vocabulary token belongs to)
  * a keyed cost table  c(b, j) = -G_bj  (standardized Gumbel values, B x m)
  * a keyed column      J in [m]

The seeds are derived with HMAC-SHA256(key, context).  The per-element values are
expanded with splitmix64, which is *random-access*: the block of a single token, or
the uniform for a single (block, column) pair, can be recomputed without generating
the whole table.  That is what makes detection cheap.

NOTE: splitmix64 is a mixer, not a CSPRNG.  Because its input is a secret HMAC
output this is adequate for a reference implementation; a production deployment
should replace `splitmix64` with a keyed block cipher in counter mode (e.g. AES-CTR
or ChaCha20) so that the expansion is itself a PRF.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Sequence

import numpy as np

EULER_GAMMA = 0.5772156649015329
GUMBEL_SD = np.pi / np.sqrt(6.0)

_GOLDEN = np.uint64(0x9E3779B97F4A7C15)
_C1 = np.uint64(0xBF58476D1CE4E5B9)
_C2 = np.uint64(0x94D049BB133111EB)


def splitmix64(x: np.ndarray) -> np.ndarray:
    """Vectorised splitmix64 finaliser on uint64 arrays (wrap-around arithmetic)."""
    x = np.asarray(x, dtype=np.uint64)
    with np.errstate(over="ignore"):
        z = x + _GOLDEN
        z = (z ^ (z >> np.uint64(30))) * _C1
        z = (z ^ (z >> np.uint64(27))) * _C2
        return z ^ (z >> np.uint64(31))


def _to_uniform(z: np.ndarray) -> np.ndarray:
    """Map uint64 words to the open interval (0, 1) with 53-bit resolution."""
    return ((z >> np.uint64(11)).astype(np.float64) + 0.5) * (2.0 ** -53)


def _mix(seed: int, idx: np.ndarray) -> np.ndarray:
    return splitmix64(np.asarray(idx, dtype=np.uint64) ^ np.uint64(seed))


@dataclass(frozen=True)
class Seeds:
    partition: int
    cost: int
    column: int


class KeyedPRF:
    """HMAC-seeded, random-access pseudorandom values keyed by (secret, context)."""

    def __init__(self, key: bytes):
        if not isinstance(key, (bytes, bytearray)) or len(key) < 16:
            raise ValueError("key must be at least 16 bytes")
        self.key = bytes(key)

    # -- seeds -----------------------------------------------------------------
    def seeds(self, context: Sequence[int]) -> Seeds:
        ctx = np.asarray(list(context), dtype=np.uint32)
        msg = len(ctx).to_bytes(4, "little") + ctx.tobytes()
        d = hmac.new(self.key, msg, hashlib.sha256).digest()
        return Seeds(
            partition=int.from_bytes(d[0:8], "little"),
            cost=int.from_bytes(d[8:16], "little"),
            column=int.from_bytes(d[16:24], "little"),
        )

    # -- partition g : V -> [B] ---------------------------------------------
    @staticmethod
    def partition(seed: int, vocab_size: int, n_blocks: int) -> np.ndarray:
        ids = np.arange(vocab_size, dtype=np.uint64)
        return (_mix(seed, ids) % np.uint64(n_blocks)).astype(np.int64)

    @staticmethod
    def block_of(seed: int, token: int, n_blocks: int) -> int:
        return int(_mix(seed, np.array([token], dtype=np.uint64))[0] % np.uint64(n_blocks))

    # -- cost table ------------------------------------------------------------
    @staticmethod
    def cost_uniforms(seed: int, n_blocks: int, n_columns: int) -> np.ndarray:
        """Uniform(0,1) variates underlying the Gumbel cost table, shape (B, m)."""
        idx = np.arange(n_blocks * n_columns, dtype=np.uint64)
        return _to_uniform(_mix(seed, idx)).reshape(n_blocks, n_columns)

    @staticmethod
    def cost_uniform(seed: int, block: int, column: int, n_columns: int) -> float:
        idx = np.array([block * n_columns + column], dtype=np.uint64)
        return float(_to_uniform(_mix(seed, idx))[0])

    @staticmethod
    def uniforms_to_costs(u: np.ndarray) -> np.ndarray:
        """c = -G where G is the standardized Gumbel: G = (Z - gamma) / (pi/sqrt(6))."""
        z = -np.log(-np.log(u))  # standard Gumbel, F_G(z) = u
        g = (z - EULER_GAMMA) / GUMBEL_SD
        return -g

    def cost_table(self, seed: int, n_blocks: int, n_columns: int) -> np.ndarray:
        return self.uniforms_to_costs(self.cost_uniforms(seed, n_blocks, n_columns))

    # -- column ----------------------------------------------------------------
    @staticmethod
    def column(seed: int, n_columns: int) -> int:
        return int(_mix(seed, np.array([0], dtype=np.uint64))[0] % np.uint64(n_columns))


def detection_score_from_uniform(u: float) -> float:
    """Y = -log(1 - F_G(Z)) = -log(1 - u): unit exponential under the null."""
    return float(-np.log1p(-u))
