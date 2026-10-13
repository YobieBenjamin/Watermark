# textgrain-ref evaluation report

- backend: `hf` model: `Qwen/Qwen2.5-1.5B-Instruct` embedder: `paraphrase-multilingual-MiniLM-L12-v2`
- samples: 40 x 400 tokens, alpha = 0.01, seed = 7
- watermark: `{"n_blocks": 32, "n_columns": 16, "beta": 0.5, "context_window": 3, "mask_repeated_contexts": true, "ot_iterations": 40, "ot_tolerance": 0.02}`

## Generation

- entropy budget requested 0.50, achieved 0.450 (mean over positions)
- masked (repeated-context) positions: 4.219%
- distinct outputs for same prompt + same key: 100% (Gumbel-max would be 0%)

## Null calibration (human text + unwatermarked model text)

- analytic FPR, hardened detector: 0.000 (target 0.01); naive: 0.000
- empirical threshold on -log10(p) at FPR 0.01: 1.42
- registry verdicts on human text: {'exact': 0.0, 'tampered': 0.0, 'unknown': 1.0}

## Attacks

| attack | tokens changed | TPR naive | TPR hardened | TPR (emp. thr) | AUC | mean z naive | mean z hardened | retrieval R@1 | registry exact/tampered/unknown | stack attributed | localization |
|---|---|---|---|---|---|---|---|---|---|---|---|
| identity (copy-paste) | 0% | 98% | 98% | 98% | 0.993 | 13.0 | 12.3 | 100% | 100% / 0% / 0% | 100% |  |
| word_autoformat | 0% | 98% | 98% | 98% | 0.993 | 12.4 | 12.3 | 100% | 100% / 0% / 0% | 100% |  |
| zero_width p=0.5 | 0% | 40% | 98% | 98% | 0.993 | 1.8 | 12.3 | 100% | 100% / 0% / 0% | 100% |  |
| homoglyph p=0.3 | 0% | 65% | 98% | 98% | 0.993 | 3.2 | 12.3 | 100% | 100% / 0% / 0% | 100% |  |
| rewrite p=0.10 | 11% | 98% | 98% | 98% | 0.989 | 8.6 | 8.1 | 100% | 0% / 100% / 0% | 100% |  |
| rewrite p=0.25 | 31% | 85% | 85% | 85% | 0.957 | 4.2 | 4.1 | 100% | 0% / 100% / 0% | 100% |  |
| rewrite p=0.50 | 70% | 5% | 5% | 12% | 0.641 | 0.8 | 0.6 | 98% | 0% / 100% / 0% | 100% |  |
| delete p=0.10 | 14% | 98% | 98% | 98% | 0.987 | 8.6 | 8.2 | 100% | 0% / 100% / 0% | 100% |  |
| insert p=0.10 | 11% | 98% | 98% | 98% | 0.979 | 8.8 | 8.3 | 100% | 0% / 100% / 0% | 100% |  |
| truncate 150 tok | 48% | 95% | 95% | 98% | 0.993 | 7.8 | 7.4 | 100% | 10% / 90% / 0% | 100% |  |
| truncate 80 tok | 70% | 88% | 85% | 95% | 0.990 | 5.0 | 4.9 | 100% | 2% / 98% / 0% | 100% |  |
| dilute 3x human | 55% | 95% | 95% | 98% | 0.987 | 8.6 | 8.1 | 95% | 0% / 95% / 5% | 98% | IoU 0.73, found 95% |
| piggyback k=3 (spoof) | 2% | 98% | 98% | 98% | 0.997 | 12.5 | 11.8 | 100% | 0% / 100% / 0% | 100% |  |
| paraphrase_llm | 53% | 60% | 57% | 65% | 0.927 | 3.7 | 3.6 | 100% | 0% / 100% / 0% | 100% |  |
| translate_roundtrip fr | 44% | 72% | 70% | 78% | 0.932 | 3.5 | 3.3 | 100% | 2% / 98% / 0% | 100% |  |

Columns: *TPR naive* = detector without canonicalisation; *TPR hardened* = with canonicalisation; *stack attributed* = watermark detected OR retrieval found the source OR registry returned exact/tampered.

## Sample

Watermarked:

> When a person uses a credit card to make a purchase, the payment flow goes through several steps. Here is a step-by-step explanation of how a bank clears a card payment:  1. Cardholder initiates the transaction: The first step is for the cardholder to make a payment by swiping their card at a merchant's point-of-sale (POS) terminal, or by swiping their card after entering in the PIN.  2. Cardholder verifies the merchant: Once the payment is made at the POS, the cardholder will be prompted to confirm the transaction with the merchant. If everything is correct, the merchant will swipe the credit

Runtime: 2689.4s
