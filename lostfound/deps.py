"""Authentication and authorization dependencies."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import store
from .auth import AuthProviderError, valid_provider_user

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str | None
    phone_e164: str | None
    role: str
    campus_id: str | None


def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return _load_user(request, credentials.credentials)


def optional_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> CurrentUser | None:
    if credentials is None:
        return None
    return _load_user(request, credentials.credentials)


def _load_user(request: Request, access_token: str) -> CurrentUser:
    try:
        provider_user = request.app.state.auth_client.get_user(access_token)
        user_id, email, phone = valid_provider_user(provider_user)
    except AuthProviderError as exc:
        code = (
            status.HTTP_401_UNAUTHORIZED if exc.status_code in {400, 401, 403} else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(
            status_code=code,
            detail="Invalid or expired access token." if code == status.HTTP_401_UNAUTHORIZED else str(exc),
            headers={"WWW-Authenticate": "Bearer"} if code == status.HTTP_401_UNAUTHORIZED else None,
        ) from exc

    con = store.connect(request.app.state.db_path, initialize=False)
    try:
        store.ensure_user(con, user_id, email=email, phone_e164=phone)
        row = store.get_user(con, user_id)
        con.commit()
    finally:
        con.close()

    if row is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is not provisioned.")
    return CurrentUser(
        id=user_id,
        email=row["email"],
        phone_e164=row["phone_e164"],
        role=row["role"],
        campus_id=row["campus_id"],
    )


def require_role(role: str) -> Callable:
    allowed = {
        "student": {"student", "staff", "campus_admin", "superadmin"},
        "staff": {"staff", "campus_admin", "superadmin"},
        "campus_admin": {"campus_admin", "superadmin"},
        "superadmin": {"superadmin"},
    }
    if role not in allowed:
        raise ValueError(f"unknown role: {role}")

    def authorize(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
        if user.role not in allowed[role]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"{role} role required.")
        return user

    return authorize
