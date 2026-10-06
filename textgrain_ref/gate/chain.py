"""The provenance chain carried by every tool call.

    deployment link   signed by a trusted ROOT key (provisioning authority, HSM).
                      Pins: deployment key, policy hash, gate measurement, the
                      watermark/registry key ids this deployment emits under.
    session link      signed by the DEPLOYMENT key.  Pins: session key, principal
                      (the accountable human/account, with their public key), expiry.
    call link         signed by the SESSION key.  Pins: tool, canonical args, sequence
                      number, nonce, expiry, and what the agent *claims* it derived
                      the call from (the gate verifies that claim independently).
    approval link     optional, signed by the PRINCIPAL's key.  Turns a HOLD into an
                      ALLOW for exactly one call hash.  This is the human-in-the-loop,
                      in cryptographic form: one signature, on one call, after the fact.

Each link signs `domain | kind | prev_hash | canonical_json(body)`; its hash covers the
signature too, so the chain is append-only and any edit anywhere breaks every hash
after it.  Verification here is stateless.  Replay, sequencing, rate limits and taint
are stateful and live in `gate.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .crypto import Signer, canonical_json, key_id_of, sha256_hex, verify
from .signals import Signal

DOMAIN = b"textgrain-gate-v1"
KIND_DEPLOYMENT, KIND_SESSION, KIND_CALL, KIND_APPROVAL = "deployment", "session", "call", "approval"
ORDER = (KIND_DEPLOYMENT, KIND_SESSION, KIND_CALL, KIND_APPROVAL)


@dataclass
class Link:
    kind: str
    body: dict
    prev: str           # hex hash of the previous link; "" for the root
    key_id: str         # id of the public key that signed this link
    sig: str            # hex Ed25519 signature

    def signing_bytes(self) -> bytes:
        return DOMAIN + b"|" + self.kind.encode() + b"|" + self.prev.encode() + b"|" + canonical_json(self.body)

    def hash(self) -> str:
        return sha256_hex(self.signing_bytes() + bytes.fromhex(self.sig))

    def as_dict(self) -> dict:
        return {"kind": self.kind, "body": self.body, "prev": self.prev, "key_id": self.key_id, "sig": self.sig}

    @classmethod
    def from_dict(cls, d: dict) -> "Link":
        return cls(str(d["kind"]), dict(d["body"]), str(d["prev"]), str(d["key_id"]), str(d["sig"]))


def sign_link(kind: str, body: dict, prev: str, signer: Signer) -> Link:
    unsigned = Link(kind, body, prev, signer.key_id, "")
    return Link(kind, body, prev, signer.key_id, signer.sign(unsigned.signing_bytes()).hex())


@dataclass
class Envelope:
    links: list[Link] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"links": [l.as_dict() for l in self.links]}

    @classmethod
    def from_dict(cls, d: dict) -> "Envelope":
        return cls([Link.from_dict(x) for x in d.get("links", [])])

    def _kind(self, kind: str) -> Optional[Link]:
        for l in self.links:
            if l.kind == kind:
                return l
        return None

    @property
    def deployment(self) -> Optional[Link]:
        return self._kind(KIND_DEPLOYMENT)

    @property
    def session(self) -> Optional[Link]:
        return self._kind(KIND_SESSION)

    @property
    def call(self) -> Optional[Link]:
        return self._kind(KIND_CALL)

    @property
    def approval(self) -> Optional[Link]:
        return self._kind(KIND_APPROVAL)

    def with_approval(self, approval: Link) -> "Envelope":
        return Envelope([l for l in self.links if l.kind != KIND_APPROVAL] + [approval])


# --------------------------------------------------------------------------- #
# builders (used by the provisioning authority, the deployment and the runtime)
# --------------------------------------------------------------------------- #

def make_deployment_link(root: Signer, deployment_id: str, model_id: str, deployment_pub_hex: str,
                         policy_hash: str, measurement: str, watermark_key_id: str, registry_key_id: str,
                         issued_at: float) -> Link:
    body = {
        "deployment_id": deployment_id, "model_id": model_id, "deployment_pub": deployment_pub_hex,
        "policy_hash": policy_hash, "measurement": measurement,
        "watermark_key_id": watermark_key_id, "registry_key_id": registry_key_id,
        "issued_at": float(issued_at),
    }
    return sign_link(KIND_DEPLOYMENT, body, "", root)


def make_session_link(deployment: Signer, deployment_link: Link, session_id: str, principal: dict,
                      session_pub_hex: str, issued_at: float, expires_at: float) -> Link:
    body = {
        "session_id": session_id, "deployment_id": deployment_link.body["deployment_id"],
        "principal": {"id": str(principal["id"]), "role": str(principal["role"]), "pub": str(principal["pub"])},
        "session_pub": session_pub_hex, "issued_at": float(issued_at), "expires_at": float(expires_at),
    }
    return sign_link(KIND_SESSION, body, deployment_link.hash(), deployment)


def make_call_link(session: Signer, session_link: Link, call_id: str, seq: int, tool: str, args: dict,
                   issued_at: float, ttl: float = 60.0, derived_from: Optional[list[str]] = None,
                   declared_provenance: Optional[dict] = None) -> Link:
    body = {
        "call_id": call_id, "session_id": session_link.body["session_id"], "seq": int(seq),
        "tool": tool, "args": args, "derived_from": list(derived_from or []),
        "declared_provenance": dict(declared_provenance or {}),
        "issued_at": float(issued_at), "expires_at": float(issued_at + ttl),
    }
    return sign_link(KIND_CALL, body, session_link.hash(), session)


def make_approval_link(principal: Signer, call_link: Link, approved_at: float, note: str = "") -> Link:
    body = {"call_id": call_link.body["call_id"], "call_hash": call_link.hash(),
            "approved_at": float(approved_at), "note": note}
    return sign_link(KIND_APPROVAL, body, call_link.hash(), principal)


# --------------------------------------------------------------------------- #
# stateless verification
# --------------------------------------------------------------------------- #

@dataclass
class ChainCheck:
    ok: bool
    signals: list[Signal] = field(default_factory=list)
    deployment: Optional[dict] = None
    session: Optional[dict] = None
    call: Optional[dict] = None
    approval: Optional[dict] = None
    call_hash: str = ""


def _signed_by(link: Link, pub_hex: str) -> bool:
    """Does the link name the key it should have been signed with?  (Signature checked separately.)"""
    return len(pub_hex) == 64 and link.key_id == key_id_of(pub_hex)


def verify_chain(env: Envelope, trusted_roots: dict[str, str], now: float, expected_policy_hash: str,
                 expected_measurement: str, max_skew: float = 30.0) -> ChainCheck:
    """`trusted_roots` maps key_id -> public key hex of the provisioning authorities."""
    sigs: list[Signal] = []
    fail = lambda name, detail="": sigs.append(Signal(name, "deny", 1.0, detail))  # noqa: E731

    kinds = [l.kind for l in env.links]
    missing = [k for k in ORDER[:3] if k not in kinds]
    if missing:
        fail("chain.missing", f"no {'/'.join(missing)} link")
        return ChainCheck(False, sigs)
    expected_prefix = list(ORDER[:3])
    if kinds[:3] != expected_prefix or (len(kinds) > 3 and (kinds[3] != KIND_APPROVAL or len(kinds) > 4)):
        fail("chain.malformed", f"link order {kinds}")
        return ChainCheck(False, sigs)
    dep, ses, call = env.links[0], env.links[1], env.links[2]
    appr = env.links[3] if len(env.links) == 4 else None

    # --- deployment: must be signed by a trusted root -----------------------------
    root_pub = trusted_roots.get(dep.key_id)
    if dep.prev != "":
        fail("chain.broken", "deployment link has a predecessor")
    if root_pub is None:
        fail("chain.root_untrusted", f"deployment signed by unknown key {dep.key_id}")
    elif not verify(root_pub, dep.signing_bytes(), bytes.fromhex(dep.sig)):
        fail("chain.sig_invalid", "deployment link")
    if str(dep.body.get("policy_hash")) != expected_policy_hash:
        fail("attest.policy_mismatch", "deployment attests a different policy than the gate loaded")
    if str(dep.body.get("measurement")) != expected_measurement:
        fail("attest.measurement_mismatch", "deployment attests a different gate build than is running")

    # --- session: signed by the deployment key, not expired -------------------------
    dep_pub = str(dep.body.get("deployment_pub", ""))
    if ses.prev != dep.hash():
        fail("chain.broken", "session link does not hash-link to the deployment link")
    if not _signed_by(ses, dep_pub):
        fail("chain.sig_invalid", "session link signed by a key other than the deployment key")
    elif not verify(dep_pub, ses.signing_bytes(), bytes.fromhex(ses.sig)):
        fail("chain.sig_invalid", "session link")
    if ses.body.get("deployment_id") != dep.body.get("deployment_id"):
        fail("chain.id_mismatch", "session names a different deployment")
    if float(ses.body.get("expires_at", 0)) < now:
        fail("chain.stale", "session expired")
    if float(ses.body.get("issued_at", 0)) > now + max_skew:
        fail("chain.stale", "session issued in the future")

    # --- call: signed by the session key, fresh ---------------------------------------
    ses_pub = str(ses.body.get("session_pub", ""))
    if call.prev != ses.hash():
        fail("chain.broken", "call link does not hash-link to the session link")
    if not _signed_by(call, ses_pub):
        fail("chain.sig_invalid", "call link signed by a key other than the session key")
    elif not verify(ses_pub, call.signing_bytes(), bytes.fromhex(call.sig)):
        fail("chain.sig_invalid", "call link")
    if call.body.get("session_id") != ses.body.get("session_id"):
        fail("chain.id_mismatch", "call names a different session")
    if float(call.body.get("expires_at", 0)) < now:
        fail("chain.stale", "call expired")
    if float(call.body.get("issued_at", 0)) > now + max_skew:
        fail("chain.stale", "call issued in the future")
    for k in ("call_id", "seq", "tool", "args"):
        if k not in call.body:
            fail("chain.malformed", f"call link lacks {k}")

    # --- approval: optional, by the principal named in the session --------------------
    approval_body = None
    if appr is not None:
        principal_pub = str((ses.body.get("principal") or {}).get("pub", ""))
        if appr.prev != call.hash() or appr.body.get("call_hash") != call.hash():
            fail("approval.invalid", "approval does not bind to this call")
        elif not _signed_by(appr, principal_pub):
            fail("approval.invalid", "approval not signed by the session's principal")
        elif not verify(principal_pub, appr.signing_bytes(), bytes.fromhex(appr.sig)):
            fail("approval.invalid", "approval signature invalid")
        else:
            approval_body = appr.body

    return ChainCheck(not sigs, sigs, dep.body, ses.body, call.body, approval_body, call.hash())
