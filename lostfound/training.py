"""Training and measurement for the Phase 3 learned models.

Two artefacts come out of here, both versioned and both optional at serve time:

* a **re-ranker** (``lostfound/rerank.py``) fitted on labelled candidate
  groups, and
* a **calibration map** (``lostfound/calibration.py``) fitted on the served
  scorer's raw probabilities.

Everything is split **by query (item)**, never by pair. A pair-level split
would let the same physical item appear on both sides and turn the reported
gain into leakage. The holdout numbers produced here are what the ablation
table in the evaluation report cites, so they have to be honest.

Labels come from two places, in priority order:

1. staff verdicts in ``feedback`` (the real signal), and
2. ground-truth pairs in the synthetic corpus (the cold start the plan
   describes), used only for queries with no staff label.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from . import calibration, encoders, rerank, store
from .features import score_pair
from .settings import settings
from .timeutil import now_utc


@dataclass
class Shortlist:
    """One query and a bounded set of labelled candidates for it."""

    query_id: str
    category: str | None
    items: list[dict]

    @property
    def positives(self) -> int:
        return sum(item["label"] for item in self.items)


def build_shortlists(matcher, *, negatives_per_query: int = 30) -> list[Shortlist]:
    """Score every lost report against the pool, keeping a bounded shortlist.

    Positives are always kept; negatives are the highest-scoring hand-set
    candidates, which is where a ranker can actually make a difference. Keeping
    the easy negatives too would flatter both the baseline and the model.
    """
    truth = store.truth_pairs(matcher.con, campus_id=matcher.campus_id)
    feedback = {
        (row["query_id"], row["candidate_id"]): row["action"]
        for row in store.list_feedback(matcher.con, campus_id=matcher.campus_id)
    }
    out: list[Shortlist] = []
    for query_id, found_id in truth.items():
        query = matcher.by_id.get(query_id)
        if query is None or query.kind != "lost":
            continue
        labelled: list[dict] = []
        for candidate in matcher.found:
            if candidate.id == query.id:
                continue
            if matcher.campus_id is not None and candidate.campus_id != matcher.campus_id:
                continue
            verdict = feedback.get((query_id, candidate.id))
            label = int(verdict == "confirm") if verdict is not None else int(candidate.id == found_id)
            scored = score_pair(query, candidate, matcher.ctx)
            labelled.append(
                {
                    "candidate_id": candidate.id,
                    "label": label,
                    "feats": scored["feats"],
                    "flags": scored["flags"],
                    "evidence_weight": scored["evidence_weight"],
                    "hand_prob": scored["prob"],
                    "hand_score": scored["score"],
                }
            )
        if not labelled:
            continue
        positives = [item for item in labelled if item["label"] == 1]
        negatives = sorted(
            (item for item in labelled if item["label"] == 0),
            key=lambda item: -item["hand_prob"],
        )[:negatives_per_query]
        items = sorted(positives + negatives, key=lambda item: -item["hand_prob"])
        if not positives:
            continue
        out.append(Shortlist(query_id=query_id, category=query.category, items=items))
    return out


def split_shortlists(shortlists: list[Shortlist], *, holdout: float = 0.25, seed: int = 0) -> tuple[list, list]:
    """Split by query id. Every candidate for a query lands on one side."""
    if not 0.0 < holdout < 1.0:
        raise ValueError("holdout must be between 0 and 1")
    if len(shortlists) < 2:
        raise ValueError("need at least two labelled queries to train and evaluate")
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(shortlists))
    cut = int(round(len(shortlists) * holdout))
    cut = min(max(cut, 1), len(shortlists) - 1)
    holdout_idx = set(order[:cut].tolist())
    train = [s for i, s in enumerate(shortlists) if i not in holdout_idx]
    test = [s for i, s in enumerate(shortlists) if i in holdout_idx]
    return train, test


# --------------------------------------------------------------------- metrics
def _decide(top_prob: float, margin: float, thresholds: dict) -> str:
    if top_prob < thresholds["plausible_floor"]:
        return "no_match"
    if top_prob < thresholds["prob_alert"] or margin < thresholds["margin_min"]:
        return "review"
    return "match"


def _thresholds(overrides: dict | None = None) -> dict:
    base = {
        "plausible_floor": settings.plausible_floor,
        "prob_alert": settings.prob_alert,
        "margin_min": settings.margin_min,
    }
    if overrides:
        base.update({key: value for key, value in overrides.items() if value is not None})
    return base


def derive_operating_point(
    shortlists: list[Shortlist],
    score_of,
    *,
    target_precision: float | None = None,
    min_coverage: float = 0.05,
) -> dict:
    """Pick the alert threshold that holds a measured precision budget.

    A calibrated probability is on a different scale from the hand-set
    sigmoid, so inheriting ``PROB_ALERT`` would either mute the system or make
    it overconfident again. The threshold is the lowest one whose alerts still
    clear the target precision with at least ``min_coverage`` of cases.
    """
    target = settings.alert_precision_target if target_precision is None else target_precision
    tops: list[tuple[float, bool]] = []
    for shortlist in shortlists:
        ordered = sorted(shortlist.items, key=lambda item: -score_of(item))
        if ordered:
            tops.append((float(score_of(ordered[0])), ordered[0]["label"] == 1))
    if not tops:
        return {"prob_alert": None, "plausible_floor": None, "margin_min": None, "queries": 0}

    tops.sort(key=lambda row: -row[0])
    n = len(tops)
    base_rate = sum(1 for _, correct in tops if correct) / n
    floor_target = max(0.5, base_rate)
    alert_at = floor_at = None
    alert_metrics: dict = {}
    correct = 0
    for i, (prob, is_true) in enumerate(tops, 1):
        correct += int(is_true)
        coverage = i / n
        precision = correct / i
        if coverage + 1e-9 >= min_coverage and precision >= target:
            alert_at = prob
            alert_metrics = {
                "precision": round(precision, 4),
                "coverage": round(coverage, 4),
            }
        if precision >= floor_target:
            floor_at = prob

    return {
        "prob_alert": None if alert_at is None else round(alert_at, 4),
        "plausible_floor": None if floor_at is None else round(min(floor_at, alert_at or floor_at), 4),
        "margin_min": None,
        "queries": n,
        "target_precision": target,
        "min_coverage": min_coverage,
        **alert_metrics,
    }


def ranking_metrics(
    shortlists: list[Shortlist],
    score_of,
    *,
    calibration_bins: int = 10,
    thresholds: dict | None = None,
    tie_break=None,
) -> dict:
    """Recall@10 / MRR / nDCG@10 / precision@alert / ECE for one scorer.

    ``score_of(item) -> float`` is the probability that scorer assigns a
    candidate. The top-10 rows of each shortlist double as the calibration set,
    which is the population the decision actually sees.
    """
    cut = _thresholds(thresholds)
    ranks: list[int | None] = []
    reciprocal = 0.0
    ndcg = 0.0
    alerts = correct_alerts = 0
    probs: list[float] = []
    labels: list[int] = []
    by_category: dict[str, list[int | None]] = {}

    for shortlist in shortlists:
        # Same tie-break as Matcher.rank: a calibrated probability can saturate,
        # and measuring an order the server would never apply is a silent
        # train/serve skew.
        ordered = sorted(
            shortlist.items,
            key=lambda item: (-score_of(item), -(tie_break(item) if tie_break else 0.0)),
        )
        rank = next((i + 1 for i, item in enumerate(ordered) if item["label"] == 1), None)
        ranks.append(rank)
        by_category.setdefault(shortlist.category or "?", []).append(rank)
        if rank is not None:
            reciprocal += 1.0 / rank
            if rank <= 10:
                ndcg += 1.0 / np.log2(rank + 1)
        if ordered:
            top, second = ordered[0], (ordered[1] if len(ordered) > 1 else None)
            top_prob = float(score_of(top))
            margin = top_prob - (float(score_of(second)) if second is not None else 0.0)
            if _decide(top_prob, margin, cut) == "match":
                alerts += 1
                if top["label"] == 1:
                    correct_alerts += 1
        for item in ordered[:10]:
            probs.append(float(score_of(item)))
            labels.append(int(item["label"]))

    n = len(shortlists)
    cal = calibration.calibration_metrics(probs, labels, calibration_bins)
    return {
        "queries": n,
        "recall@1": round(sum(1 for r in ranks if r == 1) / n, 4),
        "recall@10": round(sum(1 for r in ranks if r is not None and r <= 10) / n, 4),
        "mrr": round(reciprocal / n, 4),
        "ndcg@10": round(ndcg / n, 4),
        "precision@alert": round(correct_alerts / alerts, 4) if alerts else 0.0,
        "coverage": round(alerts / n, 4),
        "ece": cal["ece"],
        "brier": cal["brier"],
        "prob_alert": cut["prob_alert"],
        "by_category": {
            category: round(sum(1 for r in values if r == 1) / len(values), 4)
            for category, values in sorted(by_category.items())
        },
    }


def evaluate_reranker(model: rerank.ReRankModel, shortlists: list[Shortlist]) -> dict:
    """Ablation: hand-set baseline vs the learned scorer, same shortlists."""
    baseline = ranking_metrics(shortlists, lambda item: item["hand_prob"])
    learned = ranking_metrics(
        shortlists,
        lambda item: model.prob(item["feats"], item["flags"], item["evidence_weight"]),
    )
    return {"baseline": baseline, "reranked": learned}


# ------------------------------------------------------------------- training
def _stamp() -> str:
    return now_utc().replace(microsecond=0).isoformat()


def train_reranker(
    matcher,
    *,
    holdout: float = 0.25,
    seed: int = 0,
    epochs: int = 240,
    negatives_per_query: int = 30,
    out=None,
    save: bool = True,
) -> dict:
    shortlists = build_shortlists(matcher, negatives_per_query=negatives_per_query)
    if not shortlists:
        raise RuntimeError("no labelled queries found; run `seed` first or record staff feedback")
    train, test = split_shortlists(shortlists, holdout=holdout, seed=seed)

    groups = [
        [
            (rerank.design(item["feats"], item["flags"], item["evidence_weight"]), int(item["label"]))
            for item in shortlist.items
        ]
        for shortlist in train
    ]
    model = rerank.fit(
        groups,
        epochs=epochs,
        seed=seed,
        encoder=encoders.active_encoders(),
        trained_at=_stamp(),
        metrics={"train_queries": len(train), "holdout_queries": len(test), "holdout_fraction": holdout},
    )
    ablation = evaluate_reranker(model, test)
    train_fit = evaluate_reranker(model, train)

    # A ranker outputs a score, not a probability. Fit the monotone map that
    # turns it into one **on the training queries only**, so the served
    # decision thresholds (prob_alert, margin) keep their meaning. Without
    # this, every learned score lands below the alert threshold and the system
    # silently stops alerting -- the failure this project exists to avoid.
    #
    # The fit is on the raw logit, not the ranker's own sigmoid: a wide logit
    # saturates that sigmoid to all-ones, and fitting the clamped probability
    # then produces a map that answers the same thing for every candidate.
    fit_logits, fit_labels = [], []
    for shortlist in train:
        for item in shortlist.items[:10]:
            vector = rerank.design(item["feats"], item["flags"], item["evidence_weight"])
            fit_logits.append(model.logit(vector))
            fit_labels.append(int(item["label"]))
    a, b = calibration.fit_platt_logit(fit_logits, fit_labels)
    params = calibration.CalibrationParams(
        method="platt",
        version=f"platt-{_stamp()}",
        scorer=model.scorer,
        a=a,
        b=b,
        input_scale="logit",
        fit_size=len(fit_logits),
        holdout_size=sum(len(shortlist.items[:10]) for shortlist in test),
    )

    def calibrated_score(item: dict) -> float:
        vector = rerank.design(item["feats"], item["flags"], item["evidence_weight"])
        return params.apply(model.logit(vector))

    # The decision threshold is measured on the calibrated scale, on the
    # training queries, then frozen into the artifact.
    derived = derive_operating_point(train, calibrated_score)
    params = replace(
        params,
        prob_alert=derived["prob_alert"],
        plausible_floor=derived["plausible_floor"],
        margin_min=derived["margin_min"],
        operating_point=derived,
    )
    raw_score = lambda item: model.prob(item["feats"], item["flags"], item["evidence_weight"])  # noqa: E731
    calibrated = ranking_metrics(test, calibrated_score, thresholds=derived, tie_break=raw_score)
    params = replace(
        params,
        metrics={
            "raw": {"ece": ablation["reranked"]["ece"], "brier": ablation["reranked"]["brier"]},
            "calibrated": {"ece": calibrated["ece"], "brier": calibrated["brier"]},
            "improved_ece": calibrated["ece"] <= ablation["reranked"]["ece"],
        },
    )

    model = replace(
        model,
        metrics={
            **model.metrics,
            "holdout": ablation["reranked"],
            "holdout_calibrated": calibrated,
            "baseline_holdout": ablation["baseline"],
            "train": train_fit["reranked"],
            "calibration": params.metrics,
        },
    )
    path = rerank.save(model, out) if save else None
    # The calibration is scoped to this exact scorer version, so an old fit can
    # never be applied to a new model after a retrain.
    calibration_path = calibration.save(params) if (save and params.metrics["improved_ece"]) else None
    return {
        "model": model,
        "path": path,
        "train_queries": len(train),
        "holdout_queries": len(test),
        "baseline": ablation["baseline"],
        "reranked": ablation["reranked"],
        "reranked_calibrated": calibrated,
        "operating_point": derived,
        "calibration": params,
        "calibration_path": calibration_path,
        "train_metrics": train_fit["reranked"],
    }


def calibrate_scorer(
    matcher,
    *,
    holdout: float = 0.25,
    seed: int = 0,
    method: str = "auto",
    negatives_per_query: int = 30,
    out=None,
    save: bool = True,
    force: bool = False,
) -> dict:
    """Fit a calibration map for whichever scorer is currently served.

    A calibration that does not reduce holdout ECE is reported but **not**
    applied: shipping a worse confidence map because it exists is how a system
    ends up confidently wrong.
    """
    learned = rerank.load()
    if learned is not None:
        scorer = learned.scorer
        score_of = lambda item: learned.prob(item["feats"], item["flags"], item["evidence_weight"])  # noqa: E731
    else:
        scorer = rerank.POSE_SCORER
        score_of = lambda item: item["hand_prob"]  # noqa: E731

    shortlists = build_shortlists(matcher, negatives_per_query=negatives_per_query)
    if not shortlists:
        raise RuntimeError("no labelled queries found; run `seed` first or record staff feedback")
    train, test = split_shortlists(shortlists, holdout=holdout, seed=seed)

    def rows(sets):
        probs, labels = [], []
        for shortlist in sets:
            for item in shortlist.items[:10]:
                probs.append(float(score_of(item)))
                labels.append(int(item["label"]))
        return probs, labels

    fit_probs, fit_labels = rows(train)
    hold_probs, hold_labels = rows(test)
    raw = calibration.calibration_metrics(hold_probs, hold_labels)

    candidates: list[calibration.CalibrationParams] = []
    a, b = calibration.fit_platt(fit_probs, fit_labels)
    candidates.append(
        calibration.CalibrationParams(
            method="platt",
            version=f"platt-{_stamp()}",
            scorer=scorer,
            a=a,
            b=b,
            fit_size=len(fit_probs),
            holdout_size=len(hold_probs),
        )
    )
    knots = calibration.fit_isotonic(fit_probs, fit_labels)
    if knots:
        candidates.append(
            calibration.CalibrationParams(
                method="isotonic",
                version=f"isotonic-{_stamp()}",
                scorer=scorer,
                knots=knots,
                fit_size=len(fit_probs),
                holdout_size=len(hold_probs),
            )
        )

    scored = []
    for params in candidates:
        hold = calibration.calibration_metrics(params.apply_many(hold_probs), hold_labels)
        scored.append(
            {
                "method": params.method,
                "ece": hold["ece"],
                "brier": hold["brier"],
                "params": params,
                "bins": hold["bins"],
            }
        )

    if method == "auto":
        chosen = min(scored, key=lambda row: (row["ece"], row["brier"]))
    else:
        chosen = next((row for row in scored if row["method"] == method), None)
        if chosen is None:
            raise KeyError(f"unknown calibration method {method!r}")

    improved = chosen["ece"] <= raw["ece"]

    def calibrated_score(item: dict) -> float:
        return chosen["params"].apply(float(score_of(item)))

    derived = derive_operating_point(train, calibrated_score)
    holdout_decision = ranking_metrics(
        test, calibrated_score, thresholds=derived, tie_break=lambda item: float(score_of(item))
    )
    params = replace(
        chosen["params"],
        prob_alert=derived["prob_alert"],
        plausible_floor=derived["plausible_floor"],
        margin_min=derived["margin_min"],
        operating_point=derived,
        metrics={
            "raw": {"ece": raw["ece"], "brier": raw["brier"], "n": raw["n"]},
            "calibrated": {"ece": chosen["ece"], "brier": chosen["brier"]},
            "bins": chosen["bins"],
            "raw_bins": raw["bins"],
            "improved_ece": improved,
            "holdout_decision": holdout_decision,
        },
    )
    path = calibration.save(params, out) if (save and (improved or force)) else None
    return {
        "params": params,
        "path": path,
        "scorer": scorer,
        "raw": {"ece": raw["ece"], "brier": raw["brier"]},
        "calibrated": {"ece": chosen["ece"], "brier": chosen["brier"]},
        "operating_point": derived,
        "holdout_decision": holdout_decision,
        "candidates": [{k: v for k, v in row.items() if k != "params"} for row in scored],
        "fit_rows": len(fit_probs),
        "holdout_rows": len(hold_probs),
        "improved_ece": improved,
    }


# -------------------------------------------------------------------- reports
def calibration_report_markdown(result: dict) -> str:
    params = result["params"]
    lines = [
        "# Calibration report",
        "",
        f"Scorer: `{result['scorer']}`  ·  method: `{params.method}`  ·  version: `{params.version}`",
        f"Fit rows: {result['fit_rows']}  ·  holdout rows: {result['holdout_rows']} (split by query)",
        "",
        "| Metric | Raw | Calibrated |",
        "|---|---|---|",
        f"| ECE | {result['raw']['ece']} | {result['calibrated']['ece']} |",
        f"| Brier | {result['raw']['brier']} | {result['calibrated']['brier']} |",
        "",
        "## Reliability (calibrated, holdout)",
        "",
        "| Bin | n | mean predicted | observed |",
        "|---|---|---|---|",
    ]
    for row in params.metrics.get("bins", []):
        low, high = row["range"]
        lines.append(f"| {low}–{high} | {row['count']} | {row['mean_prob']} | {row['observed']} |")
    lines += [
        "",
        "## Method comparison (holdout)",
        "",
        "| Method | ECE | Brier |",
        "|---|---|---|",
    ]
    for row in result["candidates"]:
        lines.append(f"| {row['method']} | {row['ece']} | {row['brier']} |")
    lines += [
        "",
        "ECE target from the plan is < 0.05 on held-out data. A calibration is applied only",
        "when its `scorer` matches the scorer that produced the served probability.",
        "",
    ]
    return "\n".join(lines)


def reranker_report_markdown(result: dict) -> str:
    model = result["model"]
    base, learned = result["baseline"], result["reranked"]
    calibrated = result.get("reranked_calibrated", learned)

    def row(label: str, key: str) -> str:
        return f"| {label} | {base.get(key)} | {learned.get(key)} | {calibrated.get(key)} |"

    lines = [
        "# Re-ranker ablation",
        "",
        f"Model version: `{model.version}`  ·  trained pairs: {model.train_size}",
        f"Queries: {result['train_queries']} train / {result['holdout_queries']} holdout (split by query id)",
        "",
        "| Metric (holdout) | Hand-set weights | Learned (raw score) | Learned + calibrated |",
        "|---|---|---|---|",
        row("Recall@1", "recall@1"),
        row("Recall@10", "recall@10"),
        row("MRR", "mrr"),
        row("nDCG@10", "ndcg@10"),
        row("Precision@alert", "precision@alert"),
        row("Coverage (alert rate)", "coverage"),
        row("ECE", "ece"),
        row("Brier", "brier"),
        "",
        "The learned score ranks better than the hand-set weights; calibration turns that",
        "score into a probability so the alert threshold still means what it says.",
        "Serve with both artifacts present; delete either to fall back.",
        "",
    ]
    return "\n".join(lines)


__all__ = [
    "Shortlist",
    "build_shortlists",
    "calibrate_scorer",
    "calibration_report_markdown",
    "evaluate_reranker",
    "ranking_metrics",
    "reranker_report_markdown",
    "split_shortlists",
    "train_reranker",
]
