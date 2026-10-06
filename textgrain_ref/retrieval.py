"""Semantic retrieval index over generated outputs (the layer that survives paraphrase).

The provider stores sentence-level embeddings of everything it generates; a
verifier queries with a suspect text and gets back the nearest original.  Because the
match is on meaning rather than tokens, this is the only layer that is not defeated
by rewriting or translation (Krishna et al., 2023).

Two embedders:
  * `SentenceTransformerEmbedder` -- multilingual, the real thing (needs the `hf` extra
    and a model download, e.g. `paraphrase-multilingual-MiniLM-L12-v2` or
    `intfloat/multilingual-e5-small`).
  * `HashingEmbedder` -- offline character n-gram feature hashing.  It only captures
    lexical overlap, so it demonstrates the plumbing and near-duplicate recall, not
    paraphrase recall.  The report labels which one was used.
"""
from __future__ import annotations

import json
import re
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from .canonicalize import canonicalize

_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(\[])")


def split_sentences(text: str, max_words: int = 60) -> list[str]:
    text = canonicalize(text)
    parts = [s.strip() for s in _SENT_RE.split(text) if s.strip()]
    out: list[str] = []
    for s in parts:
        words = s.split()
        for i in range(0, len(words), max_words):
            chunk = " ".join(words[i:i + max_words])
            if len(chunk) >= 8:
                out.append(chunk)
    return out or ([text] if text else [])


class HashingEmbedder:
    name = "hashing-char-ngram"

    def __init__(self, dim: int = 4096, n_min: int = 3, n_max: int = 5):
        self.dim, self.n_min, self.n_max = dim, n_min, n_max

    def _features(self, s: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        s = " " + re.sub(r"\s+", " ", s.lower()) + " "
        for n in range(self.n_min, self.n_max + 1):
            for i in range(len(s) - n + 1):
                h = zlib.crc32(s[i:i + n].encode("utf-8"))  # stable across processes, unlike hash()
                v[(h >> 1) % self.dim] += 1.0 if h & 1 else -1.0
        v = np.sign(v) * np.log1p(np.abs(v))
        norm = np.linalg.norm(v)
        return v / norm if norm > 0 else v

    def encode(self, sentences: Sequence[str]) -> np.ndarray:
        return np.stack([self._features(s) for s in sentences]) if sentences else np.zeros((0, self.dim), np.float32)


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str = "paraphrase-multilingual-MiniLM-L12-v2", device: Optional[str] = None):
        from sentence_transformers import SentenceTransformer

        self.name = model_name
        self.model = SentenceTransformer(model_name, device=device)
        self.dim = int(self.model.get_sentence_embedding_dimension())

    def encode(self, sentences: Sequence[str]) -> np.ndarray:
        if not sentences:
            return np.zeros((0, self.dim), np.float32)
        return np.asarray(self.model.encode(list(sentences), normalize_embeddings=True, show_progress_bar=False), dtype=np.float32)


def build_embedder(name: Optional[str]):
    if not name or name == "hashing":
        return HashingEmbedder()
    return SentenceTransformerEmbedder(name)


@dataclass
class Hit:
    doc_id: str
    score: float            # mean over query sentences of best cosine similarity within this doc
    coverage: float         # fraction of query sentences whose best match is in this doc
    max_sim: float = 0.0    # best single-sentence similarity within this doc
    n_strong: int = 0       # query sentences with similarity >= 0.8 in this doc


class RetrievalIndex:
    def __init__(self, embedder):
        import faiss

        self.faiss = faiss
        self.embedder = embedder
        self.index = faiss.IndexFlatIP(embedder.dim)
        self.meta: list[tuple[str, int]] = []       # (doc_id, sentence_idx)
        self.docs: dict[str, str] = {}

    def add(self, doc_id: str, text: str) -> int:
        sents = split_sentences(text)
        vecs = self.embedder.encode(sents)
        if vecs.shape[0]:
            self.index.add(vecs)
            self.meta.extend((doc_id, i) for i in range(len(sents)))
        self.docs[doc_id] = text
        return len(sents)

    def query(self, text: str, k: int = 5, per_sentence_k: int = 8) -> list[Hit]:
        sents = split_sentences(text)
        if not sents or self.index.ntotal == 0:
            return []
        vecs = self.embedder.encode(sents)
        sims, idx = self.index.search(vecs, min(per_sentence_k, self.index.ntotal))
        best: dict[str, list[float]] = {}
        top_doc_votes: dict[str, int] = {}
        for qi in range(len(sents)):
            seen_doc: dict[str, float] = {}
            for s, j in zip(sims[qi], idx[qi]):
                if j < 0:
                    continue
                d = self.meta[j][0]
                if d not in seen_doc:
                    seen_doc[d] = float(s)
            for d, s in seen_doc.items():
                best.setdefault(d, [0.0] * len(sents))[qi] = s
            if seen_doc:
                top = max(seen_doc.items(), key=lambda kv: kv[1])[0]
                top_doc_votes[top] = top_doc_votes.get(top, 0) + 1
        hits = [Hit(d, float(np.mean(v)), top_doc_votes.get(d, 0) / len(sents), float(np.max(v)),
                    int(sum(1 for x in v if x >= 0.8))) for d, v in best.items()]
        hits.sort(key=lambda h: (-h.score, -h.n_strong, -h.coverage))
        return hits[:k]

    # -- persistence ----------------------------------------------------------
    def save(self, directory: str | Path) -> None:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.faiss.write_index(self.index, str(directory / "sentences.faiss"))
        (directory / "meta.json").write_text(json.dumps({"meta": self.meta, "docs": self.docs,
                                                          "embedder": getattr(self.embedder, "name", "?")}))

    @classmethod
    def load(cls, directory: str | Path, embedder) -> "RetrievalIndex":
        directory = Path(directory)
        obj = cls(embedder)
        obj.index = obj.faiss.read_index(str(directory / "sentences.faiss"))
        data = json.loads((directory / "meta.json").read_text())
        obj.meta = [tuple(x) for x in data["meta"]]
        obj.docs = data["docs"]
        return obj
