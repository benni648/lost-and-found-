"""Supabase Auth API client.

The application stores authorization roles locally and never trusts role claims
from provider tokens. Supabase remains responsible for credentials and sessions.
"""

from __future__ import annotations

import json
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class AuthClient(Protocol):
    def register(self, email: str, password: str) -> dict[str, Any]: ...

    def login(self, email: str, password: str) -> dict[str, Any]: ...

    def refresh(self, refresh_token: str) -> dict[str, Any]: ...

    def logout(self, access_token: str) -> None: ...

    def get_user(self, access_token: str) -> dict[str, Any]: ...


class AuthProviderError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class SupabaseAuth:
    def __init__(self, url: str | None, anon_key: str | None):
        self.url = url.rstrip("/") if url else None
        self.anon_key = anon_key
        if self.url:
            parsed = urlsplit(self.url)
            if parsed.scheme not in {"https", "http"} or not parsed.netloc:
                raise ValueError("SUPABASE_URL must be an absolute HTTP(S) URL.")
            if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("SUPABASE_URL must use HTTPS except for a local development instance.")

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        if not self.url or not self.anon_key:
            raise AuthProviderError(503, "Supabase Auth is not configured.")

        headers = {"apikey": self.anon_key, "Content-Type": "application/json"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        # All paths are fixed API routes; only the validated provider origin is configurable.
        request = Request(f"{self.url}{path}", data=data, headers=headers, method=method)  # noqa: S310
        try:
            # The operator-configured base URL is restricted to HTTP(S) above.
            with urlopen(request, timeout=8) as response:  # noqa: S310
                body = response.read()
        except HTTPError as exc:
            try:
                details = json.loads(exc.read())
            except (json.JSONDecodeError, UnicodeDecodeError):
                details = {}
            message = details.get("msg") or details.get("message") or details.get("error_description")
            raise AuthProviderError(exc.code, str(message or "Supabase Auth rejected the request.")) from exc
        except (URLError, TimeoutError) as exc:
            raise AuthProviderError(503, "Supabase Auth is unavailable.") from exc

        if not body:
            return {}
        try:
            result = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AuthProviderError(502, "Supabase Auth returned an invalid response.") from exc
        if not isinstance(result, dict):
            raise AuthProviderError(502, "Supabase Auth returned an invalid response.")
        return result

    def register(self, email: str, password: str) -> dict[str, Any]:
        return self._request("POST", "/auth/v1/signup", {"email": email, "password": password})

    def login(self, email: str, password: str) -> dict[str, Any]:
        return self._request("POST", "/auth/v1/token?grant_type=password", {"email": email, "password": password})

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/auth/v1/token?grant_type=refresh_token",
            {"refresh_token": refresh_token},
        )

    def logout(self, access_token: str) -> None:
        self._request("POST", "/auth/v1/logout", access_token=access_token)

    def get_user(self, access_token: str) -> dict[str, Any]:
        return self._request("GET", "/auth/v1/user", access_token=access_token)


def provider_user(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return a Supabase user object from signup or token response shapes."""
    user = payload.get("user")
    return user if isinstance(user, dict) else None


def valid_provider_user(user: dict[str, Any]) -> tuple[str, str | None, str | None]:
    user_id = user.get("id")
    if not isinstance(user_id, str) or not user_id:
        raise AuthProviderError(502, "Supabase Auth returned a user without an id.")
    email = user.get("email")
    phone = user.get("phone")
    return user_id, email if isinstance(email, str) else None, phone if isinstance(phone, str) else None
