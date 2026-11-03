"""Opt-in checks against a real Supabase Auth project."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from lostfound.api import create_app
from lostfound.auth import SupabaseAuth
from lostfound.ratelimit import RateLimiter
from lostfound.settings import settings


@pytest.mark.supabase_live
def test_supabase_login_me_refresh_and_logout(tmp_path):
    email = os.getenv("SUPABASE_TEST_EMAIL")
    password = os.getenv("SUPABASE_TEST_PASSWORD")
    if not settings.supabase_url or not settings.supabase_anon_key or not email or not password:
        pytest.skip(
            "Set SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_TEST_EMAIL, "
            "and SUPABASE_TEST_PASSWORD to run live Supabase tests."
        )

    app = create_app(
        tmp_path / "supabase-live-test.db",
        auth_client=SupabaseAuth(settings.supabase_url, settings.supabase_anon_key),
        rate_limiter=RateLimiter(None),
    )
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200
        session = login.json()
        assert session["access_token"]
        assert session["refresh_token"]

        headers = {"Authorization": f"Bearer {session['access_token']}"}
        me = client.get("/api/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["id"] == session["user"]["id"]
        assert me.json()["email"].casefold() == email.casefold()
        assert me.json()["role"] == "student"

        refreshed = client.post("/api/auth/refresh", json={"refresh_token": session["refresh_token"]})
        assert refreshed.status_code == 200
        refreshed_session = refreshed.json()
        assert refreshed_session["access_token"]
        assert refreshed_session["refresh_token"]

        refreshed_me = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {refreshed_session['access_token']}"},
        )
        assert refreshed_me.status_code == 200
        assert refreshed_me.json()["id"] == session["user"]["id"]

        logout = client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {refreshed_session['access_token']}"},
        )
        assert logout.status_code == 200
