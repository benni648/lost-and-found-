from fastapi.testclient import TestClient

from lostfound import calibration, rerank
from lostfound.api import create_app
from lostfound.calibration import CalibrationParams
from lostfound.pipeline import build_database
from lostfound.settings import settings


def _client(tmp_path, monkeypatch):
    db = tmp_path / "model-info.db"
    build_database(db_path=db, n_items=20, seed=11, render_images=False, verbose=False)
    monkeypatch.setattr(settings, "calibration_file", tmp_path / "params.json")
    monkeypatch.setattr(settings, "reranker_file", tmp_path / "model.json")
    calibration.clear_cache()
    rerank.clear_cache()
    return TestClient(create_app(db))


def test_model_info_reports_the_handset_fallbacks(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        body = client.get("/api/model/info").json()

    assert body["scorer"] == rerank.POSE_SCORER
    assert body["reranker"] is None
    assert body["calibration"] is None
    assert set(body["encoders"]) == {"text", "image"}
    assert body["encoders"]["text"]["name"] == settings.text_embedder
    assert body["encoders"]["image"]["dim"] > 0
    assert body["stale_vector_models"] == {}
    assert body["operating_point"]["prob_alert"] == settings.prob_alert


def test_model_info_reflects_a_fitted_calibration(tmp_path, monkeypatch):
    client_ctx = _client(tmp_path, monkeypatch)
    calibration.save(
        CalibrationParams(
            method="platt",
            version="platt-test",
            a=1.4,
            b=-0.1,
            fit_size=42,
            prob_alert=0.42,
            plausible_floor=0.2,
        ),
        tmp_path / "params.json",
    )
    calibration.clear_cache()
    with client_ctx as client:
        body = client.get("/api/model/info").json()
        served = client.get("/api/config/operating-point").json()

    assert body["calibration"]["version"] == "platt-test"
    assert body["calibration"]["method"] == "platt"
    assert body["calibration"]["scorer"] == rerank.POSE_SCORER
    assert body["calibration"]["fit_size"] == 42
    # the fitted thresholds replace the configured defaults, and the UI bands
    # follow the same scale
    assert body["operating_point"]["prob_alert"] == 0.42
    assert served["prob_alert"] == 0.42
    assert served["calibration_version"] == "platt-test"
    assert served["bands"]["probable"] == 0.42
    assert served["bands"]["weak"] <= served["bands"]["probable"] <= served["bands"]["strong"]
