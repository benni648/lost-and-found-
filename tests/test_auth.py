import hashlib

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from lostfound import store
from lostfound.api import create_app
from lostfound.auth import AuthProviderError
from lostfound.cli import main as cli_main
from lostfound.settings import settings

TEST_PASSWORD = "test-secret-value"


class _FakeAuth:
    def register(self, email, password):
        return {
            "access_token": "session-a",
            "refresh_token": "session-refresh",
            "user": {"id": "user-1", "email": email},
        }

    def login(self, email, password):
        if password != TEST_PASSWORD:
            raise AuthProviderError(400, "invalid credentials")
        return {
            "access_token": "session-a",
            "refresh_token": "session-refresh",
            "user": {"id": "user-1", "email": email},
        }

    def refresh(self, refresh_token):
        if refresh_token != "session-refresh":
            raise AuthProviderError(400, "invalid refresh token")
        return {
            "access_token": "session-b",
            "refresh_token": "session-refresh-b",
            "user": {"id": "user-1", "email": "student@example.com"},
        }

    def logout(self, access_token):
        if access_token not in {"session-a", "session-b"}:
            raise AuthProviderError(401, "invalid token")

    def get_user(self, access_token):
        if access_token not in {"session-a", "session-b"}:
            raise AuthProviderError(401, "invalid token")
        return {"id": "user-1", "email": "student@example.com"}


def _client(tmp_path):
    return TestClient(create_app(tmp_path / "auth.db", auth_client=_FakeAuth()))


def test_auth_register_login_refresh_and_logout(tmp_path):
    with _client(tmp_path) as client:
        registered = client.post(
            "/api/auth/register",
            json={"email": "Student@Example.com", "password": TEST_PASSWORD, "role": "superadmin"},
        )
        assert registered.status_code == 200
        assert registered.json()["user"]["role"] == "student"
        assert registered.json()["user"]["email"] == "student@example.com"
        assert registered.json()["user"]["campus_id"] == settings.default_campus_id

        assert client.get("/api/auth/me", headers={"Authorization": "Bearer session-a"}).json()["role"] == "student"

        logged_in = client.post(
            "/api/auth/login",
            json={"email": "student@example.com", "password": TEST_PASSWORD},
        )
        assert logged_in.status_code == 200

        refreshed = client.post("/api/auth/refresh", json={"refresh_token": "session-refresh"})
        assert refreshed.status_code == 200
        assert refreshed.json()["access_token"] == "session-b"

        assert client.post("/api/auth/logout", headers={"Authorization": "Bearer session-b"}).status_code == 200


def test_privileged_routes_require_staff_role_and_reject_missing_or_invalid_tokens(tmp_path):
    with _client(tmp_path) as client:
        assert client.get("/api/stats").status_code == 401
        assert client.get("/api/stats", headers={"Authorization": "Bearer invalid"}).status_code == 401
        assert client.get("/api/stats", headers={"Authorization": "Bearer session-a"}).status_code == 403
        assert client.get("/api/feedback", headers={"Authorization": "Bearer session-a"}).status_code == 403


def test_post_feedback_requires_staff_authentication(tmp_path):
    with _client(tmp_path) as client:
        client.post("/api/auth/register", json={"email": "student@example.com", "password": TEST_PASSWORD})
        payload = {"query_id": "LR-1", "candidate_id": "FR-1", "action": "confirm"}
        assert client.post("/api/feedback", json=payload).status_code == 401
        token = client.post(
            "/api/auth/login",
            json={"email": "student@example.com", "password": TEST_PASSWORD},
        ).json()["access_token"]
        response = client.post(
            "/api/feedback",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403


def test_extract_rate_limit_and_login_lockout(tmp_path):
    with _client(tmp_path) as client:
        for _ in range(60):
            assert client.post("/api/extract", json={"description": "blue bottle"}).status_code == 200
        limited = client.post("/api/extract", json={"description": "blue bottle"})
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) > 0

    with _client(tmp_path) as client:
        for _ in range(5):
            failed = client.post(
                "/api/auth/login",
                json={"email": "student@example.com", "password": "incorrect-secret"},
            )
            assert failed.status_code == 401
        locked = client.post(
            "/api/auth/login",
            json={"email": "student@example.com", "password": TEST_PASSWORD},
        )
        assert locked.status_code == 429


def test_cli_role_provisioning_is_the_only_way_to_promote_a_user(tmp_path):
    db = tmp_path / "roles.db"
    with TestClient(create_app(db, auth_client=_FakeAuth())) as client:
        client.post("/api/auth/register", json={"email": "student@example.com", "password": TEST_PASSWORD})
        assert client.get("/api/stats", headers={"Authorization": "Bearer session-a"}).status_code == 403

    assert cli_main(["--db", str(db), "auth", "set-role", "user-1", "staff"]) == 0
    with TestClient(create_app(db, auth_client=_FakeAuth())) as client:
        assert client.get("/api/auth/me", headers={"Authorization": "Bearer session-a"}).json()["role"] == "staff"
        assert client.get("/api/stats", headers={"Authorization": "Bearer session-a"}).status_code == 200


def test_authenticated_account_email_satisfies_report_contact_requirement(tmp_path):
    with _client(tmp_path) as client:
        response = client.post(
            "/api/reports",
            json={"kind": "found", "description": "Found a blue water bottle"},
            headers={"Authorization": "Bearer session-a"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["ownership_token"]


def test_security_headers_and_cors_allowlist(tmp_path):
    with _client(tmp_path) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.headers["content-security-policy"]
        assert response.headers["strict-transport-security"]
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert response.headers["permissions-policy"] == "geolocation=(self)"
        assert response.headers["x-frame-options"] == "DENY"

        allowed = client.options(
            "/api/auth/login",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert allowed.status_code == 200
        assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"

        denied = client.options(
            "/api/auth/login",
            headers={
                "Origin": "https://unlisted.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert denied.status_code == 400
        assert "access-control-allow-origin" not in denied.headers
        assert denied.headers["x-content-type-options"] == "nosniff"


def test_security_headers_are_present_on_every_api_route(tmp_path):
    app = create_app(tmp_path / "headers.db", auth_client=_FakeAuth())
    header_names = {
        "content-security-policy",
        "strict-transport-security",
        "x-content-type-options",
        "referrer-policy",
        "permissions-policy",
        "x-frame-options",
    }

    with TestClient(app) as client:
        routes = [
            (route.path, method)
            for route in app.routes
            if isinstance(route, APIRoute) and route.path.startswith("/api/")
            for method in route.methods or set()
            if method in {"GET", "POST"}
        ]
        assert routes
        for route_path, method in routes:
            path = route_path.replace("{report_id}", "LR-0001").replace("{kind}", "lost")
            response = client.request(method, path)
            assert header_names <= response.headers.keys(), (
                f"security headers missing from {method} {route_path}: {header_names - response.headers.keys()}"
            )


def test_production_requires_secure_auth_and_disables_api_docs(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(settings, "supabase_url", "https://project.supabase.co")
    monkeypatch.setattr(settings, "supabase_anon_key", "public-anon-key")
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(settings, "cors_origins", "https://app.example")
    monkeypatch.setattr(settings, "object_storage_backend", "s3")
    monkeypatch.setattr(settings, "s3_bucket", "private-test-bucket")

    app = create_app(tmp_path / "production.db")
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None


def test_reset_preserves_accounts_and_clears_report_ownership(tmp_path):
    con = store.connect(tmp_path / "reset.db")
    store.ensure_user(con, "account-1", email="student@example.com")
    store.add_report_owner(
        con,
        "LR-0001",
        user_id="account-1",
        contact_email="student@example.com",
        contact_phone=None,
        token_hash=hashlib.sha256(b"test-token").hexdigest(),
    )
    con.commit()

    store.reset(con)

    assert store.get_user(con, "account-1") is not None
    assert store.report_owner_token_hash(con, "LR-0001") is None
    con.close()
