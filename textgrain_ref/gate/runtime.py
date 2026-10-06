"""The three non-model parties that sign things, and the thin runtime between model and gate.

    Provisioner  holds the ROOT key.  Signs one deployment link per (model, policy, gate build).
    Deployment   holds the DEPLOYMENT key.  Opens sessions for principals.
    Runtime      holds the SESSION key.  Takes the model's tool request (plain data), wraps it
                 in a call link, and sends the envelope to the gate.  It is a few dozen lines
                 of code with no model in it.  # silicon: its key is the one most worth
                 keeping in a TPM -- the model process cannot exfiltrate what it cannot read,
                 and a monotonic counter makes every signature ever issued countable.
    Principal    the accountable human/account.  Holds their own key; signs content they
                 vouch for and approvals for held calls.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable, Optional

from .chain import Envelope, Link, make_approval_link, make_call_link, make_deployment_link, make_session_link
from .crypto import Signer, SoftwareSigner


@dataclass
class Principal:
    id: str
    role: str
    signer: Signer

    def as_body(self) -> dict:
        return {"id": self.id, "role": self.role, "pub": self.signer.public_hex()}


def provision(root: Signer, deployment: Signer, deployment_id: str, model_id: str, policy_hash: str,
              measurement: str, watermark_key_id: str, registry_key_id: str, now: float) -> Link:
    return make_deployment_link(root, deployment_id, model_id, deployment.public_hex(), policy_hash, measurement,
                                watermark_key_id, registry_key_id, now)


def open_session(deployment: Signer, deployment_link: Link, principal: Principal, now: float,
                 ttl: float = 3600.0, session_signer: Optional[Signer] = None) -> tuple[Signer, Link]:
    signer = session_signer or SoftwareSigner(label="session")
    link = make_session_link(deployment, deployment_link, f"s-{uuid.uuid4().hex[:12]}", principal.as_body(),
                             signer.public_hex(), now, now + ttl)
    return signer, link


class Runtime:
    """Wraps model tool requests into signed envelopes.  No model code runs here."""

    def __init__(self, deployment_link: Link, session_link: Link, session_signer: Signer,
                 clock: Callable[[], float], ttl: float = 60.0):
        self.deployment_link = deployment_link
        self.session_link = session_link
        self.signer = session_signer
        self.clock = clock
        self.ttl = ttl
        self.seq = 0

    @property
    def session_id(self) -> str:
        return str(self.session_link.body["session_id"])

    def envelope(self, tool: str, args: dict, derived_from: Optional[list[str]] = None,
                 declared_provenance: Optional[dict] = None, call_id: Optional[str] = None) -> Envelope:
        self.seq += 1
        call = make_call_link(self.signer, self.session_link, call_id or f"c-{uuid.uuid4().hex[:12]}", self.seq,
                              tool, args, float(self.clock()), self.ttl, derived_from, declared_provenance)
        return Envelope([self.deployment_link, self.session_link, call])


def approve(principal: Principal, env: Envelope, now: float, note: str = "") -> Envelope:
    """The human in the loop: one signature over one call hash."""
    assert env.call is not None
    return env.with_approval(make_approval_link(principal.signer, env.call, now, note))
