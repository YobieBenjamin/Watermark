"""textgrain-ref: reference implementation of a textGrain-style LLM watermark with a
hardened detector, semantic retrieval, a signed-output registry and an attack harness."""
from .watermark import TextGrainConfig, TextGrainSampler, generate  # noqa: F401
from .detector import Detector  # noqa: F401
from .canonicalize import canonicalize  # noqa: F401

__version__ = "0.2.0"
