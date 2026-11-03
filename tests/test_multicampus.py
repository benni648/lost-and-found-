from fastapi.testclient import TestClient

from lostfound import store
from lostfound.api import create_app


class CampusAuth:
    def get_user(self, access_token):
        users = {
            "token-a": {"id": "user-a", "email": "a@example.com"},
            "token-b": {"id": "user-b", "email": "b@example.com"},
        }
        return users[access_token]


def test_reports_and_matching_are_scoped_to_the_requesting_campus(tmp_path):
    database = tmp_path / "campus-isolation.db"
    con = store.connect(database)
    store.set_user_role(con, "user-a", "student", "campus-a")
    store.set_user_role(con, "user-b", "student", "campus-b")
    con.commit()
    con.close()

    with TestClient(create_app(database, auth_client=CampusAuth())) as client:
        lost_a = client.post(
            "/api/reports",
            json={"kind": "lost", "description": "Lost a blue water bottle"},
            headers={"Authorization": "Bearer token-a"},
        )
        found_a = client.post(
            "/api/reports",
            json={"kind": "found", "description": "Found a blue water bottle"},
            headers={"Authorization": "Bearer token-a"},
        )
        found_b = client.post(
            "/api/reports",
            json={"kind": "found", "description": "Found a blue water bottle"},
            headers={"Authorization": "Bearer token-b"},
        )
        default_report = client.post(
            "/api/reports",
            json={"kind": "found", "description": "Found a red umbrella", "contact_email": "guest@example.com"},
        )

        assert all(response.status_code == 200 for response in (lost_a, found_a, found_b, default_report))
        lost_a_id = lost_a.json()["report"]["id"]
        found_a_id = found_a.json()["report"]["id"]
        found_b_id = found_b.json()["report"]["id"]
        default_id = default_report.json()["report"]["id"]

        campus_a_headers = {"Authorization": "Bearer token-a"}
        campus_b_headers = {"Authorization": "Bearer token-b"}
        ids_for_a = {report["id"] for report in client.get("/api/reports", headers=campus_a_headers).json()["reports"]}
        ids_for_b = {report["id"] for report in client.get("/api/reports", headers=campus_b_headers).json()["reports"]}
        ids_for_anonymous = {report["id"] for report in client.get("/api/reports").json()["reports"]}

        assert ids_for_a == {lost_a_id, found_a_id}
        assert ids_for_b == {found_b_id}
        assert ids_for_anonymous == {default_id}
        assert client.get(f"/api/reports/{lost_a_id}", headers=campus_b_headers).status_code == 404
        assert client.post(f"/api/match/{lost_a_id}", headers=campus_b_headers).status_code == 404

        matches = client.post(f"/api/match/{lost_a_id}", headers=campus_a_headers).json()["matches"]
        assert [match["candidate_id"] for match in matches] == [found_a_id]


def test_api_matcher_reloads_after_a_report_is_written_by_another_process(tmp_path):
    database = tmp_path / "matcher-invalidation.db"
    with TestClient(create_app(database)) as client:
        lost = client.post(
            "/api/reports",
            json={"kind": "lost", "description": "Lost a blue water bottle", "contact_email": "guest@example.com"},
        ).json()["report"]

        con = store.connect(database, initialize=False)
        store.insert_report(
            con,
            store.Report(
                id="FR-external",
                kind="found",
                created_at="2026-10-28T11:00:00",
                description="Found a blue water bottle",
                attrs={"category": "bottle", "primary_color": "blue"},
            ),
        )
        con.commit()
        con.close()

        result = client.post(f"/api/match/{lost['id']}").json()

    assert [match["candidate_id"] for match in result["matches"]] == ["FR-external"]
