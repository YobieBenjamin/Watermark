"""Signed-output registry (the integrity layer).

A watermark is a presence signal: it says "we had a hand in this", not "this is
exactly what we said".  Piggyback spoofing exploits the gap -- edit three tokens of
a genuine output and the detector still fires.  The registry closes it:

    register(text)   -> canonical SHA-256 of the output, Ed25519-signed, stored
    verify(text)     -> EXACT     (hash matches a registered output; signature valid)
                        TAMPERED  (no exact match, but retrieval finds a near original;
                                   the differing spans are returned)
                        UNKNOWN   (nothing registered resembles it)

Combined with the watermark p-value this turns "watermark present" into attribution
with tamper localisation.  Verification endpoints are oracles and must be gated
exactly like the detector.
"""
from __future__ import annotations

import difflib
import hashlib
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .canonicalize import canonicalize
from .retrieval import RetrievalIndex


def canonical_hash(text: str) -> str:
    return hashlib.sha256(canonicalize(text).encode("utf-8")).hexdigest()


def _message(h: str, model: str, created: str) -> bytes:
    return f"textgrain-ref|{h}|{model}|{created}".encode("utf-8")


@dataclass
class Record:
    record_id: int
    canonical_hash: str
    text: str
    model: str
    created: str
    signature: bytes


@dataclass
class DiffSpan:
    op: str                 # replace | insert | delete
    original: str
    suspect: str
    orig_tokens: tuple[int, int]
    susp_tokens: tuple[int, int]


@dataclass
class Verdict:
    status: str             # exact | tampered | unknown
    record_id: Optional[int] = None
    similarity: float = 0.0
    signature_valid: Optional[bool] = None
    changed_fraction: float = 0.0
    diff: list[DiffSpan] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "status": self.status, "record_id": self.record_id, "similarity": self.similarity,
            "signature_valid": self.signature_valid, "changed_fraction": self.changed_fraction,
            "diff": [{"op": d.op, "original": d.original, "suspect": d.suspect} for d in self.diff],
        }


class Registry:
    def __init__(self, db_path: str | Path, signing_key: Optional[Ed25519PrivateKey] = None,
                 public_key: Optional[Ed25519PublicKey] = None):
        self.db = sqlite3.connect(str(db_path))
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "canonical_hash TEXT NOT NULL, text TEXT NOT NULL, model TEXT NOT NULL, "
            "created TEXT NOT NULL, signature BLOB NOT NULL)"
        )
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_hash ON records(canonical_hash)")
        self.db.commit()
        self.signing_key = signing_key
        self.public_key = public_key or (signing_key.public_key() if signing_key else None)

    # -- keys -----------------------------------------------------------------
    @staticmethod
    def generate_key() -> Ed25519PrivateKey:
        return Ed25519PrivateKey.generate()

    @staticmethod
    def save_key(key: Ed25519PrivateKey, path: str | Path) -> None:
        Path(path).write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                 serialization.PrivateFormat.PKCS8,
                                                 serialization.NoEncryption()))

    @staticmethod
    def load_key(path: str | Path) -> Ed25519PrivateKey:
        return serialization.load_pem_private_key(Path(path).read_bytes(), password=None)

    def public_key_pem(self) -> str:
        return self.public_key.public_bytes(serialization.Encoding.PEM,
                                            serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    # -- write ----------------------------------------------------------------
    def register(self, text: str, model: str = "") -> Record:
        if self.signing_key is None:
            raise RuntimeError("registry opened without a signing key")
        h = canonical_hash(text)
        created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        sig = self.signing_key.sign(_message(h, model, created))
        cur = self.db.execute("INSERT INTO records (canonical_hash, text, model, created, signature) VALUES (?,?,?,?,?)",
                              (h, text, model, created, sig))
        self.db.commit()
        return Record(cur.lastrowid, h, text, model, created, sig)

    # -- read -----------------------------------------------------------------
    def get(self, record_id: int) -> Optional[Record]:
        row = self.db.execute("SELECT id, canonical_hash, text, model, created, signature FROM records WHERE id=?",
                              (record_id,)).fetchone()
        return Record(*row) if row else None

    def lookup_exact(self, text: str) -> Optional[Record]:
        row = self.db.execute("SELECT id, canonical_hash, text, model, created, signature FROM records "
                              "WHERE canonical_hash=? ORDER BY id LIMIT 1", (canonical_hash(text),)).fetchone()
        return Record(*row) if row else None

    def verify_signature(self, rec: Record) -> bool:
        if self.public_key is None:
            return False
        try:
            self.public_key.verify(rec.signature, _message(rec.canonical_hash, rec.model, rec.created))
            return True
        except InvalidSignature:
            return False

    def count(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM records").fetchone()[0])

    # -- verdict ----------------------------------------------------------------
    def verify(self, text: str, index: Optional[RetrievalIndex] = None, doc_to_record: Optional[dict] = None,
               similarity_threshold: float = 0.5) -> Verdict:
        rec = self.lookup_exact(text)
        if rec is not None:
            return Verdict("exact", rec.record_id, 1.0, self.verify_signature(rec), 0.0, [])
        if index is None:
            return Verdict("unknown")
        hits = index.query(text, k=1)
        # a diluted document has a low *mean* similarity but several near-identical sentences
        strong = bool(hits) and (hits[0].score >= similarity_threshold or hits[0].n_strong >= 2)
        if not strong:
            return Verdict("unknown", similarity=hits[0].score if hits else 0.0)
        hit = hits[0]
        rec_id = (doc_to_record or {}).get(hit.doc_id)
        original = index.docs.get(hit.doc_id, "")
        if rec_id is not None:
            rec = self.get(int(rec_id))
            if rec is not None:
                original = rec.text
        diff, changed = token_diff(original, text)
        return Verdict("tampered", rec_id, float(hit.score),
                       self.verify_signature(rec) if rec is not None else None, changed, diff)


def token_diff(original: str, suspect: str) -> tuple[list[DiffSpan], float]:
    a = canonicalize(original).split()
    b = canonicalize(suspect).split()
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    spans = []
    changed = 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        changed += max(i2 - i1, j2 - j1)
        spans.append(DiffSpan(op, " ".join(a[i1:i2]), " ".join(b[j1:j2]), (i1, i2), (j1, j2)))
    frac = changed / max(len(a), len(b), 1)
    return spans, float(frac)
