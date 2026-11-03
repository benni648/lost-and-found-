import contextlib
import sqlite3
import struct
import time
import zlib
from io import BytesIO
from types import SimpleNamespace

from fastapi.testclient import TestClient
from PIL import Image

from lostfound import api, calibration, rerank
from lostfound.api import create_app
from lostfound.config import TEXT_EMBEDDER
from lostfound.pipeline import build_database
from lostfound.ratelimit import RateLimiter
from lostfound.settings import settings
from lostfound.store import Report, connect, get_vector, insert_report


def _settings_with_calibration_path(path):
    """A settings stand-in that only redirects where calibration is loaded from."""
    return SimpleNamespace(
        calibration_path=path,
        plausible_floor=settings.plausible_floor,
        prob_alert=settings.prob_alert,
        margin_min=settings.margin_min,
    )


def _png(colour, size=(320, 320)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, colour).save(buf, "PNG")
    return buf.getvalue()


def _oversized_png_header(width=6500, height=6500) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        contents = kind + data
        return struct.pack(">I", len(data)) + contents + struct.pack(">I", zlib.crc32(contents))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\0")) + chunk(b"IEND", b"")


def _client(tmp_path):
    db = tmp_path / "api.db"
    build_database(db_path=db, n_items=60, seed=5, render_images=False, verbose=False)
    return TestClient(create_app(db))


def test_health_and_campus(tmp_path):
    with _client(tmp_path) as client:
        health = client.get("/api/health").json()
        assert health["ok"] and health["reports"] > 100

        campus = client.get("/api/campus").json()
        assert len(campus["campus"]["buildings"]) == 19
        assert len(campus["campus"]["nodes"]) >= 30
        assert campus["campus"]["map_center"] == [31.25327, 75.70324]
        lpu_places = [b for b in campus["campus"]["buildings"] if b["latitude"] is not None]
        assert len(lpu_places) == 3


def test_api_reuses_cached_matcher_snapshot_between_read_requests(tmp_path, monkeypatch):
    from lostfound.matching import Matcher

    db = tmp_path / "cached-matcher.db"
    build_database(db_path=db, n_items=30, seed=7, render_images=False, verbose=False)
    original_load = Matcher.load
    load_count = 0

    def counting_load(cls, con, campus=None, campus_id=None):
        nonlocal load_count
        load_count += 1
        return original_load(con, campus, campus_id=campus_id)

    monkeypatch.setattr(Matcher, "load", classmethod(counting_load))
    with TestClient(create_app(db)) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/reports", params={"limit": 5}).status_code == 200
        assert client.get("/api/public/stats").status_code == 200

    assert load_count == 1


def test_async_matching_enqueues_report_after_successful_creation(tmp_path, monkeypatch):
    database = tmp_path / "async-matching.db"
    build_database(db_path=database, n_items=10, seed=19, render_images=False, verbose=False)
    monkeypatch.setattr(settings, "async_matching", True)
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/0")

    class FakeQueue:
        def __init__(self, _redis_url):
            self.jobs = []

        def enqueue(self, name, payload):
            self.jobs.append((name, payload))

        def get_status(self, _report_id):
            return {"status": "queued", "updated_at": "1"}

    monkeypatch.setattr(api, "RedisJobQueue", FakeQueue)
    app = create_app(database, rate_limiter=RateLimiter(None, disabled=True))
    with TestClient(app) as client:
        response = client.post(
            "/api/reports",
            json={
                "kind": "lost",
                "description": "Lost a blue water bottle",
                "contact_email": "reporter@example.com",
            },
        )
        status_response = client.get(f"/api/reports/{response.json()['report']['id']}/status")

    assert response.status_code == 200
    assert response.json()["matching_status"] == "queued"
    assert app.state.match_queue.jobs == [("rematch_report", {"report_id": response.json()["report"]["id"]})]
    assert status_response.json()["status"] == "queued"
    con = connect(database, initialize=False)
    try:
        assert get_vector(con, response.json()["report"]["id"], "text", TEXT_EMBEDDER) is None
    finally:
        con.close()


def test_http_read_soak_reuses_one_matcher_snapshot(tmp_path, monkeypatch):
    from lostfound.matching import Matcher

    db = tmp_path / "matcher-soak.db"
    build_database(db_path=db, n_items=5, seed=13, render_images=False, verbose=False)
    original_load = Matcher.load
    load_count = 0

    def counting_load(cls, con, campus=None, campus_id=None):
        nonlocal load_count
        load_count += 1
        return original_load(con, campus, campus_id=campus_id)

    monkeypatch.setattr(Matcher, "load", classmethod(counting_load))
    with TestClient(create_app(db)) as client:
        for _ in range(5_000):
            response = client.get("/api/health")
            assert response.status_code == 200
        initial_report_count = response.json()["reports"]

        external_con = connect(db, initialize=False)
        try:
            insert_report(
                external_con,
                Report(
                    id="LR-generation-soak",
                    kind="lost",
                    created_at="2026-10-28T10:00:00Z",
                    description="lost a blue water bottle",
                    attrs={"category": "bottle", "primary_color": "blue"},
                    campus_id=settings.default_campus_id,
                ),
            )
            external_con.commit()
        finally:
            external_con.close()

        for _ in range(5_000):
            response = client.get("/api/health")
            assert response.status_code == 200
        assert response.json()["ok"] is True
        assert response.json()["reports"] == initial_report_count + 1

    assert load_count == 2


def test_reports_and_matching(tmp_path):
    with _client(tmp_path) as client:
        found = client.get("/api/reports", params={"kind": "found", "limit": 5}).json()
        assert found["count"] == 5
        assert all(r["kind"] == "found" for r in found["reports"])

        rid = found["reports"][0]["id"]
        result = client.post(f"/api/match/{rid}", params={"top": 3}).json()
        assert result["decision"] in {"match", "review", "no_match"}
        assert len(result["matches"]) <= 3
        assert result["matches"][0]["reasons"]


def test_report_cursor_pages_cover_each_report_once_and_cap_limits(tmp_path):
    with _client(tmp_path) as client:
        expected_report_count = client.get("/api/health").json()["reports"]
        oversized = client.get("/api/reports", params={"limit": 100000})
        assert oversized.status_code == 200
        assert oversized.json()["count"] == 100
        assert oversized.json()["next_cursor"]

        seen_ids = []
        cursor = None
        while True:
            params = {"limit": 37}
            if cursor:
                params["cursor"] = cursor
            page = client.get("/api/reports", params=params)
            assert page.status_code == 200, page.text
            body = page.json()
            seen_ids.extend(report["id"] for report in body["reports"])
            cursor = body["next_cursor"]
            if cursor is None:
                break

        assert len(seen_ids) == len(set(seen_ids))
        assert len(seen_ids) == expected_report_count


def test_report_pagination_rejects_malformed_cursor(tmp_path):
    with _client(tmp_path) as client:
        response = client.get("/api/reports", params={"cursor": "not-a-valid-cursor"})
        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid pagination cursor."


def test_create_report_then_match(tmp_path):
    with _client(tmp_path) as client:
        payload = {
            "kind": "lost",
            "description": "Lost my black Milton steel water bottle near the canteen, dented cap",
            "node_key": "canteen-f1",
            "contact_email": "reporter@example.com",
        }
        created = client.post("/api/reports", json=payload).json()["report"]
        assert created["category"] == "bottle"
        assert created["attrs"]["primary_color"] == "black"

        matched = client.post(f"/api/match/{created['id']}", params={"top": 5}).json()
        assert matched["query"]["id"] == created["id"]
        assert matched["matches"]


def test_create_report_at_verified_lpu_location(tmp_path):
    with _client(tmp_path) as client:
        created = client.post(
            "/api/reports",
            json={
                "kind": "lost",
                "description": "Lost a black wallet with a student card",
                "node_key": "lpu_technology-f1",
                "contact_email": "reporter@example.com",
            },
        ).json()["report"]
        assert created["node_key"] == "lpu_technology-f1"
        assert created["location"] == "Lovely Institute of Technology"


def test_create_found_report_with_live_location(tmp_path):
    with _client(tmp_path) as client:
        response = client.post(
            "/api/reports",
            json={
                "kind": "found",
                "description": "Found a black wallet",
                "latitude": 31.2571,
                "longitude": 75.7074,
                "location_accuracy_m": 18.5,
                "contact_phone": "+911234567890",
            },
        )
        assert response.status_code == 200
        report = response.json()["report"]
        assert report["latitude"] == 31.2571
        assert report["longitude"] == 75.7074
        assert report["location_accuracy_m"] == 18.5
        assert report["location"] == "Current location"


def test_existing_database_gets_live_location_columns(tmp_path):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as legacy:
        legacy.execute("CREATE TABLE reports (id TEXT PRIMARY KEY, kind TEXT, category TEXT)")

    con = connect(db)
    columns = {row["name"] for row in con.execute("PRAGMA table_info(reports)")}
    con.close()

    assert {"latitude", "longitude", "location_accuracy_m"} <= columns


def test_upload_photo_carries_the_image_through_matching(tmp_path):
    created_path = None
    try:
        with _client(tmp_path) as client:
            res = client.post(
                "/api/reports/upload",
                data={
                    "kind": "found",
                    "description": "Found a water bottle near the canteen",
                    "node_key": "canteen-f1",
                    "latitude": "31.2571",
                    "longitude": "75.7074",
                    "location_accuracy_m": "18.5",
                    "contact_email": "reporter@example.com",
                },
                files={"photo": ("bottle.png", _png((190, 30, 35)), "image/png")},
            )
            assert res.status_code == 200, res.text
            rep = res.json()["report"]
            created_path = rep["image_path"]

            assert rep["has_photo"] is True
            assert rep["latitude"] == 31.2571
            assert rep["longitude"] == 75.7074
            assert rep["location_accuracy_m"] == 18.5
            assert rep["is_synthetic_art"] is False
            assert rep["image_url"].startswith("/media/reports/") and ".jpg?" in rep["image_url"]
            # the text never stated a colour, so it came from the photo
            assert rep["attrs"]["primary_color"] == "red"
            assert client.get(rep["image_url"]).status_code == 200
            expires = int(rep["image_url"].split("expires=")[1].split("&", 1)[0])
            expired_url = rep["image_url"].replace(f"expires={expires}", f"expires={int(time.time()) - 1}")
            assert client.get(expired_url).status_code == 403

            matched = client.post(f"/api/match/{rep['id']}", params={"top": 3}).json()
            assert matched["matches"], "an uploaded report must be matchable"
            assert matched["query"]["image_url"].startswith("/media/reports/")
    finally:
        if created_path:
            from lostfound.objects import get_object_store

            get_object_store().delete(created_path)


def test_photo_only_report_is_accepted(tmp_path):
    created_path = None
    try:
        with _client(tmp_path) as client:
            res = client.post(
                "/api/reports/upload",
                data={"kind": "found", "contact_email": "photo@example.com"},
                files={"photo": ("item.png", _png((30, 90, 190)), "image/png")},
            )
            assert res.status_code == 200, res.text
            rep = res.json()["report"]
            created_path = rep["image_path"]

            assert rep["description"] == ""
            assert rep["has_photo"] is True
            # nothing was typed, so the colour must come from the photograph
            assert rep["attrs"]["primary_color"]
            matched = client.post(f"/api/match/{rep['id']}", params={"top": 3}).json()
            assert matched["query"]["description"] == ""
    finally:
        if created_path:
            from lostfound.objects import get_object_store

            get_object_store().delete(created_path)


def test_lost_report_accepts_a_last_seen_pin(tmp_path):
    # both kinds can be pinned: two points are what makes GPS distance usable
    with _client(tmp_path) as client:
        res = client.post(
            "/api/reports",
            json={
                "kind": "lost",
                "description": "lost a blue water bottle near the canteen",
                "latitude": 31.2571,
                "longitude": 75.7074,
                "location_accuracy_m": 15.0,
                "contact_email": "pinned@example.com",
            },
        )
        assert res.status_code == 200, res.text
        report = res.json()["report"]
        assert report["latitude"] == 31.2571
        assert report["longitude"] == 75.7074
        assert report["location_accuracy_m"] == 15.0


def test_json_report_still_requires_a_description(tmp_path):
    # the JSON endpoint carries no photo, so a description is mandatory there
    with _client(tmp_path) as client:
        res = client.post(
            "/api/reports",
            json={"kind": "lost", "description": "", "contact_email": "photo@example.com"},
        )
        assert res.status_code == 422


def test_up_to_four_photos_are_stored_and_pooled(tmp_path):
    from lostfound.objects import get_object_store

    created: list[str] = []
    try:
        with _client(tmp_path) as client:
            res = client.post(
                "/api/reports/upload",
                data={
                    "kind": "found",
                    "description": "Found a red water bottle near the canteen",
                    "contact_email": "multi@example.com",
                },
                files=[
                    ("photos", ("a.png", _png((190, 30, 35)), "image/png")),
                    ("photos", ("b.png", _png((30, 90, 190)), "image/png")),
                    ("photos", ("c.png", _png((190, 30, 35)), "image/png")),
                ],
            )
            assert res.status_code == 200, res.text
            rep = res.json()["report"]
            created.append(rep["image_path"])
            created.extend(rep["image_path"].replace(".jpg", f"-p{i}.jpg") for i in (2, 3))

            assert rep["photo_count"] == 3
            assert len(rep["image_urls"]) == 3
            for url in rep["image_urls"]:
                assert client.get(url).status_code == 200
            # internal keys never reach a client
            assert "_photo_keys" not in rep["attrs"]
            assert rep["attrs"]["primary_color"] == "red"

            # the pooled vector exists, so the report is matchable
            matched = client.post(f"/api/match/{rep['id']}", params={"top": 3}).json()
            # signed URLs carry a short-lived expiry, so compare the object key,
            # not the signature: two calls a second apart sign differently.
            assert matched["query"]["image_url"].split("?")[0] == rep["image_url"].split("?")[0]

            too_many = client.post(
                "/api/reports/upload",
                data={"kind": "found", "description": "too many", "contact_email": "multi@example.com"},
                files=[("photos", (f"p{i}.png", _png((10, 10, 10)), "image/png")) for i in range(5)],
            )
            assert too_many.status_code == 422
    finally:
        store = get_object_store()
        for key in created:
            with contextlib.suppress(OSError, ValueError):
                store.delete(key)


def test_upload_rejects_a_non_image(tmp_path):
    with _client(tmp_path) as client:
        wrong_type = client.post(
            "/api/reports/upload",
            data={"kind": "lost", "description": "blue bottle", "contact_email": "reporter@example.com"},
            files={"photo": ("notes.txt", b"not an image", "text/plain")},
        )
        assert wrong_type.status_code == 400

        corrupt = client.post(
            "/api/reports/upload",
            data={"kind": "lost", "description": "blue bottle", "contact_email": "reporter@example.com"},
            files={"photo": ("broken.png", b"\x89PNG not really", "image/png")},
        )
        assert corrupt.status_code == 400


def test_upload_rejects_oversized_and_decompression_bomb_images(tmp_path):
    with _client(tmp_path) as client:
        oversized = client.post(
            "/api/reports/upload",
            data={"kind": "lost", "description": "blue bottle", "contact_email": "reporter@example.com"},
            files={"photo": ("large.png", b"\x89PNG\r\n\x1a\n" + b"x" * (9 * 1024 * 1024), "image/png")},
        )
        assert oversized.status_code == 413

        bomb = client.post(
            "/api/reports/upload",
            data={"kind": "lost", "description": "blue bottle", "contact_email": "reporter@example.com"},
            files={"photo": ("bomb.png", _oversized_png_header(), "image/png")},
        )
        assert bomb.status_code == 400
        assert "40 megapixel" in bomb.json()["detail"]


def test_anonymous_report_requires_contact_and_returns_one_time_ownership_token(tmp_path):
    with _client(tmp_path) as client:
        missing_contact = client.post(
            "/api/reports",
            json={"kind": "lost", "description": "Lost a blue water bottle"},
        )
        assert missing_contact.status_code == 422

        created = client.post(
            "/api/reports",
            json={
                "kind": "lost",
                "description": "Lost a blue water bottle",
                "contact_email": "owner@example.com",
            },
        )
        assert created.status_code == 200
        body = created.json()
        token = body["ownership_token"]
        assert len(token) >= 32
        assert "contact_email" not in body["report"]

        verified = client.post(
            f"/api/reports/{body['report']['id']}/claim",
            json={"ownership_token": token},
        )
        assert verified.status_code == 200
        assert verified.json()["status"] == "ownership_verified"

        rejected = client.post(
            f"/api/reports/{body['report']['id']}/claim",
            json={"ownership_token": "x" * 40},
        )
        assert rejected.status_code == 403


def test_spam_reports_are_held_out_of_public_feed_and_matching(tmp_path):
    with _client(tmp_path) as client:
        created = client.post(
            "/api/reports",
            json={
                "kind": "found",
                "description": "Found a blue bottle",
                "website": "spam.example",
                "contact_email": "reporter@example.com",
            },
        ).json()["report"]
        assert created["status"] == "pending_review"
        assert created["id"] not in [r["id"] for r in client.get("/api/reports").json()["reports"]]
        assert client.get(f"/api/reports/{created['id']}").status_code == 404
        assert client.post(f"/api/match/{created['id']}").status_code == 404


def test_operating_point_is_backed_by_runtime_settings(tmp_path, monkeypatch):
    # A fresh checkout has no fitted calibration; isolate the default path so a
    # generated artifact in the working tree cannot change this contract.
    calibration.clear_cache()
    monkeypatch.setattr(
        calibration, "settings", _settings_with_calibration_path(tmp_path / "absent.json")
    )

    with _client(tmp_path) as client:
        result = client.get("/api/config/operating-point").json()
        assert result == {
            "plausible_floor": settings.plausible_floor,
            "prob_alert": settings.prob_alert,
            "margin_min": settings.margin_min,
            "bands": {
                "strong": settings.strong_band,
                "probable": settings.probable_band,
                "weak": settings.weak_band,
            },
            "models": {"text": "hash-char-ngram-v1", "image": "hist-grid-v1"},
        }


def test_operating_point_reports_the_serving_calibration(tmp_path, monkeypatch):
    """When a calibration serves, its measured thresholds replace the defaults."""
    calibration.clear_cache()
    monkeypatch.setattr(rerank, "active_scorer", lambda: "test-scorer")
    path = calibration.save(
        calibration.CalibrationParams(
            method="platt",
            version="platt-test",
            scorer="test-scorer",
            input_scale="logit",
            prob_alert=0.4,
            plausible_floor=0.1,
            margin_min=0.05,
        ),
        tmp_path / "calibration.json",
    )
    monkeypatch.setattr(calibration, "settings", _settings_with_calibration_path(path))

    with _client(tmp_path) as client:
        result = client.get("/api/config/operating-point").json()

    assert result["prob_alert"] == 0.4
    assert result["plausible_floor"] == 0.1
    assert result["calibration_version"] == "platt-test"
    assert result["bands"]["probable"] == 0.4


def test_unknown_report_is_404(tmp_path):
    with _client(tmp_path) as client:
        assert client.get("/api/reports/nope").status_code == 404
        assert client.post("/api/match/nope").status_code == 404
