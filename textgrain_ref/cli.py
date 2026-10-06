"""CLI.

    python -m textgrain_ref.cli demo      --backend toy --n 30 --tokens 300 --out out
    python -m textgrain_ref.cli demo      --backend hf --model Qwen/Qwen2.5-1.5B-Instruct \
                                          --embedder paraphrase-multilingual-MiniLM-L12-v2 --llm-attacks
    python -m textgrain_ref.cli generate  --backend toy --key-hex <64 hex> --tokens 200 --prompt "It was"
    python -m textgrain_ref.cli detect    --backend toy --key-hex <64 hex> --file suspect.txt
    python -m textgrain_ref.cli localize  --backend toy --key-hex <64 hex> --file long_document.txt
    python -m textgrain_ref.cli verify    --out out --file suspect.txt          # registry + retrieval
    python -m textgrain_ref.cli keygen
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path

import numpy as np

from .detector import Detector
from .harness import HarnessConfig, build_lm, run
from .registry import Registry
from .retrieval import RetrievalIndex, build_embedder
from .watermark import TextGrainConfig, TextGrainSampler, generate


def _wm_config(args) -> TextGrainConfig:
    return TextGrainConfig(n_blocks=args.blocks, n_columns=args.columns, beta=args.beta,
                           context_window=args.context_window)


def _add_wm_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--backend", choices=["toy", "hf"], default="toy")
    p.add_argument("--model", default=None, help="HF model name/path (hf backend)")
    p.add_argument("--blocks", type=int, default=32)
    p.add_argument("--columns", type=int, default=16)
    p.add_argument("--beta", type=float, default=0.5, help="entropy budget (fraction of H(P) removed)")
    p.add_argument("--context-window", type=int, default=3)


def _read_text(args) -> str:
    if args.file:
        return Path(args.file).read_text(encoding="utf-8")
    return sys.stdin.read()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="textgrain-ref")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo", help="run the full evaluation harness")
    _add_wm_args(d)
    d.add_argument("--n", type=int, default=30, help="samples per condition")
    d.add_argument("--tokens", type=int, default=300)
    d.add_argument("--seed", type=int, default=7)
    d.add_argument("--alpha", type=float, default=0.01)
    d.add_argument("--out", default="out")
    d.add_argument("--embedder", default=None, help="sentence-transformers model name; default offline hashing")
    d.add_argument("--quick", action="store_true", help="small sweep for CI")
    d.add_argument("--llm-attacks", action="store_true", help="include paraphrase / translation attacks (hf only)")
    d.add_argument("--temperature", type=float, default=1.0)
    d.add_argument("--top-p", type=float, default=1.0)

    g = sub.add_parser("generate", help="generate watermarked text")
    _add_wm_args(g)
    g.add_argument("--key-hex", required=True)
    g.add_argument("--prompt", required=True)
    g.add_argument("--tokens", type=int, default=200)
    g.add_argument("--seed", type=int, default=0)
    g.add_argument("--no-watermark", action="store_true")

    for name in ("detect", "localize"):
        s = sub.add_parser(name)
        _add_wm_args(s)
        s.add_argument("--key-hex", required=True)
        s.add_argument("--file", default=None, help="text file (default: stdin)")
        s.add_argument("--alpha", type=float, default=0.01)
        s.add_argument("--no-canonicalize", action="store_true")
        if name == "localize":
            s.add_argument("--window", type=int, default=120)
            s.add_argument("--stride", type=int, default=30)

    v = sub.add_parser("verify", help="registry + retrieval verdict for a suspect text")
    v.add_argument("--out", default="out", help="directory produced by `demo`")
    v.add_argument("--file", default=None)
    v.add_argument("--embedder", default=None)

    sub.add_parser("keygen", help="print a fresh 256-bit watermark key (hex)")

    args = ap.parse_args(argv)

    if args.cmd == "keygen":
        print(secrets.token_hex(32))
        return 0

    if args.cmd == "demo":
        cfg = HarnessConfig(backend=args.backend, model=args.model, embedder=args.embedder, n_samples=args.n,
                            n_tokens=args.tokens, seed=args.seed, alpha=args.alpha, quick=args.quick,
                            temperature=args.temperature, top_p=args.top_p,
                            include_llm_attacks=args.llm_attacks, out_dir=args.out, watermark=_wm_config(args))
        run(cfg)
        print((Path(args.out) / "report.md").read_text())
        return 0

    if args.cmd == "generate":
        cfg = HarnessConfig(backend=args.backend, model=args.model)
        lm = build_lm(cfg)
        key = bytes.fromhex(args.key_hex)
        sampler = None if args.no_watermark else TextGrainSampler(key, _wm_config(args), lm.vocab_size)
        prompt = lm.build_prompt(args.prompt) if hasattr(lm, "build_prompt") else lm.encode(args.prompt)
        rng = np.random.default_rng(args.seed)
        if hasattr(lm, "generate"):
            ids = lm.generate(prompt, args.tokens, sampler, rng, context_window=args.context_window)
        else:
            ids = generate(lm, prompt, args.tokens, sampler, rng, context_window=args.context_window)
        print(lm.decode(ids))
        return 0

    if args.cmd in ("detect", "localize"):
        cfg = HarnessConfig(backend=args.backend, model=args.model)
        lm = build_lm(cfg)
        det = Detector(bytes.fromhex(args.key_hex), lm.encode, _wm_config(args),
                       canonicalize_input=not args.no_canonicalize, alpha=args.alpha,
                       token_spans=getattr(lm, "token_spans", None))
        text = _read_text(args)
        if args.cmd == "detect":
            print(json.dumps(det.detect(text).as_dict(), indent=1))
        else:
            loc = det.localize(text, window=args.window, stride=args.stride)
            print(json.dumps({"n_windows": loc.n_windows, "segments": [s.__dict__ for s in loc.segments]}, indent=1))
        return 0

    if args.cmd == "verify":
        out = Path(args.out)
        reg = Registry(out / "registry.sqlite", Registry.load_key(out / "registry_signing_key.pem"))
        index = RetrievalIndex.load(out / "retrieval_index", build_embedder(args.embedder))
        doc_to_record = {d: i + 1 for i, d in enumerate(index.docs)}  # demo registers wm-i as record i+1
        print(json.dumps(reg.verify(_read_text(args), index, doc_to_record).as_dict(), indent=1))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
