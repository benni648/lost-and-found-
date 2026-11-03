import datetime as dt

import numpy as np

from lostfound.campus import get_campus
from lostfound.config import IMAGE_EMBEDDER, TEXT_EMBEDDER
from lostfound.features import MatchContext, score_pair
from lostfound.matching import Matcher
from lostfound.pipeline import build_database
from lostfound.store import Report, connect, insert_report, put_vector

NOW = dt.datetime(2026, 10, 26, 9, 0)


def _report(rid, kind, desc, attrs, node="library-f2", hours_ago=5.0, lost_window=2.0):
    created = NOW - dt.timedelta(hours=hours_ago)
    if kind == "lost":
        return Report(
            id=rid,
            kind="lost",
            created_at=(created + dt.timedelta(hours=1)).isoformat(),
            description=desc,
            attrs=attrs,
            node_key=node,
            lost_from=created.isoformat(),
            lost_to=(created + dt.timedelta(hours=lost_window)).isoformat(),
        )
    return Report(id=rid, kind="found", created_at=created.isoformat(), description=desc, attrs=attrs, node_key=node)


def _attrs(**kw):
    base = {
        "category": None,
        "brand": None,
        "model": None,
        "primary_color": None,
        "secondary_color": None,
        "material": None,
        "damages": [],
        "marks": [],
        "visible_text": [],
        "confidence": {},
    }
    base.update(kw)
    return base


def _ctx():
    campus = get_campus()
    return MatchContext(campus, {}, {}, {}, {}, 100)


def test_model_number_decides_between_lookalikes():
    ctx = _ctx()
    lost = _report(
        "LR-1",
        "lost",
        "lost my black iPhone 13",
        _attrs(category="phone", brand="iPhone", model="13", primary_color="black"),
    )
    right = _report(
        "FR-1",
        "found",
        "found a black iPhone 13",
        _attrs(category="phone", brand="iPhone", model="13", primary_color="black"),
        hours_ago=4,
    )
    wrong = _report(
        "FR-2",
        "found",
        "found a black iPhone 12",
        _attrs(category="phone", brand="iPhone", model="12", primary_color="black"),
        hours_ago=4,
    )
    a = score_pair(lost, right, ctx)["prob"]
    b = score_pair(lost, wrong, ctx)["prob"]
    assert a > b + 0.1, (a, b)


def test_missing_fields_do_not_penalise_a_terse_report():
    ctx = _ctx()
    lost = _report("LR-2", "lost", "black bottle", _attrs(category="bottle", primary_color="black"))
    found = _report(
        "FR-3",
        "found",
        "black Milton steel bottle, dented",
        _attrs(category="bottle", brand="Milton", primary_color="black", material="steel", damages=["dented"]),
        hours_ago=3,
    )
    scored = score_pair(lost, found, ctx)
    assert scored["prob"] > 0.6
    assert scored["evidence_weight"] < 1.0  # some evidence was absent, and that is fine


def test_found_before_lost_is_suppressed():
    ctx = _ctx()
    lost = _report("LR-3", "lost", "black wallet", _attrs(category="wallet", primary_color="black"))
    impossible = _report(
        "FR-4", "found", "black wallet", _attrs(category="wallet", primary_color="black"), hours_ago=50
    )
    scored = score_pair(lost, impossible, ctx)
    assert "found_before_lost" in scored["flags"]
    assert scored["prob"] < 0.35


def test_category_mismatch_is_suppressed():
    ctx = _ctx()
    lost = _report("LR-4", "lost", "black wallet", _attrs(category="wallet", primary_color="black"))
    other = _report("FR-5", "found", "black headphones", _attrs(category="earbuds", primary_color="black"), hours_ago=2)
    assert score_pair(lost, other, ctx)["prob"] < 0.25


def test_decisions_cover_match_review_and_no_match():
    con = connect(":memory:")
    matcher = Matcher(con, _ctx(), {}, [], [])

    class R:
        def __init__(self, p):
            self.prob = p

    assert matcher.decide([R(0.9), R(0.5)])["decision"] == "match"
    assert matcher.decide([R(0.9), R(0.88)])["decision"] == "review"
    assert matcher.decide([R(0.42)])["decision"] == "review"
    assert matcher.decide([R(0.1)])["decision"] == "no_match"


def test_alert_and_floor_settings_change_decisions(monkeypatch):
    from lostfound.settings import settings

    con = connect(":memory:")
    matcher = Matcher(con, _ctx(), {}, [], [])

    class R:
        def __init__(self, p):
            self.prob = p

    monkeypatch.setattr(settings, "plausible_floor", 0.2)
    monkeypatch.setattr(settings, "prob_alert", 0.8)
    assert matcher.decide([R(0.7)])["decision"] == "review"
    assert matcher.decide([R(0.1)])["decision"] == "no_match"


def test_incremental_upsert_and_remove_match_a_full_rebuild():
    con = connect(":memory:")
    existing = _report("FR-existing", "found", "black wallet", _attrs(category="wallet", primary_color="black"))
    insert_report(con, existing)
    put_vector(con, existing.id, "text", TEXT_EMBEDDER, np.array([1.0, 0.0], dtype=np.float32))
    con.commit()
    matcher = Matcher.load(con)

    replacement = _report(
        existing.id,
        "lost",
        "blue phone",
        _attrs(category="phone", primary_color="blue"),
    )
    insert_report(con, replacement)
    put_vector(con, replacement.id, "text", TEXT_EMBEDDER, np.array([0.0, 1.0], dtype=np.float32))
    put_vector(con, replacement.id, "image", IMAGE_EMBEDDER, np.array([0.5, 0.5], dtype=np.float32))
    con.commit()
    matcher.upsert(replacement)

    rebuilt = Matcher.load(con)
    assert matcher.by_id == rebuilt.by_id
    assert [report.id for report in matcher.found] == [report.id for report in rebuilt.found]
    assert [report.id for report in matcher.lost] == [report.id for report in rebuilt.lost]
    assert matcher.ctx.category_freq == rebuilt.ctx.category_freq
    assert matcher.ctx.colour_category_freq == rebuilt.ctx.colour_category_freq
    assert matcher.ctx.total == rebuilt.ctx.total
    np.testing.assert_array_equal(matcher.ctx.text_vecs[replacement.id], rebuilt.ctx.text_vecs[replacement.id])
    np.testing.assert_array_equal(matcher.ctx.image_vecs[replacement.id], rebuilt.ctx.image_vecs[replacement.id])

    matcher.remove(replacement.id)
    con.execute("DELETE FROM vectors WHERE report_id = ?", (replacement.id,))
    con.execute("DELETE FROM reports WHERE id = ?", (replacement.id,))
    con.commit()
    rebuilt_after_remove = Matcher.load(con)
    assert matcher.by_id == rebuilt_after_remove.by_id
    assert matcher.ctx.category_freq == rebuilt_after_remove.ctx.category_freq
    assert matcher.ctx.colour_category_freq == rebuilt_after_remove.ctx.colour_category_freq
    assert matcher.ctx.total == rebuilt_after_remove.ctx.total
    assert replacement.id not in matcher.ctx.text_vecs
    assert replacement.id not in matcher.ctx.image_vecs
    con.close()


def test_pending_review_upsert_removes_existing_report_from_candidate_pools():
    con = connect(":memory:")
    report = _report("FR-review", "found", "wallet", _attrs(category="wallet"))
    insert_report(con, report)
    con.commit()
    matcher = Matcher.load(con)
    report.status = "pending_review"

    matcher.upsert(report)

    assert report.id not in matcher.by_id
    assert not matcher.found
    assert matcher.ctx.total == 0
    con.close()


def test_request_matcher_view_shares_snapshot_and_copy_on_write_isolates_updates():
    con = connect(":memory:")
    report = _report("FR-cached", "found", "wallet", _attrs(category="wallet"))
    report.category = "wallet"
    insert_report(con, report)
    con.commit()
    cached = Matcher.load(con)
    request_view = cached.copy_for_connection(con)
    replacement = _report("LR-request", "lost", "phone", _attrs(category="phone"))
    replacement.category = "phone"

    assert request_view.by_id is cached.by_id
    assert request_view.found is cached.found
    assert request_view.ctx is cached.ctx

    update_snapshot = cached.copy_for_update(con)
    update_snapshot.upsert(replacement)

    assert update_snapshot.by_id is not cached.by_id
    assert update_snapshot.found is not cached.found
    assert update_snapshot.ctx is not cached.ctx
    assert replacement.id in update_snapshot.by_id
    assert replacement.id not in cached.by_id
    assert cached.ctx.category_freq == {"wallet": 1}
    con.close()


def test_end_to_end_baseline(tmp_path):
    db = tmp_path / "test.db"
    build_database(db_path=db, n_items=70, seed=3, render_images=False, verbose=False)
    con = connect(db)
    matcher = Matcher.load(con)
    from lostfound.eval import run_eval

    results = run_eval(matcher, save_csv=False)
    assert results["queries"] > 20
    assert results["recall@10"] >= 0.8
    assert results["recall@1"] >= 0.45
