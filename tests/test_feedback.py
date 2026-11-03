import csv
import datetime as dt

from fastapi.testclient import TestClient

from lostfound import store
from lostfound.api import create_app
from lostfound.auth import AuthProviderError
from lostfound.cli import main as cli_main
from lostfound.pipeline import build_database


class _FakeAuth:
    def get_user(self, token):
        if token != "staff-session":
            raise AuthProviderError(401, "invalid token")
        return {"id": "staff-user", "email": "staff@example.com"}

    def register(self, email, password):
        raise NotImplementedError

    def login(self, email, password):
        raise NotImplementedError

    def refresh(self, refresh_token):
        raise NotImplementedError

    def logout(self, access_token):
        return None


def _client(tmp_path):
    db = tmp_path / "fb.db"
    build_database(db_path=db, n_items=50, seed=9, render_images=False, verbose=False)
    con = store.connect(db)
    store.set_user_role(con, "staff-user", "staff")
    con.commit()
    con.close()
    return TestClient(create_app(db, auth_client=_FakeAuth())), db


def _staff_headers():
    return {"Authorization": "Bearer staff-session"}


def test_confirm_marks_matched_and_returns_labels(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        found = client.get("/api/reports", params={"kind": "found", "limit": 2}).json()["reports"][0]
        lost = client.get("/api/reports", params={"kind": "lost", "limit": 2}).json()["reports"][0]

        res = client.post(
            "/api/feedback",
            json={"query_id": lost["id"], "candidate_id": found["id"], "action": "confirm", "staff": "desk"},
            headers=_staff_headers(),
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "matched"
        assert body["counts"]["confirmed"] == 1

        public_match = client.post(f"/api/match/{lost['id']}").json()
        assert public_match["feedback"] == {}
        matched = client.post(f"/api/match/{lost['id']}", headers=_staff_headers()).json()
        assert matched["feedback"][found["id"]] == "confirm"

        assert client.get(f"/api/reports/{lost['id']}").json()["report"]["status"] == "matched"
        assert client.get(f"/api/reports/{found['id']}").json()["report"]["status"] == "matched"

        stats = client.get("/api/stats", headers=_staff_headers()).json()
        assert stats["confirmed"] == 1
        assert stats["matched"] == 2
        feedback = client.get("/api/feedback", headers=_staff_headers()).json()["items"]
        assert feedback[0]["staff"] == "staff@example.com"


def test_reject_reopens_and_replaces_the_label(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        found = client.get("/api/reports", params={"kind": "found", "limit": 1}).json()["reports"][0]
        lost = client.get("/api/reports", params={"kind": "lost", "limit": 1}).json()["reports"][0]
        pair = {"query_id": lost["id"], "candidate_id": found["id"]}

        client.post("/api/feedback", json={**pair, "action": "confirm"}, headers=_staff_headers())
        res = client.post("/api/feedback", json={**pair, "action": "reject"}, headers=_staff_headers())
        assert res.json()["status"] == "open"

        assert client.get(f"/api/reports/{lost['id']}").json()["report"]["status"] == "open"
        labels = client.get("/api/feedback", params={"query_id": lost["id"]}, headers=_staff_headers()).json()["labels"]
        assert labels[found["id"]] == "reject"
        # one label per pair, not a history of contradictions
        items = client.get("/api/feedback", headers=_staff_headers()).json()["items"]
        assert len(items) == 1


def test_feedback_cursor_pages_are_complete_and_clamp_page_size(tmp_path):
    client, db = _client(tmp_path)
    con = store.connect(db)
    try:
        lost_ids = [report.id for report in store.list_reports(con, "lost")[:3]]
        found_ids = [report.id for report in store.list_reports(con, "found")[:35]]
        report_pairs = [(lost_id, found_id) for lost_id in lost_ids for found_id in found_ids][:105]
        assert len(report_pairs) == 105
        for index, (query_id, candidate_id) in enumerate(report_pairs):
            store.add_feedback(
                con,
                query_id=query_id,
                candidate_id=candidate_id,
                action="reject",
                prob=0.1,
                score=0.1,
                features={},
                staff="staff@example.com",
                created_at=(dt.datetime(2026, 10, 28, 10) + dt.timedelta(seconds=index)).isoformat(),
            )
        con.commit()
    finally:
        con.close()

    with client:
        oversized = client.get("/api/feedback", params={"limit": 100000}, headers=_staff_headers())
        assert oversized.status_code == 200
        assert oversized.json()["count"] == 100
        assert oversized.json()["next_cursor"]

        collected = []
        cursor = None
        while True:
            params = {"limit": 2}
            if cursor:
                params["cursor"] = cursor
            page = client.get("/api/feedback", params=params, headers=_staff_headers())
            assert page.status_code == 200, page.text
            body = page.json()
            collected.extend((item["query_id"], item["candidate_id"]) for item in body["items"])
            cursor = body["next_cursor"]
            if cursor is None:
                break

        assert len(collected) == 105
        assert len(set(collected)) == 105


def test_feedback_rejects_unknown_reports_and_bad_actions(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        found = client.get("/api/reports", params={"kind": "found", "limit": 1}).json()["reports"][0]
        lost = client.get("/api/reports", params={"kind": "lost", "limit": 1}).json()["reports"][0]

        assert (
            client.post(
                "/api/feedback",
                json={"query_id": "nope", "candidate_id": found["id"], "action": "confirm"},
                headers=_staff_headers(),
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/feedback",
                json={"query_id": lost["id"], "candidate_id": found["id"], "action": "maybe"},
                headers=_staff_headers(),
            ).status_code
            == 422
        )


def test_feedback_export_includes_features_for_training(tmp_path):
    client, db = _client(tmp_path)
    with client:
        found = client.get("/api/reports", params={"kind": "found", "limit": 1}).json()["reports"][0]
        lost = client.get("/api/reports", params={"kind": "lost", "limit": 1}).json()["reports"][0]
        client.post(
            "/api/feedback",
            json={"query_id": lost["id"], "candidate_id": found["id"], "action": "confirm"},
            headers=_staff_headers(),
        )

    out = tmp_path / "labels.csv"
    assert cli_main(["--db", str(db), "feedback", "--export", str(out)]) == 0
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "confirm"
    assert row["query_id"] == lost["id"]
    assert float(row["prob"]) > 0
    assert '"feats"' in row["features_json"]
