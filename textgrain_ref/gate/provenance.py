"""Provenance of inputs: the first five layers, applied at the boundary into the model.

The watermark stack was built to answer questions after the fact.  Here the same
components answer them *before* the model's next action executes, about the content
the model is acting on.  The oracle lives on the gate side; the agent never runs it
and its claims about its inputs are compared against the oracle's answer, never
substituted for it.

Levels (what policy consumes):

    0  verified               content signed by the session's principal (their own instruction)
    1  unverified             no watermark, no registry match -- human text or an unwatermarked model
    2  self_generated         registry EXACT -- this deployment's own earlier output
    3  tampered               registry TAMPERED -- an edited copy of a genuine output
    3  watermark_unregistered watermark present under our key, nothing registered -- spoof / stolen key / gap

`watermark_missed` is reported alongside: the whole-passage test failed but retrieval
found the original.  That is the rewrite attack; at runtime it means an instruction
that *looks* human was derived from machine output.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..canonicalize import canonicalize
from ..detector import Detector
from ..registry import Registry
from ..retrieval import RetrievalIndex
from .crypto import Signer, key_id_of, sha256_hex, verify

LEVELS = {"verified": 0, "unverified": 1, "self_generated": 2, "tampered": 3, "watermark_unregistered": 3}
CONTENT_DOMAIN = b"textgrain-gate-content-v1|"


def content_hash(text: str) -> str:
    return sha256_hex(canonicalize(text).encode("utf-8"))


def attest_content(signer: Signer, text: str) -> dict:
    """A principal vouching for a piece of content (their own instruction, a document they checked)."""
    h = content_hash(text)
    return {"content_hash": h, "key_id": signer.key_id, "sig": signer.sign(CONTENT_DOMAIN + h.encode()).hex()}


def attestation_valid(att: Optional[dict], text: str, principal_pub_hex: str) -> bool:
    if not att or len(principal_pub_hex) != 64:
        return False
    h = content_hash(text)
    return (att.get("content_hash") == h and att.get("key_id") == key_id_of(principal_pub_hex)
            and verify(principal_pub_hex, CONTENT_DOMAIN + h.encode(), bytes.fromhex(str(att.get("sig", "")))))


@dataclass
class ProvenanceLabel:
    status: str                 # verified | unverified | self_generated | tampered | watermark_unregistered
    level: int
    wm_detected: bool
    p_value: float
    z_score: float
    registry_status: str        # exact | tampered | unknown | n/a
    record_id: Optional[int] = None
    similarity: float = 0.0
    changed_fraction: float = 0.0
    watermark_missed: bool = False
    content_hash: str = ""

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


class NullOracle:
    """No watermark stack behind the gate: content is `verified` if the principal signed it, else `unverified`.
    Taint still works (unverified inputs hold irreversible actions); the self-generated / tampered /
    unregistered distinctions need the real oracle."""

    def label(self, text: str, attestation: Optional[dict] = None, principal_pub_hex: str = "") -> "ProvenanceLabel":
        h = content_hash(text)
        if attestation_valid(attestation, text, principal_pub_hex):
            return ProvenanceLabel("verified", 0, False, 1.0, 0.0, "n/a", content_hash=h)
        return ProvenanceLabel("unverified", 1, False, 1.0, 0.0, "n/a", content_hash=h)


class ProvenanceOracle:
    """Detector + registry + retrieval, held by the gate."""

    def __init__(self, detector: Detector, registry: Registry, index: Optional[RetrievalIndex] = None,
                 doc_to_record: Optional[dict] = None, similarity_threshold: float = 0.5):
        self.detector = detector
        self.registry = registry
        self.index = index
        self.doc_to_record = doc_to_record or {}
        self.similarity_threshold = similarity_threshold

    def label(self, text: str, attestation: Optional[dict] = None, principal_pub_hex: str = "") -> ProvenanceLabel:
        h = content_hash(text)
        if attestation_valid(attestation, text, principal_pub_hex):
            return ProvenanceLabel("verified", 0, False, 1.0, 0.0, "n/a", content_hash=h)
        det = self.detector.detect(text)
        v = self.registry.verify(text, self.index, self.doc_to_record, self.similarity_threshold)
        if v.status == "exact" and v.signature_valid:
            status = "self_generated"
        elif v.status == "tampered":
            status = "tampered"
        elif det.detected:
            status = "watermark_unregistered"
        else:
            status = "unverified"
        missed = (not det.detected) and v.status in ("exact", "tampered")
        return ProvenanceLabel(status, LEVELS[status], bool(det.detected), float(det.p_value), float(det.z_score),
                               v.status, v.record_id, float(v.similarity), float(v.changed_fraction), missed, h)
