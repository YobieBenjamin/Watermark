"""python -m textgrain_ref.gate [--out DIR] [--seed N] [--tokens N] [--no-sidecar]"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .harness import GateHarnessConfig, run, verify_log_dir


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m textgrain_ref.gate", description="run the execution-gate scenario sweep")
    ap.add_argument("--out", default="out-gate")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--tokens", type=int, default=300)
    ap.add_argument("--alpha", type=float, default=0.01)
    ap.add_argument("--no-sidecar", action="store_true", help="skip the separate-process run")
    ap.add_argument("--verify-log", metavar="DIR", default=None, help="re-verify the decision log of a finished run and exit")
    args = ap.parse_args(argv)
    if args.verify_log:
        res = verify_log_dir(args.verify_log)
        print(json.dumps(res))
        return 0 if res["ok"] else 1
    report = run(GateHarnessConfig(seed=args.seed, n_tokens=args.tokens, alpha=args.alpha, out_dir=args.out,
                                   sidecar=not args.no_sidecar))
    print((Path(args.out) / "report.md").read_text())
    return 0 if report["summary"]["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
