"""End-to-end evaluation: embed -> attack -> (detector | retrieval | registry) -> report.

    python -m textgrain_ref.cli demo --backend toy --n 30 --tokens 300 --out out/

Writes `report.md`, `report.json`, `roc.png`, `tpr.png`, the retrieval index, the
registry database and the signing key into the output directory.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from .attacks import AttackContext, AttackSpec, default_sweep
from .canonicalize import canonicalize
from .detector import Detector, empirical_threshold
from .registry import Registry, token_diff
from .retrieval import RetrievalIndex, build_embedder
from .watermark import TextGrainConfig, TextGrainSampler, generate

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROMPTS = [
    "Explain how a bank clears a card payment, step by step.",
    "Write a short essay on why cities grow along rivers.",
    "Describe how vaccines train the immune system.",
    "Explain optimal transport to a curious high-school student.",
    "Why do some programming languages have garbage collection?",
    "Give a plain-language history of double-entry bookkeeping.",
    "What makes a bridge design earthquake-resistant?",
    "Explain how public-key cryptography lets strangers share secrets.",
    "Describe the economics of a farmers' market.",
    "How do airlines decide ticket prices?",
    "Explain why the sky is blue and sunsets are red.",
    "Describe how a jury trial works in general terms.",
]


@dataclass
class HarnessConfig:
    backend: str = "toy"
    model: Optional[str] = None
    embedder: Optional[str] = None            # None/"hashing" or a sentence-transformers name
    n_samples: int = 30
    n_tokens: int = 300
    seed: int = 7
    alpha: float = 0.01
    quick: bool = False
    temperature: float = 1.0
    top_p: float = 1.0
    include_llm_attacks: bool = False
    out_dir: str = "out"
    corpus_dir: str = str(ROOT / "data" / "corpus")
    human_dir: str = str(ROOT / "data" / "human")
    cache_dir: str = str(ROOT / ".cache")
    watermark: TextGrainConfig = field(default_factory=TextGrainConfig)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def auc_score(pos: Sequence[float], neg: Sequence[float]) -> float:
    pos = np.asarray(pos, dtype=np.float64)
    neg = np.asarray(neg, dtype=np.float64)
    if pos.size == 0 or neg.size == 0:
        return float("nan")
    allv = np.concatenate([pos, neg])
    order = allv.argsort(kind="mergesort")
    ranks = np.empty_like(order, dtype=np.float64)
    # average ranks for ties
    sorted_v = allv[order]
    i = 0
    while i < len(sorted_v):
        j = i
        while j + 1 < len(sorted_v) and sorted_v[j + 1] == sorted_v[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    r_pos = ranks[: pos.size].sum()
    return float((r_pos - pos.size * (pos.size + 1) / 2.0) / (pos.size * neg.size))


def roc_curve(pos: Sequence[float], neg: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    pos = np.asarray(pos)
    neg = np.asarray(neg)
    thr = np.unique(np.concatenate([pos, neg, [np.inf]]))[::-1]
    tpr = [(pos >= t).mean() for t in thr]
    fpr = [(neg >= t).mean() for t in thr]
    return np.asarray(fpr), np.asarray(tpr)


def human_windows(human_dir: str, lm, n_tokens: int, n_windows: int, rng: np.random.Generator) -> list[str]:
    texts = [p.read_text(encoding="utf-8", errors="ignore") for p in sorted(Path(human_dir).glob("*.txt"))]
    if not texts:
        raise FileNotFoundError(f"no human text in {human_dir}")
    text = canonicalize("\n".join(texts))
    spans = lm.token_spans(text) if hasattr(lm, "token_spans") else None
    windows = []
    if spans:
        for _ in range(n_windows):
            st = int(rng.integers(0, max(1, len(spans) - n_tokens - 1)))
            windows.append(text[spans[st][0]:spans[st + n_tokens - 1][1]])
    else:
        words = text.split()
        for _ in range(n_windows):
            st = int(rng.integers(0, max(1, len(words) - n_tokens - 1)))
            windows.append(" ".join(words[st:st + n_tokens]))
    return windows


def build_lm(cfg: HarnessConfig):
    if cfg.backend == "toy":
        from .lm.ngram import load_or_train
        return load_or_train(cfg.corpus_dir, cfg.cache_dir)
    if cfg.backend == "hf":
        from .lm.hf import HFLanguageModel
        if not cfg.model:
            raise ValueError("--model is required for the hf backend")
        return HFLanguageModel(cfg.model)
    raise ValueError(f"unknown backend {cfg.backend}")


def demo_key(seed: int) -> bytes:
    """Deterministic key for reproducible demos.  Production: `secrets.token_bytes(32)` in an HSM."""
    return hashlib.sha256(f"textgrain-ref-demo-key-{seed}".encode()).digest()


def make_prompts(cfg: HarnessConfig, lm, humans: list[str], rng: np.random.Generator) -> list[list[int]]:
    prompts = []
    for i in range(cfg.n_samples):
        if cfg.backend == "hf":
            prompts.append(lm.build_prompt(DEFAULT_PROMPTS[i % len(DEFAULT_PROMPTS)]))
        else:
            ids = lm.encode(humans[i % len(humans)])[:8]
            prompts.append(ids)
    return prompts


def generate_texts(lm, prompts, sampler, cfg: HarnessConfig, rng: np.random.Generator) -> list[str]:
    out = []
    for p in prompts:
        gen = lm.generate if hasattr(lm, "generate") else None
        if gen is not None:
            ids = gen(p, cfg.n_tokens, sampler, rng, cfg.temperature, cfg.top_p, cfg.watermark.context_window)
        else:
            ids = generate(lm, p, cfg.n_tokens, sampler, rng, cfg.temperature, cfg.top_p, cfg.watermark.context_window)
        out.append(lm.decode(ids))
    return out


def iou(a: tuple[int, int], b: tuple[int, int]) -> float:
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 0 else 0.0


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def run(cfg: HarnessConfig) -> dict:
    t0 = time.time()
    out_dir = Path(cfg.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(cfg.seed)
    log = lambda *a: print("[harness]", *a, flush=True)  # noqa: E731

    lm = build_lm(cfg)
    wm_cfg = cfg.watermark
    key = demo_key(cfg.seed)
    sampler = TextGrainSampler(key, wm_cfg, lm.vocab_size)
    log(f"backend={cfg.backend} vocab={lm.vocab_size} watermark={wm_cfg.as_dict()}")

    humans = human_windows(cfg.human_dir, lm, cfg.n_tokens, 2 * cfg.n_samples + 8, rng)
    human_null, human_pool = humans[: cfg.n_samples], humans[cfg.n_samples:]
    prompts = make_prompts(cfg, lm, human_pool, rng)

    log(f"generating {cfg.n_samples} watermarked + {cfg.n_samples} unwatermarked samples of {cfg.n_tokens} tokens")
    wm_texts = generate_texts(lm, prompts, sampler, cfg, rng)
    beta_hist = [h.beta_achieved for h in sampler.history if not h.masked]
    masked_frac = float(np.mean([h.masked for h in sampler.history])) if sampler.history else 0.0
    plain_texts = generate_texts(lm, prompts, None, cfg, rng)

    # diversity check: same prompt + same key twice must not collapse to one answer
    twice = generate_texts(lm, prompts[:4], sampler, cfg, np.random.default_rng(cfg.seed + 1))
    twice2 = generate_texts(lm, prompts[:4], sampler, cfg, np.random.default_rng(cfg.seed + 2))
    distinct_rate = float(np.mean([a != b for a, b in zip(twice, twice2)]))

    spans_fn = getattr(lm, "token_spans", None)
    hardened = Detector(key, lm.encode, wm_cfg, canonicalize_input=True, alpha=cfg.alpha, token_spans=spans_fn)
    naive = Detector(key, lm.encode, wm_cfg, canonicalize_input=False, alpha=cfg.alpha, token_spans=spans_fn)

    # retrieval + registry
    embedder = build_embedder(cfg.embedder)
    index = RetrievalIndex(embedder)
    signing_key = Registry.generate_key()
    Registry.save_key(signing_key, out_dir / "registry_signing_key.pem")
    db_path = out_dir / "registry.sqlite"
    if db_path.exists():
        db_path.unlink()
    registry = Registry(db_path, signing_key)
    doc_to_record = {}
    for i, t in enumerate(wm_texts):
        doc_id = f"wm-{i}"
        index.add(doc_id, t)
        doc_to_record[doc_id] = registry.register(t, model=f"{cfg.backend}:{cfg.model or 'ngram'}").record_id
    index.save(out_dir / "retrieval_index")
    log(f"indexed {index.index.ntotal} sentences with embedder={getattr(embedder, 'name', '?')}; registry has {registry.count()} records")

    # null calibration
    null_texts = human_null + plain_texts
    null_hard = [hardened.detect(t) for t in null_texts]
    null_naive = [naive.detect(t) for t in null_texts]
    null_scores = [r.neg_log10_p for r in null_hard]
    emp_thr = empirical_threshold(null_scores, cfg.alpha)
    fpr_analytic = float(np.mean([r.detected for r in null_hard]))
    fpr_naive = float(np.mean([r.detected for r in null_naive]))
    null_tokens_mean = float(np.mean([r.n_scored for r in null_hard]))
    registry_on_human = [registry.verify(t, index, doc_to_record).status for t in human_null]
    log(f"null: analytic FPR={fpr_analytic:.3f} (target {cfg.alpha}), empirical threshold on -log10 p = {emp_thr:.2f}")

    # attacks
    actx = AttackContext(lm=lm, rng=rng, human_pool=human_pool, context_window=wm_cfg.context_window)
    sweep = default_sweep(quick=cfg.quick, include_llm=cfg.include_llm_attacks and cfg.backend == "hf")
    rows = []
    roc_data = {}
    for spec in sweep:
        log(f"attack: {spec.name}")
        res_hard, res_naive, recall, verdicts, changed, loc_iou, loc_found = [], [], [], [], [], [], []
        for i, t in enumerate(wm_texts):
            try:
                ar = spec.run(t, actx)
            except NotImplementedError:
                ar = None
                break
            res_hard.append(hardened.detect(ar.text))
            res_naive.append(naive.detect(ar.text))
            hits = index.query(ar.text, k=1)
            recall.append(bool(hits and hits[0].doc_id == f"wm-{i}"))
            verdicts.append(registry.verify(ar.text, index, doc_to_record).status)
            changed.append(token_diff(t, ar.text)[1])
            if "span_tokens" in ar.meta:
                truth = tuple(ar.meta["span_tokens"])
                loc = hardened.localize(ar.text, window=min(120, max(40, cfg.n_tokens // 3)), stride=20)
                best = max((iou(truth, (s.token_start, s.token_end)) for s in loc.segments), default=0.0)
                loc_iou.append(best)
                loc_found.append(best > 0.3)
        if ar is None:
            continue
        pos_scores = [r.neg_log10_p for r in res_hard]
        row = {
            "attack": spec.name,
            "tier": str(spec.params.get("tier", "")),
            "n": len(res_hard),
            "tokens_changed_frac": float(np.mean(changed)),
            "tpr_analytic_hardened": float(np.mean([r.detected for r in res_hard])),
            "tpr_analytic_naive": float(np.mean([r.detected for r in res_naive])),
            "tpr_empirical_hardened": float(np.mean([s >= emp_thr for s in pos_scores])),
            "auc_hardened": auc_score(pos_scores, null_scores),
            "mean_z_hardened": float(np.mean([r.z_score for r in res_hard])),
            "mean_z_naive": float(np.mean([r.z_score for r in res_naive])),
            "mean_scored_tokens": float(np.mean([r.n_scored for r in res_hard])),
            "retrieval_recall_at_1": float(np.mean(recall)),
            "registry_exact": verdicts.count("exact") / len(verdicts),
            "registry_tampered": verdicts.count("tampered") / len(verdicts),
            "registry_unknown": verdicts.count("unknown") / len(verdicts),
            "stack_attributed": float(np.mean([d.detected or r or v != "unknown" for d, r, v in zip(res_hard, recall, verdicts)])),
        }
        if loc_iou:
            row["localization_mean_iou"] = float(np.mean(loc_iou))
            row["localization_found_rate"] = float(np.mean(loc_found))
        row["scores_neglog10p"] = [float(x) for x in pos_scores]
        rows.append(row)
        roc_data[spec.name] = pos_scores

    report = {
        "config": {**{k: v for k, v in cfg.__dict__.items() if k != "watermark"}, "watermark": wm_cfg.as_dict()},
        "embedder": getattr(embedder, "name", "?"),
        "generation": {
            "mean_beta_achieved": float(np.mean(beta_hist)) if beta_hist else 0.0,
            "beta_requested": wm_cfg.beta,
            "masked_context_fraction": masked_frac,
            "distinct_outputs_same_prompt_same_key": distinct_rate,
        },
        "null": {
            "n": len(null_texts),
            "fpr_analytic_hardened": fpr_analytic,
            "fpr_analytic_naive": fpr_naive,
            "empirical_threshold_neglog10p": emp_thr,
            "mean_scored_tokens": null_tokens_mean,
            "scores_neglog10p": [float(x) for x in null_scores],
            "registry_verdicts_on_human": {s: registry_on_human.count(s) / len(registry_on_human) for s in ("exact", "tampered", "unknown")},
        },
        "attacks": rows,
        "runtime_seconds": time.time() - t0,
        "samples": {"watermarked_example": wm_texts[0][:600], "unwatermarked_example": plain_texts[0][:600]},
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    write_markdown(report, out_dir / "report.md")
    try:
        write_plots(roc_data, null_scores, rows, out_dir)
    except Exception as e:  # plotting is best-effort
        log(f"plotting skipped: {e}")
    log(f"done in {report['runtime_seconds']:.1f}s -> {out_dir / 'report.md'}")
    return report


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #

def write_markdown(report: dict, path: Path) -> None:
    g, n, c = report["generation"], report["null"], report["config"]
    lines = [
        "# textgrain-ref evaluation report",
        "",
        f"- backend: `{c['backend']}` model: `{c.get('model') or 'toy n-gram'}` embedder: `{report['embedder']}`",
        f"- samples: {c['n_samples']} x {c['n_tokens']} tokens, alpha = {c['alpha']}, seed = {c['seed']}",
        f"- watermark: `{json.dumps(c['watermark'])}`",
        "",
        "## Generation",
        "",
        f"- entropy budget requested {g['beta_requested']:.2f}, achieved {g['mean_beta_achieved']:.3f} (mean over positions)",
        f"- masked (repeated-context) positions: {g['masked_context_fraction']:.3%}",
        f"- distinct outputs for same prompt + same key: {g['distinct_outputs_same_prompt_same_key']:.0%} (Gumbel-max would be 0%)",
        "",
        "## Null calibration (human text + unwatermarked model text)",
        "",
        f"- analytic FPR, hardened detector: {n['fpr_analytic_hardened']:.3f} (target {c['alpha']}); naive: {n['fpr_analytic_naive']:.3f}",
        f"- empirical threshold on -log10(p) at FPR {c['alpha']}: {n['empirical_threshold_neglog10p']:.2f}",
        f"- registry verdicts on human text: {n['registry_verdicts_on_human']}",
        "",
        "## Attacks",
        "",
        "| attack | tokens changed | TPR naive | TPR hardened | TPR (emp. thr) | AUC | mean z naive | mean z hardened | retrieval R@1 | registry exact/tampered/unknown | stack attributed | localization |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report["attacks"]:
        loc = ""
        if "localization_mean_iou" in r:
            loc = f"IoU {r['localization_mean_iou']:.2f}, found {r['localization_found_rate']:.0%}"
        lines.append(
            f"| {r['attack']} | {r['tokens_changed_frac']:.0%} | {r['tpr_analytic_naive']:.0%} | {r['tpr_analytic_hardened']:.0%} | "
            f"{r['tpr_empirical_hardened']:.0%} | {r['auc_hardened']:.3f} | {r['mean_z_naive']:.1f} | {r['mean_z_hardened']:.1f} | {r['retrieval_recall_at_1']:.0%} | "
            f"{r['registry_exact']:.0%} / {r['registry_tampered']:.0%} / {r['registry_unknown']:.0%} | {r['stack_attributed']:.0%} | {loc} |"
        )
    lines += [
        "",
        "Columns: *TPR naive* = detector without canonicalisation; *TPR hardened* = with canonicalisation; "
        "*stack attributed* = watermark detected OR retrieval found the source OR registry returned exact/tampered.",
        "",
        "## Sample",
        "",
        "Watermarked:",
        "",
        "> " + report["samples"]["watermarked_example"].replace("\n", " "),
        "",
        f"Runtime: {report['runtime_seconds']:.1f}s",
        "",
    ]
    path.write_text("\n".join(lines))


def write_plots(roc_data: dict, null_scores: list, rows: list, out_dir: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if roc_data:
        _write_roc(roc_data, null_scores, out_dir, plt)
    _write_bars(rows, out_dir, plt)


def _write_roc(roc_data: dict, null_scores: list, out_dir: Path, plt) -> None:
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    for name, pos in roc_data.items():
        fpr, tpr = roc_curve(pos, null_scores)
        ax.plot(fpr, tpr, label=f"{name} (AUC {auc_score(pos, null_scores):.2f})", lw=1.4)
    ax.plot([0, 1], [0, 1], "k--", lw=0.8)
    ax.set_xlabel("false positive rate (human + unwatermarked model text)")
    ax.set_ylabel("true positive rate (attacked watermarked text)")
    ax.set_title("Hardened detector ROC per attack")
    ax.legend(fontsize=7, loc="lower right")
    fig.tight_layout()
    fig.savefig(out_dir / "roc.png", dpi=150)
    plt.close(fig)


def _write_bars(rows: list, out_dir: Path, plt) -> None:
    names = [r["attack"] for r in rows]
    y = np.arange(len(names))
    fig, ax = plt.subplots(figsize=(9, 0.42 * len(names) + 1.8))
    ax.barh(y + 0.22, [r["tpr_analytic_naive"] for r in rows], height=0.2, label="watermark, naive detector")
    ax.barh(y, [r["tpr_analytic_hardened"] for r in rows], height=0.2, label="watermark, hardened detector")
    ax.barh(y - 0.22, [r["retrieval_recall_at_1"] for r in rows], height=0.2, label="retrieval recall@1")
    ax.barh(y - 0.44, [r["stack_attributed"] for r in rows], height=0.2, label="full stack attributed")
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.02)
    ax.set_xlabel("rate")
    ax.set_title("Which layer still identifies the source after each attack")
    ax.legend(fontsize=7, loc="upper center", bbox_to_anchor=(0.5, -0.08), ncol=4, frameon=False)
    fig.tight_layout()
    fig.savefig(out_dir / "tpr.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 0.38 * len(names) + 1.8))
    ax.barh(y + 0.17, [r["mean_z_naive"] for r in rows], height=0.3, label="naive detector (no canonicalisation)")
    ax.barh(y - 0.17, [r["mean_z_hardened"] for r in rows], height=0.3, label="hardened detector")
    ax.axvline(2.33, color="k", ls="--", lw=0.8, label="z at alpha = 0.01")
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("mean detection z-score  (S_n - n) / sqrt(n)")
    ax.set_title("Watermark signal strength after each attack")
    ax.legend(fontsize=7, loc="upper center", bbox_to_anchor=(0.5, -0.08), ncol=3, frameon=False)
    fig.tight_layout()
    fig.savefig(out_dir / "zscore.png", dpi=150)
    plt.close(fig)
