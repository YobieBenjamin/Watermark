"""Execution gate: provenance-chained tool calls, verified and policed *outside* the model.

Layer 6 of the stack.  The first five layers label text (did a keyed sampler produce
it, is it exactly what we signed, which original does it resemble).  This layer
applies those labels at the moment they matter -- before an agent's action executes --
and binds every action to an accountable chain:

    root key ──signs──► deployment link ──signs──► session link ──signs──► call link
    (HSM)               (policy hash,               (principal,             (tool, args,
                         gate measurement)           session key)            seq, nonce)

The gate is a deterministic verifier.  It holds the keys, the policy, the replay
cache, the provenance oracle (detector + registry + retrieval) and the decision log,
and it is the only thing that can execute a tool.  The model only produces text.

    chain valid?  →  policy allows?  →  session taint allows?  →  advisory signals?  →  ALLOW / HOLD / DENY
    (signatures,     (allowlist,        (what crossed INTO       (the safety project's
     continuity,      arg bounds,        the model: watermark,    sensors, applied as
     freshness,       rate limits)       registry, retrieval)     thresholds, never as
     attestation)                                                  the judge)

Nothing in this package calls a language model to decide anything.
"""
from .chain import Envelope, Link, make_call_link, make_deployment_link, make_session_link  # noqa: F401
from .crypto import SoftwareSigner, canonical_json, measure, sha256_hex  # noqa: F401
from .gate import Decision, ExecutionGate  # noqa: F401
from .policy import Policy, default_policy  # noqa: F401
from .provenance import ProvenanceLabel, ProvenanceOracle  # noqa: F401
from .signals import Signal, SIGNAL_CATALOG  # noqa: F401

__all__ = [
    "Envelope", "Link", "make_call_link", "make_deployment_link", "make_session_link",
    "SoftwareSigner", "canonical_json", "measure", "sha256_hex",
    "Decision", "ExecutionGate", "Policy", "default_policy",
    "ProvenanceLabel", "ProvenanceOracle", "Signal", "SIGNAL_CATALOG",
]
