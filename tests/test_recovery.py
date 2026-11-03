"""The recovery loop end to end, and the notification outbox behind it."""

from __future__ import annotations

from fastapi.testclient import TestClient

from lostfound import notify, recovery, store
from lostfound.api import create_app
from lostfound.auth import AuthProviderError
from lostfound.pipeline import build_database
from lostfound.store import Report


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


_STAFF = {"Authorization": "Bearer staff-session"}


def _app(tmp_path):
    db = tmp_path / "recovery.db"
    build_database(db_path=db, n_items=40, seed=11, render_images=False, verbose=False)
    con = store.connect(db)
    store.set_user_role(con, "staff-user", "staff")
    con.commit()
    con.close()
    return TestClient(create_app(db, auth_client=_FakeAuth())), db


def _file_pair(client):
    """A lost and a found report that share colour, brand and serial."""
    common = {
        "contact_email": "owner@example.com",
        "contact_phone": "",
        "website": "",
        "form_started_at": 0,
    }
    lost = client.post(
        "/api/reports",
        json={
            "kind": "lost",
            "description": "black Wildcraft backpack, scratch on the zip, serial SN-4471",
            **common,
        },
    )
    assert lost.status_code == 200, lost.text
    found = client.post(
        "/api/reports",
        json={
            "kind": "found",
            "description": "black Wildcraft backpack found near the library, tag SN-4471",
            **common,
        },
    )
    assert found.status_code == 200, found.text
    return lost.json()["report"]["id"], found.json()["report"]["id"]


# ------------------------------------------------------------ pure functions
def _report(attrs: dict) -> Report:
    return Report(id="LR-1", kind="lost", created_at="2026-10-28T00:00:00Z", description="x", attrs=attrs)


def test_challenge_asks_strongest_questions_first_and_omits_unknowns():
    report = _report(
        {
            "primary_color": "black",
            "brand": "Wildcraft",
            "visible_text": ["SN-4471"],
            "marks": ["scratch on zip"],
        }
    )
    keys = [question["key"] for question in recovery.challenge_for(report)]
    assert keys == ["visible_text", "details", "brand_or_model", "primary_color"]
    assert recovery.challenge_for(_report({})) == []


def test_grading_ignores_case_punctuation_and_requires_every_answer():
    report = _report({"primary_color": "black", "brand": "Wildcraft", "visible_text": ["SN-4471"]})
    ok, score = recovery.grade(
        report,
        {"visible_text": "sn 4471", "brand_or_model": "wildcraft", "primary_color": "Black"},
    )
    assert ok is True
    assert score == 1.0

    ok, score = recovery.grade(
        report,
        {"visible_text": "sn 4471", "brand_or_model": "wildcraft", "primary_color": "red"},
    )
    assert ok is False
    assert score == round(2 / 3, 3)


def test_a_report_with_one_detail_cannot_be_verified_online():
    report = _report({"primary_color": "black", "visible_text": ["SN-4471"]})
    ok, _ = recovery.grade(report, {"visible_text": "SN-4471", "primary_color": "black"})
    # two questions are only just enough; one distinguishing detail is not
    assert ok is True
    assert recovery.grade(_report({"primary_color": "black"}), {"primary_color": "black"})[0] is False


def test_handover_codes_are_never_stored_in_the_clear():
    code = recovery.new_handover_code()
    digest = recovery.hash_token(code)
    assert len(code) == recovery.HANDOVER_DIGITS
    assert code not in digest
    assert recovery.code_matches(digest, code)
    assert not recovery.code_matches(digest, "000000" if code != "000000" else "111111")


# -------------------------------------------------------------- notification
def test_outbox_dedupes_and_survives_a_failed_delivery(tmp_path):
    con = store.connect(tmp_path / "outbox.db")
    try:
        assert notify.queue(con, dedupe_key="k1", subject="s", body="b", recipient="a@b.c") is True
        assert notify.queue(con, dedupe_key="k1", subject="s", body="b", recipient="a@b.c") is False
        con.commit()

        class Boom:
            name = "boom"

            def send(self, message):
                raise RuntimeError("relay down")

        result = notify.dispatch(con, transport=Boom())
        assert result == {"sent": 0, "failed": 1, "skipped": 0}
        counts = store.notification_counts(con)
        assert counts["failed"] == 1 and counts["pending"] == 0

        # A failed message is not lost: re-queueing the same key is a no-op, so a
        # retry must target the existing row rather than create a duplicate.
        row = con.execute("SELECT dedupe_key, attempts, last_error FROM notifications").fetchone()
        assert row["dedupe_key"] == "k1"
        assert row["attempts"] == 1
        assert "relay down" in row["last_error"]
    finally:
        con.close()


# ---------------------------------------------------------------------- API
def test_claim_requires_the_right_answers_and_then_collects(tmp_path):
    client, db = _app(tmp_path)
    with client:
        lost_id, found_id = _file_pair(client)

        challenge = client.get("/api/claims/challenge", params={"query_id": lost_id, "candidate_id": found_id})
        assert challenge.status_code == 200, challenge.text
        body = challenge.json()
        assert body["verifiable_online"] is True
        keys = {question["key"] for question in body["questions"]}
        assert {"visible_text", "brand_or_model", "primary_color"} <= keys

        wrong = client.post(
            "/api/claims",
            json={
                "query_id": lost_id,
                "candidate_id": found_id,
                "answers": {"visible_text": "XX-9999", "brand_or_model": "wildcraft", "primary_color": "black"},
            },
        )
        assert wrong.status_code == 403

        right = client.post(
            "/api/claims",
            json={
                "query_id": lost_id,
                "candidate_id": found_id,
                "answers": {
                    "visible_text": "sn 4471",
                    "brand_or_model": "Wildcraft",
                    "primary_color": "black",
                },
            },
        )
        assert right.status_code == 200, right.text
        claim = right.json()
        assert claim["status"] == "verified"
        assert claim["needs_staff"] is False
        code = claim["handover_code"]

        lookup = client.post(f"/api/claims/{claim['claim_id']}/lookup", json={"token": claim["claim_token"]})
        assert lookup.status_code == 200
        assert lookup.json()["status"] == "verified"
        assert client.post(f"/api/claims/{claim['claim_id']}/lookup", json={"token": "x" * 40}).status_code == 404

        listed = client.get("/api/claims", headers=_STAFF)
        assert listed.status_code == 200
        # the verifier hash is not data: it never leaves the server
        assert "handover_hash" not in listed.json()["claims"][0]
        assert "token_hash" not in listed.json()["claims"][0]

        assert (
            client.post(f"/api/claims/{claim['claim_id']}/collect", json={"code": "000000"}, headers=_STAFF).status_code
            == 403
        )

        collected = client.post(f"/api/claims/{claim['claim_id']}/collect", json={"code": code}, headers=_STAFF)
        assert collected.status_code == 200, collected.text
        assert collected.json()["status"] == "collected"
        assert (
            client.post(f"/api/claims/{claim['claim_id']}/collect", json={"code": code}, headers=_STAFF).status_code
            == 409
        )

        for report_id in (lost_id, found_id):
            assert client.get(f"/api/reports/{report_id}").json()["report"]["status"] == "recovered"

    con = store.connect(db)
    try:
        counts = store.notification_counts(con)
        assert counts["sent"] >= 1
        audit_actions = {row["action"] for row in con.execute("SELECT action FROM audit_log ORDER BY created_at")}
        assert {"claim.verified", "claim.collected"} <= audit_actions
        # answers are never persisted: only the questions asked
        stored = con.execute("SELECT challenge_json FROM claims").fetchone()[0]
        assert "wildcraft" not in stored.lower()
    finally:
        con.close()
