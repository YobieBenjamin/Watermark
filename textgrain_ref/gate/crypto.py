"""Primitives for the gate: canonical encoding, hashing, Ed25519 signing, measurement.

Everything marked `# silicon:` is a software stand-in for something that belongs in
hardware in a real deployment.  The interfaces are the point: a TPM 2.0 resident key,
an HSM behind PKCS#11 or a TEE-sealed key all expose exactly `sign()` and a public
key, and never `export_private()`.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Optional, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def canonical_json(obj) -> bytes:
    """Deterministic encoding: sorted keys, no whitespace, UTF-8.  What gets signed."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pub_to_hex(pub: Ed25519PublicKey) -> str:
    return pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def pub_from_hex(h: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(bytes.fromhex(h))


def key_id_of(pub_hex: str) -> str:
    """Short stable identifier for a public key (first 16 hex of its SHA-256)."""
    return sha256_hex(bytes.fromhex(pub_hex))[:16]


class Signer(Protocol):
    key_id: str

    def public_hex(self) -> str: ...
    def sign(self, msg: bytes) -> bytes: ...


class SoftwareSigner:
    """Ed25519 key held in process memory.

    # silicon: in production this object is a handle to a non-exportable key in a
    # TPM 2.0 / HSM / TEE.  `sign()` is the only operation hardware offers; the
    # signature counter is the software stand-in for a TPM monotonic counter, which
    # makes the number of signatures ever issued auditable.
    """

    def __init__(self, key: Optional[Ed25519PrivateKey] = None, label: str = ""):
        self._key = key or Ed25519PrivateKey.generate()
        self._pub_hex = pub_to_hex(self._key.public_key())
        self.key_id = key_id_of(self._pub_hex)
        self.label = label
        self.n_signatures = 0

    def public_hex(self) -> str:
        return self._pub_hex

    def sign(self, msg: bytes) -> bytes:
        self.n_signatures += 1
        return self._key.sign(msg)

    def export_private(self) -> Ed25519PrivateKey:
        """What hardware refuses to do.  Exists only so the harness can model a key leak
        (`stolen_session_key`) and show what the gate can and cannot catch afterwards."""
        return self._key

    @staticmethod
    def from_pem(path: str | Path) -> "SoftwareSigner":
        return SoftwareSigner(serialization.load_pem_private_key(Path(path).read_bytes(), password=None))

    def to_pem(self, path: str | Path) -> None:
        Path(path).write_bytes(self._key.private_bytes(serialization.Encoding.PEM,
                                                       serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption()))


def verify(pub_hex: str, msg: bytes, sig: bytes) -> bool:
    try:
        pub_from_hex(pub_hex).verify(sig, msg)
        return True
    except (InvalidSignature, ValueError):
        return False


def measure(paths: Iterable[str | Path], root: Optional[str | Path] = None) -> str:
    """Hash of the gate's own code (and anything else it must run unmodified).

    # silicon: this is what a TPM PCR or a TEE launch measurement provides -- a value
    # the running code cannot forge about itself because it is taken by something
    # below the code.  Here it is taken by the code, so it only demonstrates the
    # *check* (deployment link pins the expected value, verifier compares).
    """
    h = hashlib.sha256()
    root = Path(root) if root else None
    for p in sorted(Path(x) for x in paths):
        name = str(p.relative_to(root) if root else p.name)
        h.update(name.encode("utf-8") + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def gate_source_files() -> list[Path]:
    """The files whose integrity the measurement covers: this package's own modules."""
    here = Path(__file__).resolve().parent
    return sorted(p for p in here.glob("*.py"))
