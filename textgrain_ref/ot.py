"""Entropy-calibrated block optimal transport (textGrain Sec. 3.1 / Appendix A).

We solve

    min_{pi in Pi(rho, u_m)}  <pi, c>  +  lambda * KL(pi || rho (x) u_m)

with Sinkhorn iterations in the log domain, and adjust lambda so that the KL
divergence -- which equals the average NTP entropy removed by the watermark -- hits
a target fraction `beta` of H(P).  Marginal correction (Altschuler et al. 2017)
restores exact marginals before sampling.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def logsumexp(a: np.ndarray, axis: int) -> np.ndarray:
    """Minimal stable log-sum-exp (scipy's has ~0.3 ms of dispatch overhead per call)."""
    m = np.max(a, axis=axis, keepdims=True)
    out = m + np.log(np.sum(np.exp(a - m), axis=axis, keepdims=True))
    return np.squeeze(out, axis=axis)


def entropy(p: np.ndarray) -> float:
    p = p[p > 0]
    return float(-(p * np.log(p)).sum())


def kl_from_independence(pi: np.ndarray, rho: np.ndarray, n_columns: int) -> float:
    """KL(pi || rho (x) u_m) = sum pi log(pi / (rho_b / m))."""
    ref = np.broadcast_to(rho[:, None] / n_columns, pi.shape)
    mask = pi > 0
    return float((pi[mask] * np.log(pi[mask] / ref[mask])).sum())


def marginal_correction(x: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Round a non-negative table onto the transport polytope with marginals (a, b)."""
    r = x.sum(axis=1)
    xr = np.where(r > 0, np.minimum(a / np.where(r > 0, r, 1.0), 1.0), 1.0)
    x1 = x * xr[:, None]
    c = x1.sum(axis=0)
    yc = np.where(c > 0, np.minimum(b / np.where(c > 0, c, 1.0), 1.0), 1.0)
    x2 = x1 * yc[None, :]
    ea = a - x2.sum(axis=1)
    eb = b - x2.sum(axis=0)
    delta = ea.sum()
    if delta > 1e-15:
        x2 = x2 + np.outer(ea, eb) / delta
    return np.maximum(x2, 0.0)   # clip floating-point dust


@dataclass
class OTResult:
    coupling: np.ndarray        # (B, m), rows sum to rho, columns sum to 1/m
    beta_requested: float       # after caps
    beta_achieved: float
    converged: bool
    status: str
    iterations: int
    lam: float


def solve_block_ot(
    rho: np.ndarray,
    cost: np.ndarray,
    entropy_p: float,
    beta: float,
    n_columns: int,
    n_iter: int = 40,
    tol: float = 0.02,
    lam_min: float = 1e-2,
    lam_max: float = 1e2,
    step: float = 0.5,
) -> OTResult:
    """Algorithm A.1: alternating Sinkhorn updates and regularisation updates."""
    rho = np.asarray(rho, dtype=np.float64)
    n_blocks = rho.shape[0]
    um = np.full(n_columns, 1.0 / n_columns)
    keep = rho > 0
    rho_k = rho[keep]
    c = np.asarray(cost, dtype=np.float64)[keep]
    h_rho = entropy(rho_k)

    def independent(status: str) -> OTResult:
        return OTResult(np.outer(rho, um), 0.0, 0.0, True, status, 0, float("nan"))

    if beta <= 0 or h_rho <= 0 or entropy_p <= 0:
        return independent("independent coupling")

    # caps from the information bound (Theorem A.4)
    beta = min(beta, (1.0 - 1e-8) * h_rho / entropy_p, np.log(n_columns) / entropy_p)
    if beta <= 0:
        return independent("independent coupling (capped)")

    lam = 1.0
    logv = np.zeros(n_columns)
    delta = 1e-12 * h_rho / entropy_p
    gain = -c  # maximise <pi, G> - lam * KL, with G = -c
    log_rho = np.log(rho_k)
    log_um = np.log(um)

    pi = None
    beta_hat = 0.0
    its = 0
    for s in range(n_iter):
        its = s + 1
        log_k = gain / lam
        logu = log_rho - logsumexp(log_k + logv[None, :], axis=1)
        logv = log_um - logsumexp(log_k + logu[:, None], axis=0)
        logv = logv - logv.mean()
        logu = log_rho - logsumexp(log_k + logv[None, :], axis=1)
        logpi = logu[:, None] + log_k + logv[None, :]
        logpi = logpi - logsumexp(logpi, axis=0)[None, :] + np.log(1.0 / n_columns)
        pi = marginal_correction(np.exp(logpi), rho_k, um)
        beta_hat = kl_from_independence(pi, rho_k, n_columns) / entropy_p
        if abs(beta_hat - beta) <= tol:
            break
        lam_new = float(np.clip(lam * (max(beta_hat, delta) / beta) ** step, lam_min, lam_max))
        logv = (lam / lam_new) * logv
        lam = lam_new

    full = np.zeros((n_blocks, n_columns))
    full[keep] = pi
    converged = abs(beta_hat - beta) <= tol
    return OTResult(full, beta, beta_hat, converged, "ok" if converged else "iteration limit", its, lam)
