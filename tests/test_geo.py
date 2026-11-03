import datetime as dt

import pytest

from lostfound.campus import get_campus
from lostfound.features import MatchContext, explain, measure
from lostfound.geo import distance_label, geo_similarity, haversine_m
from lostfound.store import Report

NOW = dt.datetime(2026, 10, 26, 9, 0)
CANTEEN = (31.2571, 75.7074)


def _ctx():
    return MatchContext(get_campus(), {}, {}, {}, {}, 100)


def _report(rid, kind, latitude, longitude):
    created = NOW - dt.timedelta(hours=3)
    lost_window = {
        "lost_from": created.isoformat(),
        "lost_to": (created + dt.timedelta(hours=2)).isoformat(),
    }
    return Report(
        id=rid,
        kind=kind,
        created_at=(created + dt.timedelta(hours=1)).isoformat(),
        description="black wallet",
        attrs={"category": "wallet", "primary_color": "black"},
        node_key="library-f2",
        latitude=latitude,
        longitude=longitude,
        **(lost_window if kind == "lost" else {}),
    )


def test_haversine_one_degree_at_the_equator():
    assert haversine_m(0.0, 0.0, 0.0, 1.0) == pytest.approx(111_195, rel=0.001)


def test_haversine_is_symmetric_and_zero_for_the_same_point():
    other = (31.2610, 75.7100)
    assert haversine_m(*CANTEEN, *other) == pytest.approx(haversine_m(*other, *CANTEEN))
    assert haversine_m(*CANTEEN, *CANTEEN) == 0.0


def test_geo_similarity_decays_and_stays_in_range():
    values = [geo_similarity(d) for d in (0, 15, 50, 250, 1000, 5000)]
    assert values[0] == 1.0
    assert values == sorted(values, reverse=True)
    assert all(0.0 <= value <= 1.0 for value in values)


def test_distance_label_switches_to_km():
    assert distance_label(42) == "42 m"
    assert distance_label(1250) == "1.2 km"


def test_gps_distance_is_measured_when_both_reports_are_pinned():
    ctx = _ctx()
    lost = _report("LR-geo", "lost", *CANTEEN)
    near = _report("FR-near", "found", 31.2576, 75.7079)  # ~55 m away
    far = _report("FR-far", "found", 31.2750, 75.7250)  # ~2.5 km away

    near_feats = measure(lost, near, ctx)
    far_feats = measure(lost, far, ctx)

    assert near_feats["geo"] is not None and far_feats["geo"] is not None
    assert near_feats["geo"] > far_feats["geo"]
    assert near_feats["geo_distance_m"] < far_feats["geo_distance_m"]
    assert any("GPS" in chip["label"] for chip in explain(lost, near, ctx))
    assert any("GPS" in chip["label"] for chip in explain(lost, far, ctx))


def test_geo_is_masked_out_when_one_side_has_no_pin():
    ctx = _ctx()
    lost = _report("LR-geo", "lost", *CANTEEN)
    unpinned = Report(
        id="FR-nogeo",
        kind="found",
        created_at=NOW.isoformat(),
        description="black wallet",
        attrs={"category": "wallet", "primary_color": "black"},
        node_key="library-f2",
    )

    feats = measure(lost, unpinned, ctx)

    assert feats["geo"] is None
    assert feats["geo_distance_m"] is None
