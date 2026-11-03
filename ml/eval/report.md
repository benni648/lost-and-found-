# Evaluation report

Corpus: synthetic campus corpus, 189 ground-truth queries. Scorer: `rerank:p4260-eead14d5`. Alert threshold: 0.55.

## Ranking

| Metric | Value | 95% CI |
|---|---|---|
| Recall@1 | 0.8571 | [0.8067, 0.9101] |
| Recall@5 | 1.0 | [1.0, 1.0] |
| Recall@10 | 1.0 | [1.0, 1.0] |
| MRR | 0.9145 | [0.8858, 0.9471] |
| nDCG@10 | 0.9361 | [0.9146, 0.9606] |

## Decision quality

| Metric | Value | 95% CI |
|---|---|---|
| Precision@alert | 0.95 | [0.9105, 0.9841] |
| Coverage | 0.6349 | — |
| Abstain rate | 0.3651 | — |
| Lookalike beats truth | 0.0582 | — |

## Calibration

| Metric | Value |
|---|---|
| ECE | 0.0138 |
| Brier | 0.0374 |

Plan target: ECE < 0.05 on held-out data. Fit a calibration with `python -m lostfound.cli calibrate` and re-run to measure the calibrated value.

## Per category

| Category | Queries | Recall@10 | Precision@alert | Coverage |
|---|---|---|---|---|
| backpack | 22 | 1.0 | 0.9412 | 0.7727 |
| bottle | 26 | 1.0 | 0.9565 | 0.8846 |
| calculator | 4 | 1.0 | 1.0 | 0.25 |
| charger | 5 | 1.0 | 1.0 | 0.6 |
| earbuds | 5 | 1.0 | 1.0 | 0.4 |
| glasses | 6 | 1.0 | 1.0 | 0.5 |
| hoodie | 10 | 1.0 | 0.8333 | 0.6 |
| id_card | 18 | 1.0 | 0.7778 | 0.5 |
| keys | 10 | 1.0 | 1.0 | 0.6 |
| laptop | 7 | 1.0 | 1.0 | 0.4286 |
| lunchbox | 5 | 1.0 | 1.0 | 0.4 |
| notebook | 5 | 1.0 | 1.0 | 0.6 |
| pendrive | 4 | 1.0 | 1.0 | 0.75 |
| phone | 22 | 1.0 | 0.9333 | 0.6818 |
| umbrella | 10 | 1.0 | 1.0 | 0.6 |
| wallet | 23 | 1.0 | 1.0 | 0.5217 |
| watch | 7 | 1.0 | 1.0 | 0.8571 |

## Alert budget

Precision when only the most confident cases are allowed to alert.

| Coverage | Alerts | Precision |
|---|---|---|
| 0.101 | 18 | 1.0 |
| 0.201 | 37 | 1.0 |
| 0.302 | 56 | 0.9821 |
| 0.402 | 73 | 0.9589 |
| 0.497 | 90 | 0.9667 |
| 0.598 | 108 | 0.9444 |
| 0.698 | 120 | 0.95 |
| 0.799 | 120 | 0.95 |
| 0.899 | 120 | 0.95 |

Accuracy claims must always carry the corpus, query count and spread (plan §2).
