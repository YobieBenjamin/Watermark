"""Signals.  The gate's output vocabulary and its advisory-input interface.

Two directions:

  * OUT  -- every check the gate runs emits named signals.  They are the structured,
            machine-readable record of *why* an action was allowed, held or denied,
            written to `signals.jsonl` for whatever consumes them (a safety team's
            signal store, a SIEM, a dashboard).
  * IN   -- `SignalSource` is the plug-in point for sensors the gate does not own:
            an activation probe, a chain-of-thought monitor, an anomaly score over
            the session's action sequence.  The gate never interprets them; policy
            maps a named signal and a threshold to HOLD or DENY.  The sensor may be
            a model.  The decision is not.

Severity is the strongest action a signal can imply on its own.  Policy decides.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

SEVERITIES = ("info", "flag", "hold", "deny")


@dataclass
class Signal:
    name: str
    severity: str = "info"      # info | flag | hold | deny
    value: float = 1.0
    detail: str = ""
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"name": self.name, "severity": self.severity, "value": self.value, "detail": self.detail, **({"meta": self.meta} if self.meta else {})}


class SignalSource(Protocol):
    """An external sensor.  Called once per tool call with the gate's view of the session."""

    name: str

    def observe(self, session_view: dict, call_body: dict) -> list[Signal]: ...


# name -> (layer, meaning).  Stable identifiers; add, never rename.
SIGNAL_CATALOG: dict[str, tuple[str, str]] = {
    # chain integrity (stateless, chain.py)
    "chain.missing":              ("chain", "no provenance chain / no call link"),
    "chain.malformed":            ("chain", "links out of order or a required field is absent"),
    "chain.root_untrusted":       ("chain", "deployment link signed by a key that is not a trusted root (a rogue deployment, or an exfiltrated model running elsewhere)"),
    "chain.sig_invalid":          ("chain", "a link's signature does not verify under the key the chain names for it (forgery, or edit after signing)"),
    "chain.broken":               ("chain", "a link does not hash-link to its predecessor (splice)"),
    "chain.id_mismatch":          ("chain", "a link names a different deployment/session than its predecessor"),
    "chain.stale":                ("chain", "session or call expired, or issued in the future"),
    "approval.invalid":           ("chain", "an approval link is present but is not the principal's, or binds a different call"),
    # attestation (what build and what policy the chain claims)
    "attest.policy_mismatch":     ("attest", "deployment attests a policy hash other than the one the gate loaded"),
    "attest.measurement_mismatch": ("attest", "deployment attests a gate measurement other than the running build"),
    # stateful chain checks (gate.py)
    "chain.replay":               ("chain", "call id already seen (replay)"),
    "chain.out_of_order":         ("chain", "sequence number not greater than the last accepted one for this session"),
    # policy
    "policy.tool_denied":         ("policy", "tool not permitted for this principal's role"),
    "policy.tool_unknown":        ("policy", "tool not in the policy at all (fail closed)"),
    "policy.arg_violation":       ("policy", "an argument is outside its allowed bounds (amount cap, host or domain allowlist, path prefix)"),
    "policy.rate_exceeded":       ("policy", "per-session rate limit or irreversible-action budget exhausted"),
    # what crossed INTO the model (provenance of inputs, labelled by the first five layers)
    "input.verified":             ("input", "input carries a valid signature from the session's principal"),
    "input.unverified":           ("input", "no watermark, no registry match: human text or an unwatermarked model"),
    "input.self_generated":       ("input", "registry EXACT: the model is acting on this deployment's own earlier output (loop / AI-to-AI)"),
    "input.tampered":             ("input", "registry TAMPERED: an edited copy of a genuine output (the piggyback spoof, applied to instructions)"),
    "input.watermark_unregistered": ("input", "watermark detected under this deployment's key but nothing registered: stolen key, spoof, or registry gap"),
    "input.watermark_missed":     ("input", "watermark test failed but retrieval found the original: the rewrite that defeats the first layer"),
    "session.taint":              ("input", "highest provenance level that has entered this session so far"),
    "agent.claim_mismatch":       ("agent", "the agent's declared provenance for an input disagrees with what the gate measured"),
    "agent.claim_omitted":        ("agent", "the agent declared no derivation for an action taken after untrusted inputs entered the session"),
    # advisory (external sensors, e.g. the safety project's signals)
    "advisory.hold":              ("advisory", "an external signal crossed its policy HOLD threshold"),
    "advisory.deny":              ("advisory", "an external signal crossed its policy DENY threshold"),
    # decision
    "decision.allow":             ("decision", "executed"),
    "decision.hold":              ("decision", "not executed; needs a principal approval link to proceed"),
    "decision.deny":              ("decision", "not executed; not approvable"),
    "decision.approved":          ("decision", "a held call was executed under a valid principal approval"),
}

LEVEL_ORDER = ["info", "flag", "hold", "deny"]


def strongest(signals: list[Signal]) -> str:
    best = "info"
    for s in signals:
        if LEVEL_ORDER.index(s.severity) > LEVEL_ORDER.index(best):
            best = s.severity
    return best
