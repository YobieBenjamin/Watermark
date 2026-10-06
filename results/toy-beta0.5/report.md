# textgrain-ref evaluation report

- backend: `toy` model: `toy n-gram` embedder: `hashing-char-ngram`
- samples: 30 x 300 tokens, alpha = 0.01, seed = 7
- watermark: `{"n_blocks": 32, "n_columns": 16, "beta": 0.5, "context_window": 3, "mask_repeated_contexts": true, "ot_iterations": 40, "ot_tolerance": 0.02}`

## Generation

- entropy budget requested 0.50, achieved 0.464 (mean over positions)
- masked (repeated-context) positions: 1.333%
- distinct outputs for same prompt + same key: 100% (Gumbel-max would be 0%)

## Null calibration (human text + unwatermarked model text)

- analytic FPR, hardened detector: 0.017 (target 0.01); naive: 0.017
- empirical threshold on -log10(p) at FPR 0.01: 3.14
- registry verdicts on human text: {'exact': 0.0, 'tampered': 0.0, 'unknown': 1.0}

## Attacks

| attack | tokens changed | TPR naive | TPR hardened | TPR (emp. thr) | AUC | mean z naive | mean z hardened | retrieval R@1 | registry exact/tampered/unknown | stack attributed | localization |
|---|---|---|---|---|---|---|---|---|---|---|---|
| identity (copy-paste) | 0% | 100% | 100% | 100% | 1.000 | 26.9 | 26.9 | 100% | 100% / 0% / 0% | 100% |  |
| word_autoformat | 0% | 100% | 100% | 100% | 1.000 | 24.4 | 26.9 | 100% | 100% / 0% / 0% | 100% |  |
| zero_width p=0.5 | 0% | 100% | 100% | 100% | 1.000 | 5.3 | 26.9 | 100% | 100% / 0% / 0% | 100% |  |
| homoglyph p=0.3 | 0% | 100% | 100% | 100% | 1.000 | 8.4 | 26.9 | 100% | 100% / 0% / 0% | 100% |  |
| rewrite p=0.10 | 16% | 100% | 100% | 100% | 1.000 | 17.8 | 17.8 | 100% | 0% / 100% / 0% | 100% |  |
| rewrite p=0.25 | 34% | 100% | 100% | 100% | 1.000 | 8.7 | 8.7 | 100% | 0% / 100% / 0% | 100% |  |
| rewrite p=0.50 | 73% | 20% | 20% | 7% | 0.868 | 1.6 | 1.6 | 100% | 0% / 0% / 100% | 100% |  |
| delete p=0.10 | 15% | 100% | 100% | 100% | 1.000 | 18.6 | 18.6 | 100% | 0% / 100% / 0% | 100% |  |
| insert p=0.10 | 13% | 100% | 100% | 100% | 1.000 | 18.9 | 18.9 | 100% | 0% / 100% / 0% | 100% |  |
| truncate 150 tok | 50% | 100% | 100% | 100% | 1.000 | 19.3 | 19.3 | 100% | 0% / 100% / 0% | 100% |  |
| truncate 80 tok | 73% | 100% | 100% | 100% | 1.000 | 13.8 | 13.8 | 100% | 0% / 100% / 0% | 100% |  |
| dilute 3x human | 54% | 100% | 100% | 100% | 1.000 | 17.9 | 17.9 | 100% | 0% / 100% / 0% | 100% | IoU 0.73, found 100% |
| piggyback k=3 (spoof) | 1% | 100% | 100% | 100% | 1.000 | 25.9 | 25.9 | 100% | 0% / 100% / 0% | 100% |  |

Columns: *TPR naive* = detector without canonicalisation; *TPR hardened* = with canonicalisation; *stack attributed* = watermark detected OR retrieval found the source OR registry returned exact/tampered.

## Sample

Watermarked:

> hurry. ""Oh! Mr. Knightley has any evil was now, whether you are a serious and would have been falling her. Captain Benwick, who was to be permitted to rent. Elizabeth was the village does chuse the other in a moment; rather sooner than all the make to begin abusing he, " but in Highbury, was be a different it was the work of their coming Poor but however, to assure her that he had done well, that I have a great pleasure at Randalls. He gave her attitude She could not often are"Now, Elizabeth to Mr. Suckling was always felt how to allay of a little were the liveliest objects of his own her in 

Runtime: 133.0s
