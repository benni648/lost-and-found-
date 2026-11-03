"""Probability calibration (Phase 3, task F).

``features.score_pair`` squashes the score with a fixed sigmoid. That is
monotone, which is all ranking needs, but it is **not** a probability: when it
says 0.7, the true pair does not occur 70% of the time. This module fits a
monotone map from the raw probability to an observed frequency, reports the
calibration error (ECE) and Brier score, and stores the fitted parameters with
a version so a serving process can apply exactly the model that was measured.

Two fits are implemented in plain numpy so calibration works in the offline,
dependency-free runtime:

* **Platt** — logistic ``sigmoid(a * logit(p) + b)``; smooth, monotone as long
  as ``a > 0``, very few parameters, the safe default.
* **Isotonic** — pool-adjacent-violators staircase; more expressive, needs more
  data, and is clipped to the observed range.

A served calibration is only meaningful for the scorer it was fitted on, so
``CalibrationParams.scorer`` records that and :meth:`CalibrationParams.matches`
gates application. Nothing is applied unless a params file exists — the
matcher's behaviour is unchanged on a fresh checkout.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .logging import get_logger
from .settings import settings

log = get_logger(__name__)

EPS = 1e-6
IDENTITY_SCORER = "handset"
HANDSET_SCORER = "handset"


def _logit(prob: float) -> float:
    p = min(max(float(prob), EPS), 1.0 - EPS)
    return math.log(p / (1.0 - p))


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def _sigmoid_array(z: np.ndarray) -> np.ndarray:
    """Sigmoid that never overflows and never returns an exact 0 or 1.

    A learned ranker's logits can sit at +/-40; squashing those with a naive
    ``exp`` both warns and, worse, flattens distinct candidates onto the same
    probability, which destroys ordering.
    """
    z = np.clip(np.asarray(z, dtype=np.float64), -30.0, 30.0)
    out = np.empty_like(z)
    positive = z >= 0
    out[positive] = 1.0 / (1.0 + np.exp(-z[positive]))
    e = np.exp(z[~positive])
    out[~positive] = e / (1.0 + e)
    return out


@dataclass(frozen=True)
class CalibrationParams:
    """A fitted, versioned calibration map."""

    method: str
    version: str
    scorer: str = HANDSET_SCORER
    a: float = 1.0
    b: float = 0.0
    # Which scale ``a``/``b`` act on: a probability (the hand-set sigmoid) or an
    # unbounded logit (a learned ranker). A learned ranker's logits can be wide
    # enough that its own sigmoid saturates to 0/1, and a Platt fit on those
    # clamped probabilities collapses to a constant map. Fitting the logit
    # keeps the resolution the ranker actually has.
    input_scale: str = "prob"
    knots: tuple[tuple[float, float], ...] = ()
    fit_size: int = 0
    holdout_size: int = 0
    metrics: dict = field(default_factory=dict)
    # Operating point measured on the calibrated scale. A threshold tuned for
    # one scorer's raw sigmoid is meaningless for another scorer's probability,
    # so the thresholds travel with the calibration that produced them.
    prob_alert: float | None = None
    plausible_floor: float | None = None
    margin_min: float | None = None
    operating_point: dict = field(default_factory=dict)

    # ------------------------------------------------------------------ apply
    def apply(self, prob: float) -> float:
        return self.apply_many([prob])[0]

    def apply_many(self, probs) -> list[float]:
        raw = np.asarray([min(max(float(p), EPS), 1.0 - EPS) for p in probs], dtype=np.float64)
        if self.method == "isotonic":
            if not self.knots:
                return raw.tolist()
            xs = np.asarray([k[0] for k in self.knots], dtype=np.float64)
            ys = np.asarray([k[1] for k in self.knots], dtype=np.float64)
            out = np.interp(raw, xs, ys)
        else:
            if self.input_scale == "logit":
                z = np.clip(np.asarray([float(p) for p in probs], dtype=np.float64), -30.0, 30.0)
            else:
                z = np.log(raw / (1.0 - raw))
            out = _sigmoid_array(self.a * z + self.b)
        return np.clip(out, EPS, 1.0 - EPS).tolist()

    def matches(self, scorer: str) -> bool:
        """Refuse to apply a calibration fitted for a different scorer."""
        return self.scorer in ("", "any") or self.scorer == scorer

    # -------------------------------------------------------------- (de)serde
    def as_dict(self) -> dict:
        return {
            "method": self.method,
            "version": self.version,
            "scorer": self.scorer,
            "a": self.a,
            "b": self.b,
            "input_scale": self.input_scale,
            "knots": [list(k) for k in self.knots],
            "fit_size": self.fit_size,
            "holdout_size": self.holdout_size,
            "metrics": self.metrics,
            "prob_alert": self.prob_alert,
            "plausible_floor": self.plausible_floor,
            "margin_min": self.margin_min,
            "operating_point": self.operating_point,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> CalibrationParams:
        return cls(
            method=str(payload["method"]),
            version=str(payload.get("version", "1")),
            scorer=str(payload.get("scorer", HANDSET_SCORER)),
            a=float(payload.get("a", 1.0)),
            b=float(payload.get("b", 0.0)),
            input_scale=str(payload.get("input_scale", "prob")),
            knots=tuple((float(x), float(y)) for x, y in payload.get("knots", [])),
            fit_size=int(payload.get("fit_size", 0)),
            holdout_size=int(payload.get("holdout_size", 0)),
            metrics=dict(payload.get("metrics") or {}),
            prob_alert=(None if payload.get("prob_alert") is None else float(payload["prob_alert"])),
            plausible_floor=(None if payload.get("plausible_floor") is None else float(payload["plausible_floor"])),
            margin_min=(None if payload.get("margin_min") is None else float(payload["margin_min"])),
            operating_point=dict(payload.get("operating_point") or {}),
        )


# ---------------------------------------------------------------------- fits
def fit_platt(probs, labels, iterations: int = 4000, learning_rate: float = 0.5) -> tuple[float, float]:
    """Fit ``sigmoid(a * logit(p) + b)`` by gradient descent on the log-loss.

    Newton's method is the usual choice, but it needs a non-singular Hessian:
    a saturating scorer gives weights near zero and the fit silently collapses
    to the identity, which is the opposite of what calibration is for. Gradient
    descent on the standardised logit is slower and far harder to break.
    """
    p = np.clip(np.asarray(probs, dtype=np.float64), EPS, 1.0 - EPS)
    return fit_platt_logit(np.log(p / (1.0 - p)), labels, iterations, learning_rate)


def fit_platt_logit(logits, labels, iterations: int = 4000, learning_rate: float = 0.5) -> tuple[float, float]:
    """Fit ``sigmoid(a * z + b)`` directly on a model's logits.

    This is the fit a learned ranker needs. Recovering logits from its already
    saturated probabilities first would quantise them to two values and fit a
    map that returns the same probability for every candidate.
    """
    z = np.clip(np.asarray(logits, dtype=np.float64), -30.0, 30.0)
    y = np.asarray(labels, dtype=np.float64)
    if z.size == 0 or y.min() == y.max():
        # one class only: nothing to learn, stay at the identity map
        return 1.0, 0.0
    if float(z.std()) < 1e-9:
        # every candidate got the same score: no monotone map can separate them
        return 1.0, 0.0

    mean = float(z.mean())
    scale = float(z.std()) or 1.0
    z_std = (z - mean) / scale

    a, b = 1.0, 0.0
    for _ in range(iterations):
        q = _sigmoid_array(a * z_std + b)
        residual = q - y
        grad_a = float(np.mean(residual * z_std))
        grad_b = float(np.mean(residual))
        if abs(grad_a) < 1e-9 and abs(grad_b) < 1e-9:
            break
        a = min(max(a - learning_rate * grad_a, 1e-3), 20.0)
        b = min(max(b - learning_rate * grad_b, -20.0), 20.0)

    slope = round(a / scale, 9)
    if a <= 0 or slope <= 0:  # keep the map monotone increasing; a non-positive
        return 1.0, 0.0  # slope means the data cannot support a calibration
    # undo the standardisation: a*z_std + b == (a/scale)*z + (b - a*mean/scale)
    return slope, round(b - a * mean / scale, 6)


def fit_isotonic(probs, labels) -> tuple[tuple[float, float], ...]:
    """Pool-adjacent-violators fit, returned as monotone ``(x, y)`` knots."""
    p = np.clip(np.asarray(probs, dtype=np.float64), EPS, 1.0 - EPS)
    y = np.asarray(labels, dtype=np.float64)
    if p.size == 0:
        return ()
    order = np.argsort(p, kind="stable")
    p, y = p[order], y[order]

    # pool adjacent violators over (mean, weight) blocks
    block_mean: list[float] = []
    block_weight: list[float] = []
    block_max: list[float] = []
    for value, label in zip(p, y, strict=True):
        block_mean.append(float(label))
        block_weight.append(1.0)
        block_max.append(float(value))
        while len(block_mean) > 1 and block_mean[-2] > block_mean[-1]:
            w2 = block_weight.pop()
            m2 = block_mean.pop()
            x2 = block_max.pop()
            w1 = block_weight.pop()
            m1 = block_mean.pop()
            x1 = block_max.pop()
            total = w1 + w2
            block_mean.append((m1 * w1 + m2 * w2) / total)
            block_weight.append(total)
            block_max.append(x2 if x2 > x1 else x1)

    knots: list[tuple[float, float]] = []
    start = 0
    for mean, weight, last_x in zip(block_mean, block_weight, block_max, strict=True):
        count = int(round(weight))
        low = float(p[start])
        # one knot at each edge of the block's x-range keeps the staircase shape
        knots.append((low, float(mean)))
        if last_x > low:
            knots.append((float(last_x), float(mean)))
        start += count
    if not knots:
        return ()
    # np.interp needs strictly increasing x
    dedup: dict[float, float] = {}
    for x, value in knots:
        dedup[x] = value
    return tuple(sorted((round(x, 6), round(v, 6)) for x, v in dedup.items()))


# ------------------------------------------------------------------- metrics
def reliability_bins(probs, labels, bins: int = 10) -> list[dict]:
    """Per-bin counts, mean predicted probability and observed frequency."""
    p = np.clip(np.asarray(probs, dtype=np.float64), 0.0, 1.0)
    y = np.asarray(labels, dtype=np.float64)
    rows: list[dict] = []
    if p.size == 0:
        return rows
    idx = np.minimum((p * bins).astype(int), bins - 1)
    for b in range(bins):
        mask = idx == b
        count = int(mask.sum())
        if count == 0:
            continue
        rows.append(
            {
                "bin": b,
                "range": (round(b / bins, 2), round((b + 1) / bins, 2)),
                "count": count,
                "mean_prob": round(float(p[mask].mean()), 4),
                "observed": round(float(y[mask].mean()), 4),
            }
        )
    return rows


def expected_calibration_error(probs, labels, bins: int = 10) -> float:
    """Weighted mean |observed - predicted| across confidence bins."""
    p = np.clip(np.asarray(probs, dtype=np.float64), 0.0, 1.0)
    y = np.asarray(labels, dtype=np.float64)
    if p.size == 0:
        return 0.0
    idx = np.minimum((p * bins).astype(int), bins - 1)
    total = float(p.size)
    ece = 0.0
    for b in range(bins):
        mask = idx == b
        count = int(mask.sum())
        if count == 0:
            continue
        gap = abs(float(y[mask].mean()) - float(p[mask].mean()))
        ece += (count / total) * gap
    return round(ece, 4)


def brier_score(probs, labels) -> float:
    p = np.asarray(probs, dtype=np.float64)
    y = np.asarray(labels, dtype=np.float64)
    if p.size == 0:
        return 0.0
    return round(float(np.mean((p - y) ** 2)), 4)


def calibration_metrics(probs, labels, bins: int = 10) -> dict:
    return {
        "n": int(np.asarray(probs).size),
        "ece": expected_calibration_error(probs, labels, bins),
        "brier": brier_score(probs, labels),
        "bins": reliability_bins(probs, labels, bins),
    }


# ------------------------------------------------------------- persistence
_CACHE: dict[str, tuple[float, CalibrationParams]] = {}


def load(path: Path | None = None) -> CalibrationParams | None:
    """Load the served calibration, or ``None`` when none has been fitted."""
    target = Path(path) if path is not None else settings.calibration_path
    key = str(target)
    try:
        mtime = target.stat().st_mtime
    except OSError:
        return None
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        params = CalibrationParams.from_dict(json.loads(target.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError) as exc:
        log.warning("ignoring unreadable calibration file %s: %s", target, exc)
        return None
    _CACHE[key] = (mtime, params)
    return params


def save(params: CalibrationParams, path: Path | None = None) -> Path:
    target = Path(path) if path is not None else settings.calibration_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(params.as_dict(), indent=2) + "\n", encoding="utf-8")
    return target


def clear_cache() -> None:
    _CACHE.clear()


def operating_point(scorer: str | None = None) -> dict:
    """Effective decision thresholds for the scorer that is actually serving.

    Without a matching calibration these are the configured defaults; with one
    they are the thresholds measured against the calibrated probabilities, so
    the alert rate keeps the precision budget it was tuned for.
    """
    effective = {
        "plausible_floor": settings.plausible_floor,
        "prob_alert": settings.prob_alert,
        "margin_min": settings.margin_min,
        "calibration_version": None,
    }
    params = load()
    if params is None or (scorer is not None and not params.matches(scorer)):
        return effective
    for key in ("plausible_floor", "prob_alert", "margin_min"):
        value = getattr(params, key)
        if value is not None:
            effective[key] = value
    effective["calibration_version"] = params.version
    return effective


def calibrate_prob(prob: float, params: CalibrationParams | None = None) -> float:
    params = params if params is not None else load()
    return prob if params is None else params.apply(prob)


__all__ = [
    "CalibrationParams",
    "HANDSET_SCORER",
    "brier_score",
    "calibrate_prob",
    "calibration_metrics",
    "clear_cache",
    "expected_calibration_error",
    "fit_isotonic",
    "fit_platt",
    "fit_platt_logit",
    "load",
    "operating_point",
    "reliability_bins",
    "save",
]
