"""Text canonicalisation applied before tokenisation in the hardened detector.

Tokeniser-desynchronisation attacks change *bytes* without changing what a reader
sees: homoglyphs (Cyrillic `а` for Latin `a`), zero-width characters, typographic
quotes/dashes, exotic whitespace.  Each such change moves a token boundary and
destroys the next `context_window` scored positions.  Canonicalising first removes
that attack surface entirely.  The same pass is applied at registration time in the
signed registry so hashes are stable.
"""
from __future__ import annotations

import re
import unicodedata

ZERO_WIDTH = {
    "\u200b", "\u200c", "\u200d", "\u200e", "\u200f", "\u2060", "\u2061", "\u2062",
    "\u2063", "\u2064", "\ufeff", "\u00ad", "\u180e", "\u034f",
}

# Hand-curated map of the confusables that actually get used in attacks.  A full
# deployment should load the Unicode consortium's confusables.txt instead.
CONFUSABLES = {
    # Cyrillic lowercase
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c", "\u0443": "y",
    "\u0445": "x", "\u0456": "i", "\u0458": "j", "\u0455": "s", "\u04bb": "h", "\u0501": "d",
    "\u051d": "w", "\u0261": "g", "\u04cf": "l", "\u0454": "e",
    # Cyrillic uppercase
    "\u0410": "A", "\u0412": "B", "\u0415": "E", "\u041a": "K", "\u041c": "M", "\u041d": "H",
    "\u041e": "O", "\u0420": "P", "\u0421": "C", "\u0422": "T", "\u0425": "X", "\u0406": "I",
    "\u0408": "J", "\u0405": "S",
    # Greek
    "\u0391": "A", "\u0392": "B", "\u0395": "E", "\u0396": "Z", "\u0397": "H", "\u0399": "I",
    "\u039a": "K", "\u039c": "M", "\u039d": "N", "\u039f": "O", "\u03a1": "P", "\u03a4": "T",
    "\u03a5": "Y", "\u03a7": "X", "\u03bf": "o", "\u03bd": "v", "\u03c1": "p", "\u03b9": "i",
    "\u03b1": "a", "\u03ba": "k",
    # Latin look-alikes / symbols not covered by NFKC
    "\u2113": "l", "\u212e": "e", "\u0251": "a", "\u01c0": "l", "\u1d00": "A",
}

# Attackers use the reverse map; keep it next to the forward one so they stay in sync.
HOMOGLYPHS_FOR = {}
for _src, _dst in CONFUSABLES.items():
    HOMOGLYPHS_FOR.setdefault(_dst, []).append(_src)

TYPOGRAPHY = {
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u00ab": '"', "\u00bb": '"',
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2039": "'", "\u203a": "'",
    "\u2013": "-", "\u2014": "-", "\u2012": "-", "\u2010": "-", "\u2011": "-", "\u2212": "-",
    "\u2026": "...", "\u00a0": " ", "\u2009": " ", "\u200a": " ", "\u202f": " ", "\u3000": " ",
    "\u2028": "\n", "\u2029": "\n",
}

_WS = re.compile(r"[ \t\f\v\r]+")
_MULTI_NL = re.compile(r"\n\s*\n+")


def strip_zero_width(text: str) -> str:
    return "".join(ch for ch in text if ch not in ZERO_WIDTH)


def map_confusables(text: str) -> str:
    return "".join(CONFUSABLES.get(ch, ch) for ch in text)


def normalize_typography(text: str) -> str:
    return "".join(TYPOGRAPHY.get(ch, ch) for ch in text)


def canonicalize(text: str) -> str:
    """NFKC -> drop zero-width -> confusables -> typography -> whitespace collapse."""
    text = unicodedata.normalize("NFKC", text)
    text = strip_zero_width(text)
    text = map_confusables(text)
    text = normalize_typography(text)
    text = _WS.sub(" ", text)
    text = _MULTI_NL.sub("\n", text)
    return text.strip()
