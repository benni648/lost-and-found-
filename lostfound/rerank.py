"""Learned re-ranker (Phase 3, task E).

The hand-set weights in :mod:`lostfound.features` are a defensible baseline
and they stay the fallback: if no model artifact exists, the matcher behaves
exactly as before. When an artifact *is* present, this module scores the
ANN/exhaustive shortlist with a learned model instead.

The model is a **pairwise logistic (RankNet) scorer over the same feature
vector**, implemented in plain numpy so it trains and serves on a laptop with
no LightGBM and no GPU. That is a deliberate trade: the plan's
recommendation (LambdaMART) is the right end state, but a learned scorer that
actually trains on the labels we have beats a hand-tuned constant *today*, and
the interface here is the same one LambdaMART would plug into.

Two properties are non-negotiable and are enforced by tests:

* **Train/serve parity** — ``design()`` is the only place a feature dict turns
  into a model vector, and training imports it from here.
* **Honest holdout** — splits are by lost-report (item), never by pair, so the
  model cannot memorise an item across train and test.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .logging import get_logger
from .settings import settings

log = get_logger(__name__)

# Served feature layout. Order is part of the model artifact: changing it
# requires a new version, never an in-place edit.
FEATURES: tuple[str, ...] = (
    "category",
    "serial",
    "text",
    "image",
    "model",
    "colour",
    "brand",
    "time",
    "location",
    "marks",
    "material",
    "damages",
    "rarity",
    "token_overlap",
)
EXTRA_SCALARS: tuple[str, ...] = ("evidence_weight",)
FLAGS: tuple[str, ...] = ("category_mismatch", "model_mismatch", "serial_mismatch", "found_before_lost")
MISSING_CORE: tuple[str, ...] = ("category", "colour", "brand", "material")

POSE_SCORER = "handset"


def design(feats: dict, flags: list[str] | tuple[str, ...] = (), evidence_weight: float | None = None) -> np.ndarray:
    """Canonical train/serve feature vector for one (lost, found) pair.

    Missing evidence is encoded as ``0.0``; the ``missing_*`` indicators let the
    model distinguish "not stated" from "measured and bad", which is the
    difference between a one-line report and a contradictory one.
    """
    values: list[float] = []
    for name in FEATURES:
        raw = feats.get(name)
        values.append(0.0 if raw is None else float(raw))
    weight = evidence_weight if evidence_weight is not None else feats.get("evidence_weight")
    for _ in EXTRA_SCALARS:
        values.append(0.0 if weight is None else float(weight))
    flag_set = set(flags or ())
    values.extend(1.0 if name in flag_set else 0.0 for name in FLAGS)
    values.extend(1.0 if feats.get(name) is None else 0.0 for name in MISSING_CORE)
    return np.asarray(values, dtype=np.float64)


def feature_names() -> list[str]:
    return [
        *FEATURES,
        *EXTRA_SCALARS,
        *[f"flag_{name}" for name in FLAGS],
        *[f"missing_{name}" for name in MISSING_CORE],
    ]


def _sigmoid(z):
    # Clamp before the exponential: an unbounded logit from a trained ranker
    # would otherwise squash distinct candidates onto an identical 0.0 or 1.0
    # and make the ranking depend on insertion order.
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30.0, 30.0)))


@dataclass(frozen=True)
class ReRankModel:
    version: str
    weights: tuple[float, ...]
    mean: tuple[float, ...]
    scale: tuple[float, ...]
    features: tuple[str, ...] = field(default_factory=lambda: tuple(feature_names()))
    bias: float = 0.0
    train_size: int = 0
    trained_at: str = ""
    metrics: dict = field(default_factory=dict)
    encoder: dict = field(default_factory=dict)

    @property
    def scorer(self) -> str:
        return f"rerank:{self.version}"

    def _standardize(self, x: np.ndarray) -> np.ndarray:
        mean = np.asarray(self.mean, dtype=np.float64)
        scale = np.asarray(self.scale, dtype=np.float64)
        return (x - mean) / np.where(scale > 0, scale, 1.0)

    def logit(self, x: np.ndarray) -> float:
        w = np.asarray(self.weights, dtype=np.float64)
        return float(np.dot(self._standardize(x), w) + self.bias)

    def prob(self, feats: dict, flags=(), evidence_weight: float | None = None) -> float:
        return float(_sigmoid(self.logit(design(feats, flags, evidence_weight))))

    # -------------------------------------------------------------- (de)serde
    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "features": list(self.features),
            "weights": [round(float(w), 8) for w in self.weights],
            "bias": round(float(self.bias), 8),
            "mean": [round(float(m), 8) for m in self.mean],
            "scale": [round(float(s), 8) for s in self.scale],
            "train_size": self.train_size,
            "trained_at": self.trained_at,
            "metrics": self.metrics,
            "encoder": self.encoder,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> ReRankModel:
        features = tuple(payload["features"])
        if features != tuple(feature_names()):
            raise ValueError(
                "re-ranker artifact feature layout does not match this code; retrain it (serving parity check)"
            )
        return cls(
            version=str(payload["version"]),
            weights=tuple(float(w) for w in payload["weights"]),
            mean=tuple(float(m) for m in payload["mean"]),
            scale=tuple(float(s) for s in payload["scale"]),
            features=features,
            bias=float(payload.get("bias", 0.0)),
            train_size=int(payload.get("train_size", 0)),
            trained_at=str(payload.get("trained_at", "")),
            metrics=dict(payload.get("metrics") or {}),
            encoder=dict(payload.get("encoder") or {}),
        )


# ------------------------------------------------------------------- training
def fit(
    groups: list[list[tuple[np.ndarray, int]]],
    *,
    epochs: int = 240,
    learning_rate: float = 0.35,
    l2: float = 1e-3,
    version: str | None = None,
    seed: int = 0,
    metrics: dict | None = None,
    encoder: dict | None = None,
    trained_at: str = "",
) -> ReRankModel:
    """Fit a pairwise logistic ranker on labelled candidate groups.

    ``groups`` is one list of ``(design_vector, label)`` per query. Pairs form
    only *within* a group, which is the ranking objective: a positive only has
    to beat its own negatives.
    """
    if not any(sum(label for _, label in group) for group in groups):
        raise ValueError("training data contains no positive pairs; cannot fit a ranker")

    stacked = np.vstack([x for group in groups for x, _ in group])
    mean = stacked.mean(axis=0)
    scale = stacked.std(axis=0)
    scale[scale < 1e-9] = 1.0

    pairs: list[tuple[np.ndarray, np.ndarray]] = []
    for group in groups:
        positives = [x for x, label in group if label == 1]
        negatives = [x for x, label in group if label == 0]
        if not positives or not negatives:
            continue
        for pos in positives:
            for neg in negatives:
                pairs.append(((pos - mean) / scale, (neg - mean) / scale))
    if not pairs:
        raise ValueError("no positive/negative pairs within any query group; cannot fit a ranker")

    rng = np.random.default_rng(seed)
    pos_matrix = np.vstack([p for p, _ in pairs])
    neg_matrix = np.vstack([n for _, n in pairs])
    weights = np.zeros(pos_matrix.shape[1], dtype=np.float64)
    bias = 0.0

    n = pos_matrix.shape[0]
    batch = min(n, 512)
    for _ in range(epochs):
        order = rng.permutation(n)
        for start in range(0, n, batch):
            idx = order[start : start + batch]
            diff = pos_matrix[idx] - neg_matrix[idx]
            z = diff @ weights
            # d/dz of softplus(-z) is -sigmoid(-z) = sigmoid(z) - 1
            g = _sigmoid(z) - 1.0
            grad = diff.T @ g / len(idx) + l2 * weights
            weights -= learning_rate * grad
            bias -= learning_rate * float(g.mean())
        learning_rate *= 0.995

    version = version or f"p{len(pairs)}-" + hashlib.sha256(np.round(weights, 6).tobytes()).hexdigest()[:8]
    return ReRankModel(
        version=version,
        weights=tuple(float(w) for w in weights),
        mean=tuple(float(m) for m in mean),
        scale=tuple(float(s) for s in scale),
        bias=float(bias),
        train_size=int(len(pairs)),
        trained_at=trained_at,
        metrics=dict(metrics or {}),
        encoder=dict(encoder or {}),
    )


# ---------------------------------------------------------------- persistence
_CACHE: dict[str, tuple[float, ReRankModel]] = {}


def load(path: Path | None = None) -> ReRankModel | None:
    """Load the served re-ranker, or ``None`` to keep the hand-set scorer."""
    target = Path(path) if path is not None else settings.reranker_path
    key = str(target)
    try:
        mtime = target.stat().st_mtime
    except OSError:
        return None
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        model = ReRankModel.from_dict(json.loads(target.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError) as exc:
        log.warning("ignoring unreadable re-ranker artifact %s: %s", target, exc)
        return None
    _CACHE[key] = (mtime, model)
    return model


def save(model: ReRankModel, path: Path | None = None) -> Path:
    target = Path(path) if path is not None else settings.reranker_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(model.as_dict(), indent=2) + "\n", encoding="utf-8")
    return target


def clear_cache() -> None:
    _CACHE.clear()


def active_scorer() -> str:
    model = load()
    return model.scorer if model is not None else POSE_SCORER


__all__ = [
    "FEATURES",
    "FLAGS",
    "MISSING_CORE",
    "POSE_SCORER",
    "ReRankModel",
    "active_scorer",
    "clear_cache",
    "design",
    "feature_names",
    "fit",
    "load",
    "save",
]
