# Evaluation

Every number here is stated with the corpus, the query count and the spread.
A single synthetic corpus is one draw, so single-run figures carry bootstrap
confidence intervals and multi-seed figures carry the max−min range. No number
in this document is a bare "accuracy" claim.

Reproduce all of it from a fresh checkout with the commands in
[Reproducing](#reproducing).

---

## 1. What is being measured, and what is not

| | |
|---|---|
| Task | rank the true counterpart of a lost report among found reports |
| Corpus | synthetic campus corpus (`lostfound/corpus.py`): 260 items → 220 lost, 229 found, 189 true pairs, 3 simulated weeks, 10% human-error reports |
| Labels | ground-truth pairs (never read by the matcher) plus staff confirm/reject verdicts |
| Photos | deterministic rendered item images (`lostfound/imagery.py`), **not photographs** |
| Text | template-generated descriptions in three registers (terse/detailed/vague) |
| Encoders | classical: hashed n-gram text, HSV/spatial-grid image (no deep model, by decision) |

**This is a simulated corpus.** It measures whether the pipeline, the retrieval
stage, the decision logic and the calibration behave as designed. It does not
claim performance on real photographs or real student writing. The honest
sim-to-real gap is an explicit limitation (§7).

## 2. Retrieval baseline — hand-set weights, 4 seeds

4 independently generated corpora (187.5 queries each on average):

| Metric | Mean | Spread (max−min) |
|---|---|---|
| Recall@1 | 0.6655 | 0.0661 |
| Recall@5 | 0.9390 | 0.0458 |
| Recall@10 | 0.9708 | 0.0203 |
| MRR | 0.7832 | 0.0324 |
| nDCG@10 | 0.8282 | — |
| Precision @ alert | 0.8276 | — |
| Coverage (alert rate) | 0.6148 | — |
| Abstain rate | 0.3852 | — |
| Lookalike beats truth | 0.2533 | — |
| ECE | 0.3659 | — |
| Brier | 0.2287 | — |

Reading: Recall@10 of 0.97 with Recall@1 of 0.67 says **retrieval is doing its
job and ordering is where the gain is** — which is exactly what the learned
re-ranker attacks. The abstain rate of ~39% is the design working: it abstains
rather than guessing. ECE of 0.37 is the raw sigmoid being overconfident, and
that is what calibration exists to fix.

> **Scorer note (regenerated 2026-11-03).** The table above is the hand-set
> baseline. With the retrained re-ranker and its calibration served
> (`cli train-rerank`), the same four seeds measure Recall@1 **0.804**
> (spread 0.096), Recall@10 0.996, MRR 0.884, nDCG@10 0.912,
> precision@alert 0.876 at coverage 0.645, ECE 0.015. The §3 single-corpus
> table is likewise hand-set; with the artifacts served that corpus measures
> Recall@1 0.857 [0.807, 0.910], precision@alert 0.950 [0.911, 0.984],
> ECE 0.014. Delete `ml/reranker/model.json` and
> `ml/calibration/params.json` to reproduce the hand-set rows.

## 3. Single corpus, with bootstrap confidence intervals

Seed `20261026`, 189 queries, 500-resample percentile CI over queries:

| Metric | Value | 95% CI |
|---|---|---|
| Recall@1 | 0.7249 | [0.6586, 0.7831] |
| Recall@5 | 0.9418 | [0.9101, 0.9735] |
| Recall@10 | 0.9788 | [0.9577, 1.0000] |
| MRR | 0.8134 | [0.7661, 0.8558] |
| nDCG@10 | 0.8529 | [0.8152, 0.8873] |
| Precision @ alert | 0.8560 | [0.7842, 0.9145] |
| Coverage | 0.6614 | — |
| ECE | 0.3719 | — |

The CI width is the point: on ~190 queries, Recall@1 moves by ±6 points of pure
sampling noise. Any claim of a two-point improvement needs the spread reported
or it is not measurable.

## 4. Learned re-ranker ablation (holdout by item)

Pairwise logistic (RankNet) re-ranker over the same evidence vector. Split is
**by query id (item)**, so an item cannot appear on both sides:
142 train / 47 holdout queries, 4,260 training pairs.

| Metric (holdout) | Hand-set | Learned (raw score) | Learned + calibrated |
|---|---|---|---|
| Recall@1 | 0.7447 | 0.8511 | **0.8511** |
| Recall@10 | 0.9787 | 1.0000 | **1.0000** |
| MRR | 0.8275 | 0.9167 | **0.9167** |
| nDCG@10 | 0.8633 | 0.9380 | **0.9380** |
| Precision @ alert | 0.8710 | 0.0000 | **0.9000** |
| Coverage (alert rate) | 0.6596 | 0.0000 | 0.2128 |
| ECE | 0.3478 | 0.9000 | **0.0047** |
| Brier | 0.2074 | 0.8999 | **0.0719** |

Three honest observations:

1. **Ordering improves materially** (+10.6 points Recall@1, +8.9 MRR, +7.5
   nDCG@10) on held-out items.
2. **A learned score is not a probability.** Served raw, its threshold never
   fires (coverage 0.0) — the system would silently stop alerting. This is why
   calibration is part of training, not an optional extra.
3. **Calibration trades coverage for correctness.** ECE drops 0.35 → 0.005 and
   precision at alert reaches 0.90, but coverage falls 0.66 → 0.21. The
   hand-set scorer's higher alert rate was *purchased with overconfidence*;
   the calibrated system alerts less and is right when it does. This trade-off
   is a finding, not a regression.

## 5. Calibration alone (hand-set scorer)

Platt and isotonic fitted on 1,420 shortlist rows, evaluated on 470 held-out
rows (split by query):

| Metric | Raw | Calibrated (isotonic) |
|---|---|---|
| ECE | 0.3478 | **0.0167** |
| Brier | 0.2074 | **0.0510** |

Plan target was ECE < 0.05; the fitted map clears it. A calibration is applied
only when it improves holdout ECE, and only to the scorer version it was fitted
for.

## 6. Operating point

Thresholds are not copied across scorers. With a calibrated scorer the alert
threshold is derived from a measured precision budget
(`ALERT_PRECISION_TARGET`, default 0.90) as the lowest threshold whose alerts
still clear it with ≥5% coverage, and the derived value travels with the
calibration version. `GET /api/config/operating-point` and
`GET /api/model/info` report the effective values, so the UI meter and the
decision can never disagree.

## 7. Limitations — state these before a reviewer does

1. **Synthetic corpus and rendered images.** No claim about real photographs.
   A small consented real set is the next step; the sim-to-real delta must be
   measured, not assumed.
2. **Classical encoders by decision.** Hashed n-grams and HSV/spatial grids;
   the DINOv2/bge-m3 adapters and the `vision` extra were removed, so no deep
   model is loaded or trained. `ml/eval/real-image-baseline.json` bounds what
   the image descriptor can do on real product pairs (54.4% rank accuracy).
3. **Pointwise/pairwise logistic re-ranker, not LambdaMART.** No LightGBM in
   the runtime here; the interface is built so a LambdaMART swap is a drop-in.
4. **47-query holdout.** The ablation is directionally strong but small; treat
   the exact deltas as indicative, not final.
5. **Staff labels are sparse.** Cold start uses ground-truth pairs; real label
   volume is a Phase 4/5 prerequisite.
6. **Optional OCR; up to four photos per report.** GPS distance is implemented
   and unit-tested (`geo.py`), but the synthetic corpus carries no coordinates,
   so it does not move the numbers in this document. The corpus has a single
   rendered photo per report and no printed text that Tesseract would read, so
   neither the multi-photo pooling path nor OCR is evaluated here; both are
   covered by tests against known inputs instead. The recovery loop and the
   notification outbox are operational features with no effect on ranking, so
   they appear only in `docs/ALIGNMENT.md`.

## What is deliberately not measured

Ranking is measured; the recovery loop is not. Claims, challenge grading and
handover are covered by request-level tests (wrong answers rejected, code stored
only as a hash, attempts capped, both reports `recovered`, notifications
queued once), but there is no measurement of how many real owners can answer
their own questions — that needs a consented user study, not a synthetic corpus.
7. **Load numbers are local TestClient baselines**, not production capacity.

## 8. How to make an accuracy claim here

> "Recall@10 = 0.97 ± 0.02 (4 seeds, 187.5 queries/seed, synthetic corpus);
> Precision@alert = 0.90 with coverage 0.21 on a 47-query item-wise holdout;
> ECE = 0.005 after calibration, down from 0.35."

Never: "98% accurate". Always: metric, corpus, n, spread, and which of
recall / precision / calibration is being claimed, at what alert budget.

## Reproducing

```bash
# baseline, 4 seeds (mean + spread)
.venv/Scripts/python.exe -m lostfound.cli eval --seeds 4 --items 260

# single corpus with CIs and the markdown report
.venv/Scripts/python.exe -m lostfound.cli eval --report

# learned re-ranker ablation (holdout by item)
.venv/Scripts/python.exe -m lostfound.cli train-rerank

# calibration fit + reliability report
.venv/Scripts/python.exe -m lostfound.cli calibrate

# tests
.venv/Scripts/python.exe -m pytest -q
```

Generated reports: `ml/eval/report.md`, `ml/reranker/report.md`,
`ml/calibration/report.md`. Model artifacts
(`ml/reranker/model.json`, `ml/calibration/params.json`) are generated, not
committed; deleting them restores the hand-set fallback.
