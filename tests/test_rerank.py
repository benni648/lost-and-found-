import numpy as np
import pytest

from lostfound import rerank
from lostfound.training import Shortlist, derive_operating_point, split_shortlists


def _pair(colour: float, text: float = 0.5) -> dict:
    return {
        "category": 1.0,
        "sex": None,
        "colour": colour,
        "text": text,
        "image": colour,
        "model": None,
        "serial": None,
        "brand": None,
        "time": 0.5,
        "location": 0.5,
        "marks": None,
        "material": None,
        "damages": None,
        "rarity": 0.4,
        "token_overlap": text,
    }


def _groups(count: int = 40) -> list[list[tuple[np.ndarray, int]]]:
    rng = np.random.default_rng(3)
    groups = []
    for _ in range(count):
        noise = rng.uniform(0.0, 0.2)
        positive = rerank.design(_pair(1.0), [], 0.8)
        negatives = [rerank.design(_pair(float(noise), float(noise)), [], 0.5) for _ in range(3)]
        groups.append([(positive, 1), *[(neg, 0) for neg in negatives]])
    return groups


def test_design_matches_the_declared_feature_layout():
    vector = rerank.design(_pair(0.5), ["model_mismatch"], 0.75)
    assert vector.shape == (len(rerank.feature_names()),)
    assert rerank.feature_names() == [
        *rerank.FEATURES,
        "evidence_weight",
        *[f"flag_{name}" for name in rerank.FLAGS],
        *[f"missing_{name}" for name in rerank.MISSING_CORE],
    ]


def test_design_marks_missing_evidence_separately_from_a_zero():
    stated = rerank.design(_pair(0.0), [], 0.5)
    missing = rerank.design({**_pair(0.0), "colour": None}, [], 0.5)
    colour_index = rerank.feature_names().index("colour")
    missing_index = rerank.feature_names().index("missing_colour")
    assert stated[colour_index] == 0.0 and missing[colour_index] == 0.0
    assert stated[missing_index] == 0.0
    assert missing[missing_index] == 1.0


def test_fit_ranks_a_positive_above_its_negatives():
    model = rerank.fit(_groups(), epochs=120, seed=0)
    positive = model.prob(_pair(1.0), [], 0.8)
    negative = model.prob(_pair(0.0), [], 0.5)
    assert positive > negative
    assert 0.0 < negative < positive < 1.0


def test_fit_requires_a_positive_example():
    with pytest.raises(ValueError):
        rerank.fit([[(rerank.design(_pair(1.0)), 0)]])


def test_artifact_round_trip_and_layout_guard(tmp_path):
    model = rerank.fit(_groups(10), epochs=40, seed=1)
    path = rerank.save(model, tmp_path / "model.json")
    rerank.clear_cache()
    loaded = rerank.load(path)
    assert loaded is not None
    assert loaded.version == model.version
    assert loaded.prob(_pair(1.0), [], 0.8) == pytest.approx(model.prob(_pair(1.0), [], 0.8))

    payload = model.as_dict()
    payload["features"] = ["colour"]
    with pytest.raises(ValueError):
        rerank.ReRankModel.from_dict(payload)


def test_missing_artifact_keeps_the_handset_scorer(tmp_path):
    rerank.clear_cache()
    assert rerank.load(tmp_path / "absent.json") is None


def test_holdout_split_never_shares_a_query():
    shortlists = [
        Shortlist(query_id=f"LR-{i:04d}", category="bottle", items=[{"label": 1}, {"label": 0}]) for i in range(20)
    ]
    train, test = split_shortlists(shortlists, holdout=0.25, seed=5)
    train_ids = {s.query_id for s in train}
    test_ids = {s.query_id for s in test}
    assert train_ids and test_ids
    assert not train_ids & test_ids
    assert len(train_ids) + len(test_ids) == 20


def test_derived_operating_point_meets_the_precision_budget():
    shortlists = [
        Shortlist(
            query_id=f"q{i}",
            category=None,
            items=[{"label": 1 if i < 8 else 0, "p": 0.9}, {"label": 0, "p": 0.2}],
        )
        for i in range(10)
    ]
    op = derive_operating_point(shortlists, lambda item: item["p"], target_precision=0.8)
    assert op["prob_alert"] == 0.9
    assert op["plausible_floor"] <= op["prob_alert"]
    assert op["queries"] == 10


def test_a_stricter_precision_budget_never_lowers_the_threshold():
    tops = [(0.95, 1), (0.9, 1), (0.85, 0), (0.8, 1), (0.7, 0), (0.6, 0)]
    shortlists = [
        Shortlist(query_id=f"q{i}", category=None, items=[{"label": label, "p": prob}])
        for i, (prob, label) in enumerate(tops)
    ]
    loose = derive_operating_point(shortlists, lambda item: item["p"], target_precision=0.66)
    strict = derive_operating_point(shortlists, lambda item: item["p"], target_precision=1.0)
    assert loose["prob_alert"] is not None and strict["prob_alert"] is not None
    assert strict["prob_alert"] >= loose["prob_alert"]


def test_holdout_split_rejects_impossible_fractions():
    shortlists = [Shortlist(query_id="a", category=None, items=[{"label": 1}]) for _ in range(4)]
    with pytest.raises(ValueError):
        split_shortlists(shortlists, holdout=0.0)
