"""Pair features and scoring.

Every measurement is ``Optional[float]`` in ``[0, 1]`` where ``None`` means
"this evidence does not exist on either side". The final score is a
*weight-normalised mean over available evidence*, not a sum -- that is what
makes a one-line report ("blue bottle") competitive instead of being punished
for the twelve fields it never mentioned.

``score_pair`` also emits human-readable reasons, which is what the UI shows
as chips and what makes an otherwise opaque number defensible.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from .campus import Campus
from .colorspace import color_distance_by_name
from .geo import distance_label, geo_similarity, haversine_m
from .schema import Attributes
from .store import Report
from .taxonomy import CATEGORIES, CONFUSABLE
from .textnorm import content_tokens
from .timeutil import parse_utc

WEIGHTS: dict[str, float] = {
    "category": 0.20,
    "serial": 0.12,
    "text": 0.12,
    "image": 0.11,
    "model": 0.10,
    "colour": 0.10,
    "brand": 0.06,
    "time": 0.05,
    "location": 0.04,
    "geo": 0.04,
    "marks": 0.04,
    "material": 0.03,
    "damages": 0.03,
    "rarity": 0.02,
}

# When the two sides state different categories or an impossible order of
# events, the pair is still scored (so we can show it in "other candidates")
# but heavily suppressed.
CATEGORY_MISMATCH_FACTOR = 0.45
IMPOSSIBLE_TIME_FACTOR = 0.15
# A stated model number or serial is an identity *assertion*, not soft
# evidence: two different model numbers cannot be the same object. Weighted
# averaging alone would still call an iPhone 12 a 0.86 match for a 13.
MODEL_MISMATCH_FACTOR = 0.45
SERIAL_MISMATCH_FACTOR = 0.30

CORE = ("category", "brand", "colour", "material")


@dataclass
class MatchContext:
    campus: Campus
    text_vecs: dict[str, np.ndarray]
    image_vecs: dict[str, np.ndarray]
    category_freq: Counter
    colour_category_freq: Counter
    total: int

    @classmethod
    def build(cls, campus, text_vecs, image_vecs, reports) -> MatchContext:
        cat_freq = Counter(r.category for r in reports if r.category)
        cc_freq = Counter((r.category, (r.attrs or {}).get("primary_color")) for r in reports if r.category)
        return cls(campus, text_vecs, image_vecs, cat_freq, cc_freq, len(reports))

    def add_report(self, report: Report) -> None:
        if report.category:
            self.category_freq[report.category] += 1
            colour = (report.attrs or {}).get("primary_color")
            self.colour_category_freq[(report.category, colour)] += 1
        self.total += 1

    def remove_report(self, report: Report) -> None:
        if report.category:
            self.category_freq[report.category] -= 1
            if self.category_freq[report.category] <= 0:
                del self.category_freq[report.category]
            colour = (report.attrs or {}).get("primary_color")
            key = (report.category, colour)
            self.colour_category_freq[key] -= 1
            if self.colour_category_freq[key] <= 0:
                del self.colour_category_freq[key]
        self.total = max(0, self.total - 1)

    def rarity(self, category: str | None, colour: str | None) -> float:
        """0 = extremely common combination, 1 = rare on this campus."""
        if not category:
            return 0.5
        if self.total <= 1:
            return 1.0
        n = self.colour_category_freq.get((category, colour), 1) if colour else self.category_freq.get(category, 1)
        p = max(1, n) / self.total
        return max(0.0, min(1.0, -math.log(p) / math.log(self.total)))


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _cos(a: np.ndarray | None, b: np.ndarray | None) -> float | None:
    if a is None or b is None:
        return None
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return None
    return float(np.dot(a, b) / denom)


@lru_cache(maxsize=4096, typed=True)
def _cached_attrs(serialized_attrs: str) -> Attributes:
    return Attributes.from_dict(json.loads(serialized_attrs))


@lru_cache(maxsize=4096, typed=True)
def _cached_tokens(description: str) -> frozenset[str]:
    return frozenset(content_tokens(description))


def _attrs(report: Report) -> Attributes:
    """Parsing attributes and tokenising text dominate the inner loop."""
    serialized_attrs = json.dumps(report.attrs, sort_keys=True, separators=(",", ":"))
    return _cached_attrs(serialized_attrs)


def _tokens(report: Report) -> frozenset[str]:
    return _cached_tokens(report.description)


def _jaccard(a, b) -> float | None:
    if not a or not b:
        return None
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb)


def hours_between(lost: Report, found: Report) -> tuple[float, bool]:
    """Gap in hours between the lost interval and the moment it was found.

    Returns ``(hours, impossible)``. ``hours`` is 0 when the found report falls
    inside the interval the owner reported, which is the honest way to compare
    an interval against a timestamp.
    """
    import datetime as dt

    lo = parse_utc(lost.lost_from) if lost.lost_from else parse_utc(lost.created_at)
    hi = parse_utc(lost.lost_to) if lost.lost_to else lo + dt.timedelta(hours=2)
    found_at = parse_utc(found.created_at)
    if lo <= found_at <= hi:
        return 0.0, False
    if found_at < lo:
        return (lo - found_at).total_seconds() / 3600.0, True
    return (found_at - hi).total_seconds() / 3600.0, False


def measure(lost: Report, found: Report, ctx: MatchContext) -> dict:
    """Raw measurements for one (lost, found) pair."""
    a, b = _attrs(lost), _attrs(found)
    m: dict[str, float | None] = {}

    # -- category ----------------------------------------------------------
    if a.category and b.category:
        if a.category == b.category:
            m["category"] = 1.0
        else:
            confusable = b.category in CONFUSABLE.get(a.category, [])
            m["category"] = 0.25 if confusable else 0.0
    else:
        m["category"] = None

    # -- model number (iPhone 13 vs 12) ------------------------------------
    if a.model and b.model:
        m["model"] = 1.0 if a.model.lower() == b.model.lower() else 0.0
    else:
        m["model"] = None

    # -- serial / printed text --------------------------------------------
    if a.visible_text and b.visible_text:
        sa = {s.upper() for s in a.visible_text}
        sb = {s.upper() for s in b.visible_text}
        if sa & sb:
            m["serial"] = 1.0
        else:
            partial = any(x in y or y in x for x in sa for y in sb)
            m["serial"] = 0.5 if partial else 0.0
    else:
        m["serial"] = None

    # -- colour ------------------------------------------------------------
    d = color_distance_by_name(a.primary_color, b.primary_color)
    m["colour"] = None if d is None else _clamp(math.exp(-d / 18.0))

    # -- brand / material --------------------------------------------------
    if a.brand and b.brand:
        m["brand"] = 1.0 if a.brand == b.brand else 0.05
    else:
        m["brand"] = None
    if a.material and b.material:
        m["material"] = 1.0 if a.material == b.material else 0.25
    else:
        m["material"] = None

    # -- damage / marks ----------------------------------------------------
    m["damages"] = _jaccard(a.damages, b.damages)
    m["marks"] = _jaccard(a.marks, b.marks)

    # -- lexical + semantic text ------------------------------------------
    cos = _cos(ctx.text_vecs.get(lost.id), ctx.text_vecs.get(found.id))
    m["text"] = None if cos is None else _clamp((cos - 0.15) / 0.6)
    m["token_overlap"] = _jaccard(_tokens(lost), _tokens(found))

    # -- visual ------------------------------------------------------------
    icos = _cos(ctx.image_vecs.get(lost.id), ctx.image_vecs.get(found.id))
    m["image"] = None if icos is None else _clamp((icos - 0.55) / 0.4)

    # -- time --------------------------------------------------------------
    hours, impossible = hours_between(lost, found)
    m["hours"] = round(hours, 2)
    m["impossible"] = 1.0 if impossible else 0.0
    scale = CATEGORIES.get(a.category or "", {}).get("hold_hours", 24.0)
    m["time"] = _clamp(math.exp(-max(0.0, hours - 1.0) / scale))

    # -- location ----------------------------------------------------------
    minutes = ctx.campus.minutes(lost.node_key, found.node_key)
    m["minutes_apart"] = minutes
    if minutes is None:
        m["location"] = None
    else:
        v = _clamp(math.exp(-minutes / 9.0))
        if ctx.campus.same_building(lost.node_key, found.node_key):
            v = max(v, 0.85)
        m["location"] = v

    m["rarity"] = ctx.rarity(a.category, a.primary_color or b.primary_color)

    # -- real geo distance (GPS), when both sides were pinned ---------------
    if None not in (lost.latitude, lost.longitude, found.latitude, found.longitude):
        metres = haversine_m(lost.latitude, lost.longitude, found.latitude, found.longitude)
        m["geo_distance_m"] = round(metres, 1)
        m["geo"] = geo_similarity(metres)
    else:
        m["geo_distance_m"] = None
        m["geo"] = None
    return m


def score_pair(lost: Report, found: Report, ctx: MatchContext) -> dict:
    """Turn measurements into ``{score, prob, feats, reasons, flags}``."""
    feats = measure(lost, found, ctx)

    total_w = 0.0
    accum = 0.0
    contributions: dict[str, float] = {}
    for name, w in WEIGHTS.items():
        v = feats.get(name)
        if v is None:
            continue
        total_w += w
        accum += w * float(v)
        contributions[name] = w * float(v)

    score = accum / total_w if total_w else 0.0
    flags: list[str] = []
    if feats.get("category") is not None and float(feats["category"]) < 0.5:
        score *= CATEGORY_MISMATCH_FACTOR
        flags.append("category_mismatch")
    if feats.get("model") == 0.0:
        score *= MODEL_MISMATCH_FACTOR
        flags.append("model_mismatch")
    if feats.get("serial") == 0.0:
        score *= SERIAL_MISMATCH_FACTOR
        flags.append("serial_mismatch")
    if feats.get("impossible"):
        score *= IMPOSSIBLE_TIME_FACTOR
        flags.append("found_before_lost")

    prob = 1.0 / (1.0 + math.exp(-6.5 * (score - 0.55)))
    return {
        "score": round(score, 4),
        "prob": round(prob, 4),
        "feats": {k: (None if v is None else round(float(v), 4)) for k, v in feats.items()},
        "contributions": {k: round(v, 4) for k, v in contributions.items()},
        "flags": flags,
        "evidence_weight": round(total_w / sum(WEIGHTS.values()), 3),
    }


# ------------------------------------------------------------------ explain
def _fmt_hours(h: float) -> str:
    if h < 1:
        return f"{int(round(h * 60))} min"
    if h < 48:
        return f"{h:.1f} h"
    return f"{h / 24:.1f} days"


def explain(lost: Report, found: Report, ctx: MatchContext, scored: dict | None = None) -> list[dict]:
    """Human-readable chips for the UI, ordered by influence."""
    feats = (scored or score_pair(lost, found, ctx))["feats"]
    a, b = _attrs(lost), _attrs(found)
    chips: list[dict] = []

    def add(label: str, kind: str, weight: float) -> None:
        chips.append({"label": label, "kind": kind, "weight": round(weight, 3)})

    if feats.get("serial") == 1.0:
        add("serial / tag number matches", "positive", 1.0)
    elif feats.get("serial") == 0.0:
        add("printed serial differs", "negative", 1.0)

    if feats.get("model") == 1.0 and a.model:
        add(f"same model ({a.model})", "positive", 0.9)
    elif feats.get("model") == 0.0 and a.model and b.model:
        add(f"different model ({a.model} vs {b.model})", "negative", 1.0)

    if feats.get("colour") is not None:
        d = color_distance_by_name(a.primary_color, b.primary_color) or 0
        if feats["colour"] >= 0.8:
            add(f"colour matches ({a.primary_color or b.primary_color})", "positive", 0.7)
        elif feats["colour"] <= 0.3:
            add(f"colour differs ({a.primary_color} vs {b.primary_color}, dE {d:.0f})", "negative", 0.8)

    if feats.get("brand") == 1.0 and a.brand:
        add(f"brand matches ({a.brand})", "positive", 0.5)
    elif feats.get("brand") == 0.05:
        add(f"brand differs ({a.brand} vs {b.brand})", "negative", 0.5)

    if feats.get("marks") not in (None, 0.0):
        add("distinguishing mark matches", "positive", 0.6)
    if feats.get("damages") not in (None, 0.0):
        add("same damage described", "positive", 0.4)

    if feats.get("image") is not None:
        if feats["image"] >= 0.6:
            add("photos look similar", "positive", 0.6)
        elif feats["image"] <= 0.2:
            add("photos look different", "negative", 0.5)

    if feats.get("text") is not None and feats["text"] >= 0.5:
        add("wording lines up", "positive", 0.35)

    if feats.get("location") is not None:
        mins = feats.get("minutes_apart") or 0
        if ctx.campus.same_building(lost.node_key, found.node_key):
            add(f"same building ({ctx.campus.label(lost.node_key).split(',')[0]})", "positive", 0.5)
        elif mins <= 6:
            add(f"{mins:.0f} min walk apart", "positive", 0.35)
        elif mins > 15:
            add(f"{mins:.0f} min walk apart", "negative", 0.4)

    if feats.get("geo") is not None:
        label = distance_label(feats.get("geo_distance_m") or 0.0)
        if feats["geo"] >= 0.6:
            add(f"{label} apart (GPS)", "positive", 0.6)
        elif feats["geo"] <= 0.25:
            add(f"{label} apart (GPS)", "negative", 0.6)

    hours = feats.get("hours")
    if hours is not None and not feats.get("impossible"):
        add(f"found {_fmt_hours(hours)} after loss", "neutral", 0.3)
    elif feats.get("impossible"):
        add("found before it was lost", "negative", 1.0)

    misses = [f for f in CORE if feats.get(f) is None]
    if misses:
        add("not stated: " + ", ".join(misses), "neutral", 0.1)

    chips.sort(key=lambda c: -c["weight"])
    return chips
