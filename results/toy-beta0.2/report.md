# textgrain-ref evaluation report

- backend: `toy` model: `toy n-gram` embedder: `hashing-char-ngram`
- samples: 30 x 300 tokens, alpha = 0.01, seed = 7
- watermark: `{"n_blocks": 32, "n_columns": 16, "beta": 0.2, "context_window": 3, "mask_repeated_contexts": true, "ot_iterations": 40, "ot_tolerance": 0.02}`

## Generation

- entropy budget requested 0.20, achieved 0.188 (mean over positions)
- masked (repeated-context) positions: 1.000%
- distinct outputs for same prompt + same key: 100% (Gumbel-max would be 0%)

## Null calibration (human text + unwatermarked model text)

- analytic FPR, hardened detector: 0.017 (target 0.01); naive: 0.017
- empirical threshold on -log10(p) at FPR 0.01: 3.14
- registry verdicts on human text: {'exact': 0.0, 'tampered': 0.0, 'unknown': 1.0}

## Attacks

| attack | tokens changed | TPR naive | TPR hardened | TPR (emp. thr) | AUC | mean z naive | mean z hardened | retrieval R@1 | registry exact/tampered/unknown | stack attributed | localization |
|---|---|---|---|---|---|---|---|---|---|---|---|
| identity (copy-paste) | 0% | 100% | 100% | 100% | 1.000 | 19.7 | 19.7 | 100% | 100% / 0% / 0% | 100% |  |
| word_autoformat | 0% | 100% | 100% | 100% | 1.000 | 17.5 | 19.7 | 100% | 100% / 0% / 0% | 100% |  |
| zero_width p=0.5 | 0% | 83% | 100% | 100% | 1.000 | 3.8 | 19.7 | 100% | 100% / 0% / 0% | 100% |  |
| homoglyph p=0.3 | 0% | 100% | 100% | 100% | 1.000 | 6.3 | 19.7 | 100% | 100% / 0% / 0% | 100% |  |
| rewrite p=0.10 | 16% | 100% | 100% | 100% | 1.000 | 12.8 | 12.8 | 100% | 0% / 100% / 0% | 100% |  |
| rewrite p=0.25 | 34% | 100% | 100% | 97% | 0.999 | 6.4 | 6.4 | 100% | 0% / 100% / 0% | 100% |  |
| rewrite p=0.50 | 73% | 17% | 17% | 0% | 0.761 | 1.1 | 1.1 | 100% | 0% / 0% / 100% | 100% |  |
| delete p=0.10 | 15% | 100% | 100% | 100% | 1.000 | 13.5 | 13.5 | 100% | 0% / 100% / 0% | 100% |  |
| insert p=0.10 | 13% | 100% | 100% | 100% | 1.000 | 13.7 | 13.7 | 100% | 0% / 100% / 0% | 100% |  |
| truncate 150 tok | 50% | 100% | 100% | 100% | 1.000 | 14.0 | 14.0 | 100% | 0% / 100% / 0% | 100% |  |
| truncate 80 tok | 73% | 100% | 100% | 100% | 1.000 | 10.0 | 10.0 | 100% | 0% / 100% / 0% | 100% |  |
| dilute 3x human | 54% | 100% | 100% | 100% | 1.000 | 13.1 | 13.1 | 100% | 0% / 100% / 0% | 100% | IoU 0.77, found 100% |
| piggyback k=3 (spoof) | 1% | 100% | 100% | 100% | 1.000 | 18.8 | 18.8 | 100% | 0% / 100% / 0% | 100% |  |

Columns: *TPR naive* = detector without canonicalisation; *TPR hardened* = with canonicalisation; *stack attributed* = watermark detected OR retrieval found the source OR registry returned exact/tampered.

## Sample

Watermarked:

> different have been, because I would take them back again with it. She found, however, that she had always been endeavouring for But if he had come to any such misuse, when you found herself, as usual Very true, " actually Captain Wentworth know how?" " Oh! You will There is such of be, and raising men to sea true, my dear, you know him the executor, on hearing of us think, that they had talked - a Miss Woodhouse, gave him a look forward to dissuade You will stay; and that if you please, my dear papa, who would have done breakfast, of all, and altogether there was no doubt of his life had been

Runtime: 55.0s
