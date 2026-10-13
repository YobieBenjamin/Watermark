#!/usr/bin/env python3
"""Render a README results section from a harness run (`report.json`).

    python results/readme_table.py results/hf-20261013-081500            # print the Markdown block
    python results/readme_table.py results/hf-20261013-081500 --write    # replace the block between
                                                                           # <!-- hf-table:start/end --> in README.md

Same columns as the toy table in the README, so the two read side by side.  The block
holds only what is computed from the run (heading, links, table, calibration line, plots);
the prose that interprets it lives outside the markers and is written by hand.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

START, END = "<!-- hf-table:start -->", "<!-- hf-table:end -->"

COLUMNS = ["attack", "tokens changed", "TPR naive", "TPR hardened", "mean z naive", "mean z hardened",
           "retrieval R@1", "registry exact / tampered / unknown", "stack attributed"]


def pct(x: float) -> str:
    return f"{int(round(100 * x))}%"


def row(a: dict) -> str:
    attributed = pct(a["stack_attributed"])
    if "localization_mean_iou" in a:
        attributed += f" (localisation IoU {a['localization_mean_iou']:.2f}, found {pct(a['localization_found_rate'])})"
    cells = [
        a["attack"].replace("3x", "3×"),
        pct(a["tokens_changed_frac"]),
        pct(a["tpr_analytic_naive"]),
        pct(a["tpr_analytic_hardened"]),
        f"{a['mean_z_naive']:.1f}",
        f"{a['mean_z_hardened']:.1f}",
        pct(a["retrieval_recall_at_1"]),
        f"{int(round(100 * a['registry_exact']))} / {int(round(100 * a['registry_tampered']))} / {int(round(100 * a['registry_unknown']))}",
        attributed,
    ]
    return "| " + " | ".join(cells) + " |"


def render(run_dir: pathlib.Path) -> str:
    r = json.loads((run_dir / "report.json").read_text())
    c, g, n = r["config"], r["generation"], r["null"]
    rel = run_dir.as_posix()
    model = c.get("model") or "toy n-gram"
    short = model.split("/")[-1]
    head = (f"## What the harness shows (real model: {short}, {c['n_samples']} × {c['n_tokens']} tokens, "
            f"α = {c['alpha']:g}, β = {c['watermark']['beta']:g})")
    lines = [
        START,
        head,
        "",
        f"Full report: [`{rel}/report.md`]({rel}/report.md). Model `{model}` on Apple Silicon (MPS, bf16), "
        f"temperature {c['temperature']:g}, top-p {c['top_p']:g}; embedder `{r['embedder']}`; the two LLM-driven "
        f"attacks use the same model, unwatermarked, as the attacker. Runtime {r['runtime_seconds'] / 60:.0f} min "
        f"on an M4 Max. `bash results/reproduce_hf.sh` regenerates it.",
        "",
        "| " + " | ".join(COLUMNS) + " |",
        "|" + "---|" * len(COLUMNS),
    ]
    lines += [row(a) for a in r["attacks"]]
    human = n["registry_verdicts_on_human"]
    lines += [
        "",
        f"Null calibration on {n['n']} passages ({n['n'] // 2} human, {n['n'] - n['n'] // 2} unwatermarked model): "
        f"analytic FPR {100 * n['fpr_analytic_hardened']:.1f}% at α = {100 * c['alpha']:g}% (naive {100 * n['fpr_analytic_naive']:.1f}%); "
        f"registry says `unknown` for {pct(human['unknown'])} of human text. Same prompt + same key gives distinct outputs "
        f"{pct(g['distinct_outputs_same_prompt_same_key'])} of the time. Achieved entropy budget "
        f"{g['mean_beta_achieved']:.3f} of a requested {g['beta_requested']:g}; "
        f"{100 * g['masked_context_fraction']:.1f}% of positions masked as repeated contexts.",
        "",
        f"![which layer identifies the source]({rel}/tpr.png)",
        f"![signal strength per attack]({rel}/zscore.png)",
        END,
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", type=pathlib.Path)
    ap.add_argument("--write", nargs="?", const="README.md", default=None, metavar="README",
                    help="replace the block between the hf-table markers in this file (default README.md)")
    args = ap.parse_args()
    block = render(args.run_dir)
    if not args.write:
        sys.stdout.write(block)
        return 0
    readme = pathlib.Path(args.write)
    text = readme.read_text()
    i, j = text.find(START), text.find(END)
    if i < 0 or j < 0:
        sys.exit(f"{readme}: markers {START} … {END} not found; add them where the section should go")
    readme.write_text(text[:i] + block.rstrip("\n") + text[j + len(END):])
    print(f"{readme}: real-model section regenerated from {args.run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
