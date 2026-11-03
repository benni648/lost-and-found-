"""Evaluation harness.

One row per run, appended to ``ml/eval/results.csv`` (stable legacy schema) and
a full metrics row appended to ``ml/eval/metrics.jsonl``. The metrics answer the
questions a reviewer will actually ask:

* Recall@k / MRR / nDCG   -- can it find the right item at all?
* precision @ alert       -- when it says "match", how often is it right?
* coverage                -- how many real cases get an alert at all?
* distractor rate         -- how often does a same-category, same-colour
                             *wrong* item outrank the truth?
* ECE / Brier             -- is the confidence itself trustworthy?
* bootstrap CI            -- how much of that is noise on ~190 queries?
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np

from . import calibration
from .config import EVAL_DIR, PROB_ALERT
from .matching import Matcher
from .rerank import POSE_SCORER
from .settings import settings
from .store import truth_pairs

# The committed CSV schema is append-only; new metrics go to metrics.jsonl so
# historical rows keep parsing.
CSV_FIELDS = [
    "queries",
    "recall@1",
    "recall@5",
    "recall@10",
    "mrr",
    "ndcg@10",
    "alerts",
    "precision@alert",
    "coverage",
    "abstain_rate",
    "distractor_rate",
    "median_rank",
]
METRICS_JSONL = "metrics.jsonl"
ALERT_CURVE_TARGETS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
RANKING_ROWS = (
    ("recall@1", "Recall@1"),
    ("recall@5", "Recall@5"),
    ("recall@10", "Recall@10"),
    ("mrr", "MRR"),
    ("ndcg@10", "nDCG@10"),
)
DECISION_ROWS = (
    ("precision@alert", "Precision@alert"),
    ("coverage", "Coverage"),
    ("abstain_rate", "Abstain rate"),
    ("distractor_rate", "Lookalike beats truth"),
)


def _ndcg_at_k(rank: int | None, k: int) -> float:
    if rank is None or rank > k:
        return 0.0
    return 1.0 / math.log2(rank + 1)


def _decide(top_prob: float, margin: float, alert_prob: float) -> str:
    if top_prob < settings.plausible_floor:
        return "no_match"
    if top_prob < alert_prob or margin < settings.margin_min:
        return "review"
    return "match"


def run_eval(
    matcher: Matcher,
    ks=(1, 5, 10),
    alert_prob: float = PROB_ALERT,
    save_csv: bool = True,
    bootstrap: int = 500,
    seed: int = 0,
) -> dict:
    truth = truth_pairs(matcher.con, campus_id=matcher.campus_id)
    lost = [r for r in matcher.lost if r.id in truth]
    if not lost:
        raise RuntimeError("no ground-truth pairs in this database; run `seed` first")

    hits = dict.fromkeys(ks, 0)
    reciprocal = 0.0
    ndcg = 0.0
    ranks: list[int | None] = []
    alerts = correct_alerts = 0
    distractors = 0
    abstained = 0
    scorer = POSE_SCORER
    per_query: list[dict] = []
    cal_probs: list[float] = []
    cal_labels: list[int] = []

    for query in lost:
        ranked = matcher.rank(query, top_k=10_000)
        if ranked:
            scorer = ranked[0].scorer
        target = truth[query.id]
        rank = next((i + 1 for i, r in enumerate(ranked) if r.candidate.id == target), None)
        ranks.append(rank)
        for k in ks:
            if rank is not None and rank <= k:
                hits[k] += 1
        if rank is not None:
            reciprocal += 1.0 / rank
        ndcg += _ndcg_at_k(rank, 10)

        # operating point: would we have alerted, and on the right item?
        top_prob = ranked[0].prob if ranked else 0.0
        margin = (ranked[0].prob - ranked[1].prob) if len(ranked) > 1 else top_prob
        decision = _decide(top_prob, margin, alert_prob)
        alerted = decision == "match"
        correct = bool(ranked) and ranked[0].candidate.id == target
        if alerted:
            alerts += 1
            if correct:
                correct_alerts += 1
        else:
            abstained += 1
        per_query.append(
            {
                "query_id": query.id,
                "category": query.category or "?",
                "rank": rank,
                "ndcg": _ndcg_at_k(rank, 10),
                "alerted": alerted,
                "correct_alert": alerted and correct,
                "prob": top_prob,
                "decision": decision,
            }
        )

        # the confidence itself is measured over the shortlist the user sees
        for r in ranked[:10]:
            cal_probs.append(r.prob)
            cal_labels.append(1 if r.candidate.id == target else 0)

        # would a lookalike have beaten the truth?
        if rank is None or rank > 1:
            q_attrs = query.attrs or {}
            for r in ranked[: rank - 1 if rank else 10]:
                c_attrs = r.candidate.attrs or {}
                if (
                    r.candidate.category == query.category
                    and c_attrs.get("primary_color") == q_attrs.get("primary_color")
                    and r.candidate.id != target
                ):
                    distractors += 1
                    break

    n = len(lost)
    cal = calibration.calibration_metrics(cal_probs, cal_labels)
    results = {
        "queries": n,
        **{f"recall@{k}": round(hits[k] / n, 4) for k in ks},
        "mrr": round(reciprocal / n, 4),
        "ndcg@10": round(ndcg / n, 4),
        "alerts": alerts,
        "precision@alert": round(correct_alerts / alerts, 4) if alerts else 0.0,
        "coverage": round(alerts / n, 4),
        "abstain_rate": round(abstained / n, 4),
        "distractor_rate": round(distractors / n, 4),
        "median_rank": _median([r for r in ranks if r is not None]),
        "ece": cal["ece"],
        "brier": cal["brier"],
        "scorer": scorer,
        "alert_prob": alert_prob,
        "by_category": _by_category(per_query),
        "alert_curve": _alert_curve(per_query),
        "ci95": _bootstrap(per_query, ks, bootstrap, seed) if bootstrap else {},
    }

    if save_csv:
        _append_row(results)
        _append_metrics(results)
    return results


def _by_category(per_query: list[dict]) -> dict:
    groups: dict[str, list[dict]] = {}
    for row in per_query:
        groups.setdefault(row["category"], []).append(row)
    out = {}
    for category, rows in sorted(groups.items()):
        n = len(rows)
        alerted = [r for r in rows if r["alerted"]]
        out[category] = {
            "queries": n,
            "recall@10": round(sum(1 for r in rows if r["rank"] is not None and r["rank"] <= 10) / n, 4),
            "precision@alert": (
                round(sum(1 for r in alerted if r["correct_alert"]) / len(alerted), 4) if alerted else 0.0
            ),
            "coverage": round(len(alerted) / n, 4),
        }
    return out


def _alert_curve(per_query: list[dict]) -> list[dict]:
    """Precision if we only alerted on the most confident x% of cases."""
    ranked = sorted(per_query, key=lambda r: -r["prob"])
    total = len(ranked)
    curve = []
    for target in ALERT_CURVE_TARGETS:
        k = max(1, int(round(total * target)))
        window = ranked[:k]
        alerted = [r for r in window if r["alerted"]]
        curve.append(
            {
                "coverage": round(k / total, 3),
                "precision": round(sum(1 for r in alerted if r["correct_alert"]) / len(alerted), 4) if alerted else 0.0,
                "alerts": len(alerted),
            }
        )
    return curve


def _bootstrap(per_query: list[dict], ks, iterations: int, seed: int) -> dict:
    """Percentile CIs, resampling queries: the honest error bar on ~190 cases."""
    rng = np.random.default_rng(seed)
    n = len(per_query)
    samples: dict[str, list[float]] = {f"recall@{k}": [] for k in ks}
    for key in ("mrr", "ndcg@10", "precision@alert"):
        samples[key] = []
    for _ in range(iterations):
        idx = rng.integers(0, n, n)
        rows = [per_query[i] for i in idx]
        for k in ks:
            samples[f"recall@{k}"].append(sum(1 for r in rows if r["rank"] is not None and r["rank"] <= k) / n)
        samples["mrr"].append(sum(1.0 / r["rank"] for r in rows if r["rank"] is not None) / n)
        samples["ndcg@10"].append(sum(r["ndcg"] for r in rows) / n)
        alerted = [r for r in rows if r["alerted"]]
        samples["precision@alert"].append(
            sum(1 for r in alerted if r["correct_alert"]) / len(alerted) if alerted else np.nan
        )
    out = {}
    for key, values in samples.items():
        arr = np.asarray(values, dtype=np.float64)
        arr = arr[~np.isnan(arr)]
        if not arr.size:
            # Undefined on every resample (e.g. no alert fired anywhere): omit
            # the interval instead of emitting one the formatters cannot unpack.
            continue
        out[key] = [round(float(np.percentile(arr, 2.5)), 4), round(float(np.percentile(arr, 97.5)), 4)]
    return out


def run_multi_seed(seeds: list[int], n_items: int, save_csv: bool = False) -> dict:
    """Average the metrics over several independently seeded corpora.

    A single synthetic corpus is one draw. Reporting the mean over seeds (and
    the spread) is the difference between a number and a measurement.
    """
    import tempfile

    from .pipeline import build_database
    from .store import connect

    runs = []
    for seed in seeds:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / f"eval-{seed}.db"
            build_database(db_path=db, n_items=n_items, seed=seed, render_images=False, verbose=False)
            con = connect(db)
            runs.append(run_eval(Matcher.load(con), save_csv=False, bootstrap=200, seed=seed))
            con.close()

    keys = [k for k in runs[0] if isinstance(runs[0][k], (float, int))]
    mean = {k: round(sum(r[k] for r in runs) / len(runs), 4) for k in keys}
    spread = {k: round(max(r[k] for r in runs) - min(r[k] for r in runs), 4) for k in keys}
    return {"runs": runs, "mean": mean, "spread": spread, "seeds": seeds}


def _median(values: list[int]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    return float(s[mid]) if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _append_row(results: dict) -> Path:
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    path = EVAL_DIR / "results.csv"
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow({key: results.get(key) for key in CSV_FIELDS})
    return path


def _append_metrics(results: dict) -> Path:
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    path = EVAL_DIR / METRICS_JSONL
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(results, sort_keys=True) + "\n")
    return path


def latest_results() -> dict | None:
    """Most recent full metrics row, for the trust endpoint."""
    path = EVAL_DIR / METRICS_JSONL
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return None
    try:
        return json.loads(lines[-1])
    except (ValueError, IndexError):
        return None


def format_results(results: dict) -> str:
    order = [
        ("queries", "queries"),
        ("recall@1", "Recall@1"),
        ("recall@5", "Recall@5"),
        ("recall@10", "Recall@10"),
        ("mrr", "MRR"),
        ("ndcg@10", "nDCG@10"),
        ("precision@alert", "Precision @ alert"),
        ("coverage", "Coverage"),
        ("abstain_rate", "Abstain rate"),
        ("distractor_rate", "Lookalike beats truth"),
        ("ece", "ECE (confidence error)"),
        ("brier", "Brier score"),
        ("median_rank", "Median rank of truth"),
    ]
    width = max(len(label) for _, label in order)
    lines = []
    for key, label in order:
        if key not in results:
            continue
        lines.append(f"  {label:<{width}}  {results.get(key)}")
    for key, interval in results.get("ci95", {}).items():
        if len(interval) != 2:
            continue
        low, high = interval
        lines.append(f"  {('95% CI ' + key):<{width}}  [{low}, {high}]")
    return "\n".join(lines)


def format_report_markdown(results: dict) -> str:
    lines = [
        "# Evaluation report",
        "",
        f"Corpus: synthetic campus corpus, {results['queries']} ground-truth queries. "
        f"Scorer: `{results.get('scorer', POSE_SCORER)}`. Alert threshold: {results.get('alert_prob')}.",
        "",
        "## Ranking",
        "",
        "| Metric | Value | 95% CI |",
        "|---|---|---|",
    ]
    for key, label in RANKING_ROWS:
        ci = results.get("ci95", {}).get(key)
        lines.append(f"| {label} | {results.get(key)} | {ci if ci else '—'} |")
    lines += [
        "",
        "## Decision quality",
        "",
        "| Metric | Value | 95% CI |",
        "|---|---|---|",
    ]
    for key, label in DECISION_ROWS:
        ci = results.get("ci95", {}).get(key)
        lines.append(f"| {label} | {results.get(key)} | {ci if ci else '—'} |")
    lines += [
        "",
        "## Calibration",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| ECE | {results.get('ece')} |",
        f"| Brier | {results.get('brier')} |",
        "",
        "Plan target: ECE < 0.05 on held-out data. Fit a calibration with "
        "`python -m lostfound.cli calibrate` and re-run to measure the calibrated value.",
        "",
        "## Per category",
        "",
        "| Category | Queries | Recall@10 | Precision@alert | Coverage |",
        "|---|---|---|---|---|",
    ]
    for category, row in results.get("by_category", {}).items():
        lines.append(
            f"| {category} | {row['queries']} | {row['recall@10']} | {row['precision@alert']} | {row['coverage']} |"
        )
    lines += [
        "",
        "## Alert budget",
        "",
        "Precision when only the most confident cases are allowed to alert.",
        "",
        "| Coverage | Alerts | Precision |",
        "|---|---|---|",
    ]
    for row in results.get("alert_curve", []):
        lines.append(f"| {row['coverage']} | {row['alerts']} | {row['precision']} |")
    lines += ["", "Accuracy claims must always carry the corpus, query count and spread (plan §2).", ""]
    return "\n".join(lines)


__all__ = [
    "CSV_FIELDS",
    "format_report_markdown",
    "format_results",
    "latest_results",
    "run_eval",
    "run_multi_seed",
]
