import json
from types import SimpleNamespace

import numpy as np

from lostfound import calibration


def test_ece_is_zero_when_bins_are_perfectly_calibrated():
    probs = [0.2] * 100 + [0.8] * 100
    labels = [0] * 80 + [1] * 20 + [0] * 20 + [1] * 80
    assert calibration.expected_calibration_error(probs, labels, bins=10) == 0.0


def test_ece_is_positive_for_an_overconfident_model():
    probs = [0.9] * 100
    labels = [0] * 50 + [1] * 50
    assert calibration.expected_calibration_error(probs, labels) == 0.4


def test_brier_score_matches_the_textbook_definition():
    assert calibration.brier_score([0.0, 1.0], [0, 1]) == 0.0
    assert calibration.brier_score([0.5, 0.5], [0, 1]) == 0.25


def test_platt_fit_is_monotone_and_improves_ece_on_underconfident_scores():
    rng = np.random.default_rng(7)
    probs = rng.uniform(0.05, 0.95, 4000)
    # truth fires far more often than the raw sigmoid claims
    labels = (rng.uniform(size=probs.size) < np.clip(probs**0.4, 0.01, 0.99)).astype(int)
    a, b = calibration.fit_platt(probs, labels)
    assert a > 0

    params = calibration.CalibrationParams(method="platt", version="t", a=a, b=b)
    probe = params.apply_many([0.1, 0.4, 0.7, 0.9])
    assert probe == sorted(probe)
    assert all(0.0 < p < 1.0 for p in probe)
    mapped = params.apply_many(probs)
    assert calibration.expected_calibration_error(mapped, labels) < calibration.expected_calibration_error(
        probs, labels
    )


def test_logit_fit_keeps_resolution_when_the_ranker_saturates():
    """A ranker whose own sigmoid is pinned near 1.0 still needs a usable map.

    Fitting Platt on probabilities recovered from that sigmoid collapses to a
    constant (the bug this path exists to prevent); fitting the logit keeps
    the ordering the ranker really has.
    """
    logits = np.linspace(-2.0, 6.0, 400)
    labels = (logits > 2.0).astype(int)
    a, b = calibration.fit_platt_logit(logits, labels)
    assert a > 0

    params = calibration.CalibrationParams(method="platt", version="t", a=a, b=b, input_scale="logit")
    mapped = params.apply_many(logits.tolist())
    assert mapped == sorted(mapped)
    assert mapped[-1] - mapped[0] > 0.5
    assert calibration.expected_calibration_error(mapped, labels) < 0.15


def test_input_scale_round_trips_through_disk(tmp_path):
    params = calibration.CalibrationParams(method="platt", version="t", a=2.0, b=-1.0, input_scale="logit")
    path = calibration.save(params, tmp_path / "params.json")
    loaded = calibration.load(path)
    assert loaded is not None
    assert loaded.input_scale == "logit"
    assert loaded.apply(3.0) == params.apply(3.0)


def test_isotonic_fit_is_a_monotone_staircase():
    probs = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    labels = [0, 0, 0, 1, 0, 1, 1, 1, 1]
    knots = calibration.fit_isotonic(probs, labels)
    assert knots
    y_values = [y for _, y in knots]
    assert y_values == sorted(y_values)
    params = calibration.CalibrationParams(method="isotonic", version="t", knots=knots)
    mapped = params.apply_many([0.05, 0.35, 0.85])
    assert mapped == sorted(mapped)
    assert mapped[0] <= mapped[1] <= mapped[2]


def test_one_class_fit_stays_at_identity():
    assert calibration.fit_platt([0.2, 0.5, 0.9], [1, 1, 1]) == (1.0, 0.0)


def test_missing_params_file_is_the_identity_map(tmp_path, monkeypatch):
    # Isolate the default path: a generated ml/calibration/params.json in the
    # working tree must not change what "no calibration" means here.
    calibration.clear_cache()
    monkeypatch.setattr(calibration, "settings", SimpleNamespace(calibration_path=tmp_path / "absent.json"))
    assert calibration.load() is None
    assert calibration.calibrate_prob(0.42, None) == 0.42


def test_params_round_trip_through_disk(tmp_path):
    params = calibration.CalibrationParams(
        method="platt",
        version="platt-test",
        scorer="rerank:v1",
        a=1.7,
        b=-0.2,
        fit_size=10,
        holdout_size=4,
        metrics={"calibrated": {"ece": 0.02}},
    )
    path = calibration.save(params, tmp_path / "params.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["method"] == "platt"

    calibration.clear_cache()
    loaded = calibration.load(path)
    assert loaded is not None
    assert (loaded.a, loaded.b, loaded.scorer) == (1.7, -0.2, "rerank:v1")
    assert loaded.apply(0.5) == params.apply(0.5)


def test_calibration_refuses_a_different_scorer():
    params = calibration.CalibrationParams(method="platt", version="t", scorer="rerank:v1")
    assert params.matches("rerank:v1")
    assert not params.matches("handset")


def test_operating_point_only_moves_for_the_scorer_it_was_fitted_on(tmp_path, monkeypatch):
    from lostfound.settings import settings

    monkeypatch.setattr(settings, "calibration_file", tmp_path / "params.json")
    calibration.clear_cache()
    assert calibration.operating_point("handset")["prob_alert"] == settings.prob_alert

    calibration.save(
        calibration.CalibrationParams(
            method="platt",
            version="platt-v1",
            scorer="rerank:v1",
            prob_alert=0.42,
            plausible_floor=0.2,
        ),
        tmp_path / "params.json",
    )
    calibration.clear_cache()
    reranked = calibration.operating_point("rerank:v1")
    assert reranked["prob_alert"] == 0.42
    assert reranked["plausible_floor"] == 0.2
    assert reranked["calibration_version"] == "platt-v1"
    assert calibration.operating_point("handset")["prob_alert"] == settings.prob_alert
