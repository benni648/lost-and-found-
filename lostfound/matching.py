"""The matcher: rank candidates and *decide*.

Two decisions matter more than the ranking itself:

1. ``match``   -- one candidate is clearly ahead of the field.
2. ``review``  -- something scored well but either the confidence is below the
                  alert threshold or the runner-up is nearly as good. Staff get
                  the shortlist instead of a guess.
3. ``no_match``-- nothing plausible. The system abstains.

Abstention is a margin test *and* a threshold test, because the worst failure
mode in this domain is a confident wrong handover.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from . import calibration, rerank, store
from .campus import get_campus
from .config import IMAGE_EMBEDDER, TEXT_EMBEDDER
from .features import MatchContext, explain, score_pair
from .objects import media_url
from .retrieval import ann_candidate_ids
from .store import Report
from .timeutil import iso_utc, now_utc


@dataclass
class Ranked:
    candidate: Report
    score: float
    prob: float
    feats: dict
    flags: list[str]
    reasons: list[dict]
    # pre-calibration probability and the scorer that produced it, so a
    # decision can always be traced back to a model version
    raw_prob: float = 0.0
    scorer: str = rerank.POSE_SCORER

    def as_dict(self) -> dict:
        return {
            "candidate_id": self.candidate.id,
            "kind": self.candidate.kind,
            "prob": self.prob,
            "raw_prob": self.raw_prob,
            "scorer": self.scorer,
            "score": self.score,
            "flags": self.flags,
            "reasons": self.reasons,
            "feats": self.feats,
            "description": self.candidate.description,
            "category": self.candidate.category,
            "location": self.candidate.location_note,
            "image_path": self.candidate.image_path,
            "image_url": media_url(self.candidate.image_path),
            "created_at": self.candidate.created_at,
            "campus_timezone": self.candidate.campus_timezone,
        }


class Matcher:
    def __init__(
        self,
        con,
        ctx: MatchContext,
        by_id: dict[str, Report],
        found: list[Report],
        lost: list[Report],
        campus_id: str | None = None,
    ):
        self.con = con
        self.ctx = ctx
        self.by_id = by_id
        self.found = found
        self.lost = lost
        self.campus_id = campus_id

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, con, campus=None, campus_id: str | None = None) -> Matcher:
        reports = store.list_reports(con, campus_id=campus_id)
        by_id = {r.id: r for r in reports}
        campus = campus or get_campus()
        ctx = MatchContext.build(
            campus,
            store.load_vectors(con, "text", TEXT_EMBEDDER, campus_id=campus_id),
            store.load_vectors(con, "image", IMAGE_EMBEDDER, campus_id=campus_id),
            reports,
        )
        found = [r for r in reports if r.kind == "found"]
        lost = [r for r in reports if r.kind == "lost"]
        return cls(con, ctx, by_id, found, lost, campus_id)

    def copy_for_connection(self, con) -> Matcher:
        """Create an O(1) request view over an immutable matcher snapshot."""
        return Matcher(con, self.ctx, self.by_id, self.found, self.lost, self.campus_id)

    def copy_for_update(self, con=None) -> Matcher:
        """Create a copy-on-write snapshot before applying report changes."""
        ctx = MatchContext(
            self.ctx.campus,
            self.ctx.text_vecs.copy(),
            self.ctx.image_vecs.copy(),
            Counter(self.ctx.category_freq),
            Counter(self.ctx.colour_category_freq),
            self.ctx.total,
        )
        return Matcher(con or self.con, ctx, self.by_id.copy(), self.found.copy(), self.lost.copy(), self.campus_id)

    def upsert(self, report: Report, *, con=None) -> None:
        """Incrementally replace one report and its vectors in the matcher."""
        if self.campus_id is not None and report.campus_id != self.campus_id:
            raise ValueError("cannot add a report from another campus to this matcher")
        self.remove(report.id)
        if report.status == "pending_review":
            return

        self.by_id[report.id] = report
        self.ctx.add_report(report)
        (self.found if report.kind == "found" else self.lost).append(report)

        vector_connection = con or self.con
        text_vector = store.get_vector(vector_connection, report.id, "text", TEXT_EMBEDDER)
        image_vector = store.get_vector(vector_connection, report.id, "image", IMAGE_EMBEDDER)
        if text_vector is not None:
            self.ctx.text_vecs[report.id] = text_vector
        if image_vector is not None:
            self.ctx.image_vecs[report.id] = image_vector

    def remove(self, report_id: str) -> None:
        """Remove one report, its cached vectors, and its context frequencies."""
        report = self.by_id.pop(report_id, None)
        if report is None:
            return

        self.found[:] = [item for item in self.found if item.id != report_id]
        self.lost[:] = [item for item in self.lost if item.id != report_id]
        self.ctx.text_vecs.pop(report_id, None)
        self.ctx.image_vecs.pop(report_id, None)
        self.ctx.remove_report(report)

    # ------------------------------------------------------------------ rank
    def rank(self, query: Report, top_k: int = 5, pool: list[Report] | None = None) -> list[Ranked]:
        if pool is None:
            pool = self.found if query.kind == "lost" else self.lost
        if self.campus_id is not None:
            pool = [candidate for candidate in pool if candidate.campus_id == self.campus_id]
        if getattr(getattr(self.con, "dialect", None), "name", None) == "postgresql":
            query_vectors = {
                model: vector
                for model, vector_map in (
                    (TEXT_EMBEDDER, self.ctx.text_vecs),
                    (IMAGE_EMBEDDER, self.ctx.image_vecs),
                )
                if (vector := vector_map.get(query.id)) is not None
            }
            if query_vectors:
                candidate_ids = ann_candidate_ids(
                    self.con,
                    query_vectors=query_vectors,
                    candidate_kind="found" if query.kind == "lost" else "lost",
                    campus_id=self.campus_id,
                )
                pool = [candidate for candidate in pool if candidate.id in candidate_ids]
        # Resolve the served models once per query, not once per candidate.
        # An absent artifact means the hand-set scorer and the raw sigmoid,
        # which is the documented fallback for CI and fresh installs.
        learned = rerank.load()
        calibrator = calibration.load()
        out: list[Ranked] = []
        for cand in pool:
            if cand.id == query.id:
                continue
            lost, found = (query, cand) if query.kind == "lost" else (cand, query)
            s = score_pair(lost, found, self.ctx)
            scorer = rerank.POSE_SCORER
            raw_prob = s["prob"]
            calibration_input: float | None = None
            if learned is not None:
                raw_prob = learned.prob(s["feats"], s["flags"], s["evidence_weight"])
                scorer = learned.scorer
                if calibrator is not None and calibrator.input_scale == "logit" and calibrator.matches(scorer):
                    # The map was fitted on logits; feeding it the saturated
                    # probability would throw away the ranker's resolution.
                    calibration_input = learned.logit(rerank.design(s["feats"], s["flags"], s["evidence_weight"]))
            prob = raw_prob
            if calibrator is not None and calibrator.matches(scorer):
                prob = calibrator.apply(calibration_input if calibration_input is not None else raw_prob)
            out.append(
                Ranked(
                    cand,
                    s["score"],
                    prob,
                    s["feats"],
                    s["flags"],
                    explain(lost, found, self.ctx, s),
                    raw_prob=raw_prob,
                    scorer=scorer,
                )
            )
        # raw_prob breaks ties so a calibrated value that saturates can never
        # reorder the shortlist by insertion order
        out.sort(key=lambda r: (-r.prob, -r.raw_prob, -r.score))
        return out[:top_k]

    def decide(self, ranked: list[Ranked]) -> dict:
        if not ranked:
            return {"decision": "no_match", "confidence": 0.0, "margin": 0.0, "reason": "empty pool"}
        top = ranked[0]
        margin = top.prob - (ranked[1].prob if len(ranked) > 1 else 0.0)
        # Thresholds live with the scorer: a probability from a learned +
        # calibrated scorer is on a different scale from the hand-set sigmoid.
        effective = calibration.operating_point(getattr(top, "scorer", rerank.POSE_SCORER))
        floor = effective["plausible_floor"]
        alert_at = effective["prob_alert"]
        margin_min = effective["margin_min"]
        if top.prob < floor:
            return {
                "decision": "no_match",
                "confidence": top.prob,
                "margin": round(margin, 3),
                "reason": "best candidate is below the plausible-match floor",
            }
        if top.prob < alert_at:
            return {
                "decision": "review",
                "confidence": top.prob,
                "margin": round(margin, 3),
                "reason": "below the auto-alert threshold; shortlist sent to staff",
            }
        if margin < margin_min:
            return {
                "decision": "review",
                "confidence": top.prob,
                "margin": round(margin, 3),
                "reason": "two candidates are nearly tied; staff must choose",
            }
        return {
            "decision": "match",
            "confidence": top.prob,
            "margin": round(margin, 3),
            "reason": "clear leader on visual, attribute, time and location evidence",
        }

    def snapshot(self, a_id: str, b_id: str) -> dict:
        """Score one pair in either direction, for storing with staff feedback."""
        a, b = self.by_id.get(a_id), self.by_id.get(b_id)
        if a is None or b is None:
            raise KeyError(f"unknown report: {a_id if a is None else b_id}")
        lost, found = (a, b) if a.kind == "lost" else (b, a)
        return {"lost_id": lost.id, "found_id": found.id, **score_pair(lost, found, self.ctx)}

    # ----------------------------------------------------------------- query
    def match(self, query_id: str, top_k: int = 5, save: bool = True) -> dict:
        query = self.by_id.get(query_id) or store.get_report(self.con, query_id, campus_id=self.campus_id)
        if query is None:
            raise KeyError(f"no such report: {query_id}")
        if query.status == "pending_review":
            raise KeyError(f"report is pending moderation review: {query_id}")
        ranked = self.rank(query, top_k=top_k)
        decision = self.decide(ranked)
        result = {
            "query": {
                "id": query.id,
                "kind": query.kind,
                "description": query.description,
                "category": query.category,
                "location": query.location_note,
                "image_path": query.image_path,
                "image_url": media_url(query.image_path),
                "created_at": query.created_at,
                "campus_timezone": query.campus_timezone,
                "attrs": query.attrs,
            },
            **decision,
            "scorer": ranked[0].scorer if ranked else rerank.POSE_SCORER,
            "feedback": store.feedback_for_query(self.con, query.id, campus_id=self.campus_id),
            "matches": [r.as_dict() for r in ranked],
        }
        if save and ranked:
            store.save_matches(
                self.con,
                query.id,
                [r.as_dict() for r in ranked],
                iso_utc(now_utc()),
                decision=decision["decision"],
            )
            self.con.commit()
        return result

    def match_all(self, top_k: int = 5) -> int:
        n = 0
        for q in self.lost:
            self.match(q.id, top_k=top_k)
            n += 1
        return n
