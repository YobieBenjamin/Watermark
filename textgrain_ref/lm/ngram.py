"""A toy trigram language model (interpolated absolute discounting) over word tokens.

It exists so that the whole stack -- embedding, detection, attacks, retrieval,
registry -- can be exercised and self-validated with no model download, no GPU and
no network.  It is not meant to produce good prose; it is meant to produce
distributions with real entropy whose tokens round-trip through a tokenizer, which
is all the watermark cares about.  Swap in `HFLanguageModel` for real experiments.
"""
from __future__ import annotations

import hashlib
import os
import pickle
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np

TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?|[^\sA-Za-z0-9]")
UNK = "<unk>"


class WordTokenizer:
    """Regex word/punctuation tokenizer with a fixed vocabulary (OOV -> <unk>)."""

    def __init__(self, vocab: Sequence[str]):
        self.itos = list(vocab)
        self.stoi = {w: i for i, w in enumerate(self.itos)}
        self.unk_id = self.stoi[UNK]

    @staticmethod
    def tokenize(text: str) -> list[str]:
        return TOKEN_RE.findall(text)

    def encode(self, text: str) -> list[int]:
        return [self.stoi.get(t, self.unk_id) for t in self.tokenize(text)]

    def decode(self, ids: Iterable[int]) -> str:
        toks = [self.itos[int(i)] for i in ids]
        return detokenize(toks)

    def token_spans(self, text: str) -> list[tuple[int, int]]:
        return [(m.start(), m.end()) for m in TOKEN_RE.finditer(text)]

    @property
    def vocab_size(self) -> int:
        return len(self.itos)


_NO_SPACE_BEFORE = set(".,;:!?)]}'\"")
_NO_SPACE_AFTER = set("([{\"'")


def detokenize(tokens: Sequence[str]) -> str:
    out: list[str] = []
    quote_open = False
    for tok in tokens:
        if tok == '"':
            if quote_open:
                out.append(tok)
            else:
                out.append((" " if out else "") + tok)
            quote_open = not quote_open
            continue
        if not out:
            out.append(tok)
        elif tok in _NO_SPACE_BEFORE or out[-1] in _NO_SPACE_AFTER or (out[-1] == '"' and quote_open):
            out.append(tok)
        else:
            out.append(" " + tok)
    return "".join(out)


class NGramLM:
    """Interpolated absolute-discounting trigram LM with dense full-vocabulary outputs."""

    def __init__(self, tokenizer: WordTokenizer, discount: float = 0.75, alpha: float = 0.1):
        self.tok = tokenizer
        self.vocab_size = tokenizer.vocab_size
        self.discount = discount
        self.alpha = alpha
        self.eos_id: Optional[int] = None
        self.uni: Optional[np.ndarray] = None       # dense unigram distribution
        self.bi: dict = {}                           # w1 -> (ids, counts, total, n_types)
        self.tri: dict = {}                          # (w1, w2) -> (ids, counts, total, n_types)

    # -- training --------------------------------------------------------------
    @classmethod
    def train(cls, texts: Iterable[str], vocab_size: int = 12000, discount: float = 0.75) -> "NGramLM":
        counts: dict[str, int] = defaultdict(int)
        token_streams = []
        for text in texts:
            toks = WordTokenizer.tokenize(text)
            token_streams.append(toks)
            for t in toks:
                counts[t] += 1
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[: vocab_size - 1]
        vocab = [UNK] + [w for w, _ in top]
        tokenizer = WordTokenizer(vocab)
        lm = cls(tokenizer, discount=discount)

        uni = np.zeros(lm.vocab_size)
        bi_c: dict = defaultdict(lambda: defaultdict(int))
        tri_c: dict = defaultdict(lambda: defaultdict(int))
        for toks in token_streams:
            ids = [tokenizer.stoi.get(t, tokenizer.unk_id) for t in toks]
            for i, w in enumerate(ids):
                uni[w] += 1
                if i >= 1:
                    bi_c[ids[i - 1]][w] += 1
                if i >= 2:
                    tri_c[(ids[i - 2], ids[i - 1])][w] += 1
        lm.uni = (uni + lm.alpha) / (uni.sum() + lm.alpha * lm.vocab_size)
        lm.bi = {k: cls._pack(v) for k, v in bi_c.items()}
        lm.tri = {k: cls._pack(v) for k, v in tri_c.items()}
        return lm

    @staticmethod
    def _pack(d: dict) -> tuple[np.ndarray, np.ndarray, float, int]:
        ids = np.fromiter(d.keys(), dtype=np.int64, count=len(d))
        c = np.fromiter(d.values(), dtype=np.float64, count=len(d))
        return ids, c, float(c.sum()), len(d)

    # -- inference ------------------------------------------------------------
    def _interpolate(self, table: Optional[tuple], lower: np.ndarray) -> np.ndarray:
        if table is None:
            return lower
        ids, c, total, n_types = table
        lam = self.discount * n_types / total
        p = lower * lam
        np.add.at(p, ids, np.maximum(c - self.discount, 0.0) / total)
        return p

    def next_token_dist(self, ids: Sequence[int]) -> np.ndarray:
        p1 = self.uni
        p2 = self._interpolate(self.bi.get(int(ids[-1])) if len(ids) >= 1 else None, p1)
        p3 = self._interpolate(self.tri.get((int(ids[-2]), int(ids[-1]))) if len(ids) >= 2 else None, p2)
        return p3 / p3.sum()

    def encode(self, text: str) -> list[int]:
        return self.tok.encode(text)

    def decode(self, ids: Iterable[int]) -> str:
        return self.tok.decode(ids)

    # -- persistence ----------------------------------------------------------
    def save(self, path: str | os.PathLike) -> None:
        with open(path, "wb") as f:
            pickle.dump(self, f, protocol=pickle.HIGHEST_PROTOCOL)

    @staticmethod
    def load(path: str | os.PathLike) -> "NGramLM":
        with open(path, "rb") as f:
            return pickle.load(f)


def corpus_files(corpus_dir: str | os.PathLike) -> list[Path]:
    return sorted(Path(corpus_dir).glob("*.txt"))


def load_or_train(corpus_dir: str | os.PathLike, cache_dir: str | os.PathLike, vocab_size: int = 12000) -> NGramLM:
    files = corpus_files(corpus_dir)
    if not files:
        raise FileNotFoundError(f"no .txt files in {corpus_dir}")
    h = hashlib.sha1()
    for f in files:
        h.update(f.name.encode())
        h.update(str(f.stat().st_size).encode())
    h.update(str(vocab_size).encode())
    cache = Path(cache_dir) / f"ngram-{h.hexdigest()[:12]}.pkl"
    if cache.exists():
        return NGramLM.load(cache)
    lm = NGramLM.train((f.read_text(encoding="utf-8", errors="ignore") for f in files), vocab_size=vocab_size)
    cache.parent.mkdir(parents=True, exist_ok=True)
    lm.save(cache)
    return lm
