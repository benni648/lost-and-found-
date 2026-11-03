"""HTTP API.

Run with ``python -m lostfound.cli serve`` then:

    GET  /api/campus              campus graph for the map
    GET  /api/reports?kind=found  report feed
    GET  /api/reports/{id}        one report with its stored matches
    POST /api/reports             file a new text report
    POST /api/reports/upload      file a new report with one photo
    POST /api/match/{id}          rank candidates for a report
    GET  /api/stats               corpus counters
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import hmac
import json
import mimetypes
import re
import secrets
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from threading import RLock
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from redis.exceptions import RedisError

from . import calibration, encoders, notify, recovery, rerank, store
from .auth import AuthClient, AuthProviderError, SupabaseAuth, provider_user, valid_provider_user
from .campus import get_campus
from .config import IMAGE_EMBEDDER, ROOT, TEXT_EMBEDDER, settings
from .deps import CurrentUser, bearer, current_user, optional_current_user, require_role
from .embedders import image_embedder, text_embedder
from .eval import latest_results
from .logging import RequestContextMiddleware, configure_logging, get_logger
from .matching import Matcher
from .middleware import SecurityHeadersMiddleware
from .moderation import needs_review
from .objects import LocalObjectStore, get_local_object_store, get_object_store, media_url
from .pipeline import MAX_PHOTOS, aggregate_image_vector, enrich_report, open_image, save_report_photos
from .queue import RedisJobQueue
from .ratelimit import RateLimiter, rate_limit
from .store import Report
from .taxonomy import COLORS
from .textnorm import extract
from .timeutil import iso_utc, now_utc


class NewReport(BaseModel):
    kind: str = Field(pattern="^(lost|found)$")
    description: str = Field(min_length=3, max_length=1000)
    node_key: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    location_accuracy_m: float | None = Field(default=None, ge=0, le=100000)
    lost_from: str | None = None
    lost_to: str | None = None
    contact_email: str | None = Field(default=None, max_length=254)
    contact_phone: str | None = Field(default=None, max_length=20)
    website: str = Field(default="", max_length=250)
    form_started_at: float | None = Field(default=None, ge=0)


class ExtractRequest(BaseModel):
    description: str = Field(max_length=1000)


class FeedbackRequest(BaseModel):
    query_id: str
    candidate_id: str
    action: str = Field(pattern="^(confirm|reject)$")
    note: str | None = Field(default=None, max_length=400)


class AuthRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or "." not in normalized.rsplit("@", 1)[-1]:
            raise ValueError("a valid email address is required")
        return normalized


class RoleUpdate(BaseModel):
    role: str = Field(pattern="^(student|staff|superadmin)$")


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=4096)


class ClaimRequest(BaseModel):
    ownership_token: str = Field(min_length=32, max_length=128)


class ClaimAnswers(BaseModel):
    """A claim attempt. Answers are graded and then discarded, never stored."""

    query_id: str = Field(min_length=1, max_length=64)
    candidate_id: str = Field(min_length=1, max_length=64)
    answers: dict[str, str] = Field(default_factory=dict)
    contact_email: str | None = Field(default=None, max_length=254)


class ClaimLookup(BaseModel):
    token: str = Field(min_length=8, max_length=128)


class CollectRequest(BaseModel):
    code: str = Field(min_length=4, max_length=12)


MAX_PHOTO_BYTES = 8 * 1024 * 1024
MAX_PAGE_SIZE = 100


def _encode_cursor(values: tuple[str, ...]) -> str:
    payload = json.dumps(values, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None, expected_values: int) -> tuple[str, ...] | None:
    if cursor is None:
        return None
    try:
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        values = json.loads(raw)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid pagination cursor.") from exc
    if (
        not isinstance(values, list)
        or len(values) != expected_values
        or any(not isinstance(value, str) or not value for value in values)
    ):
        raise HTTPException(status_code=400, detail="Invalid pagination cursor.")
    return tuple(values)


def _has_supported_image_signature(data: bytes) -> bool:
    return data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG\r\n\x1a\n")


def _report_contact(
    email: str | None,
    phone: str | None,
    user: CurrentUser | None,
) -> tuple[str | None, str | None]:
    contact_email = (email or "").strip().lower() or (user.email if user else None)
    contact_phone = (phone or "").strip() or (user.phone_e164 if user else None)
    if contact_email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", contact_email):
        raise HTTPException(status_code=422, detail="A valid contact email is required.")
    if contact_phone and not re.fullmatch(r"\+[1-9]\d{7,14}", contact_phone):
        raise HTTPException(status_code=422, detail="A phone number must use E.164 format.")
    if not contact_email and not contact_phone:
        raise HTTPException(status_code=422, detail="Provide a contact email or phone number.")
    return contact_email, contact_phone


def _report_json(r: Report) -> dict:
    # keys beginning with `_` are internal (e.g. additional photo keys) and are
    # never handed to a client
    public_attrs = {key: value for key, value in (r.attrs or {}).items() if not key.startswith("_")}
    photo_keys = ([r.image_path] if r.image_path else []) + list((r.attrs or {}).get("_photo_keys") or [])
    return {
        "id": r.id,
        "kind": r.kind,
        "status": r.status,
        "created_at": r.created_at,
        "campus_timezone": r.campus_timezone,
        "description": r.description,
        "category": r.category,
        "location": r.location_note,
        "node_key": r.node_key,
        "latitude": r.latitude,
        "longitude": r.longitude,
        "location_accuracy_m": r.location_accuracy_m,
        "lost_from": r.lost_from,
        "lost_to": r.lost_to,
        "image_path": r.image_path,
        "image_url": media_url(r.image_path),
        "image_urls": [media_url(key) for key in photo_keys],
        "photo_count": len(photo_keys),
        "has_photo": r.has_photo,
        "is_synthetic_art": r.is_synthetic_art,
        "attrs": public_attrs,
    }


async def _read_upload_bytes(upload: UploadFile) -> bytes:
    """Stream one upload with a hard byte cap, so a huge body is never buffered."""
    chunks: list[bytes] = []
    total_bytes = 0
    try:
        while chunk := await upload.read(min(64 * 1024, MAX_PHOTO_BYTES + 1 - total_bytes)):
            total_bytes += len(chunk)
            if total_bytes > MAX_PHOTO_BYTES:
                raise HTTPException(status_code=413, detail="photo is larger than 8 MB")
            chunks.append(chunk)
    finally:
        await upload.close()
    return b"".join(chunks)


def create_app(
    db_path: str | Path | None = None,
    auth_client: AuthClient | None = None,
    rate_limiter: RateLimiter | None = None,
) -> FastAPI:
    configure_logging(settings.log_level, settings.log_format)
    log = get_logger("lostfound.api")
    if settings.is_production:
        if not settings.supabase_url or not settings.supabase_anon_key:
            raise ValueError("SUPABASE_URL and SUPABASE_ANON_KEY are required in production.")
        if not settings.supabase_url.startswith("https://"):
            raise ValueError("SUPABASE_URL must use HTTPS in production.")
    if settings.is_production and any(not origin.startswith("https://") for origin in settings.cors_origin_list):
        raise ValueError("Production CORS origins must use HTTPS.")
    if settings.is_production and settings.object_storage_backend == "local":
        raise ValueError("Production photo storage must use a private S3-compatible bucket.")
    if settings.object_storage_backend == "s3" and not settings.s3_bucket:
        raise ValueError("S3_BUCKET is required when OBJECT_STORAGE_BACKEND=s3.")
    if settings.async_matching and not settings.redis_url:
        raise ValueError("REDIS_URL is required when ASYNC_MATCHING is enabled.")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        con = store.connect(db_path)
        con.close()
        log.info("API started", extra={"event": "startup", "report_id": None})
        yield

    app = FastAPI(
        title="Campus Lost & Found",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.state.db_path = db_path
    app.state.matcher_cache = {}
    app.state.matcher_cache_versions = {}
    app.state.matcher_cache_lock = RLock()
    app.state.auth_client = auth_client or SupabaseAuth(settings.supabase_url, settings.supabase_anon_key)
    app.state.rate_limiter = rate_limiter or RateLimiter(settings.redis_url, required=settings.is_production)
    app.state.match_queue = None
    if settings.async_matching:
        redis_url = settings.redis_url
        if redis_url is None:
            raise ValueError("REDIS_URL is required when ASYNC_MATCHING is enabled.")
        app.state.match_queue = RedisJobQueue(redis_url)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )
    app.add_middleware(SecurityHeadersMiddleware)

    auth_rate_limit = rate_limit("auth", 10, 60)
    report_rate_limit = rate_limit("reports", 10, 3600, session=True)
    extract_rate_limit = rate_limit("extract", 60, 60)
    match_rate_limit = rate_limit("match", 30, 60, session=True)

    @contextmanager
    def request_matcher(campus_id: str | None = None) -> Iterator[Matcher]:
        scope_id = campus_id or settings.default_campus_id
        con = store.connect(app.state.db_path, initialize=False)
        try:
            current_version = store.campus_match_version(con, scope_id)
            with app.state.matcher_cache_lock:
                cached = app.state.matcher_cache.get(scope_id)
                cached_version = app.state.matcher_cache_versions.get(scope_id)
                if cached is None or cached_version != current_version:
                    cached = Matcher.load(con, campus_id=scope_id)
                    app.state.matcher_cache[scope_id] = cached
                    app.state.matcher_cache_versions[scope_id] = current_version
                matcher = cached.copy_for_connection(con)
            yield matcher
        finally:
            con.close()

    def update_matcher_cache(m: Matcher, reports: list[Report]) -> None:
        with app.state.matcher_cache_lock:
            campus_id = m.campus_id or settings.default_campus_id
            cached = app.state.matcher_cache.get(campus_id)
            if cached is None:
                return
            current_version = store.campus_match_version(m.con, campus_id)
            cached_version = app.state.matcher_cache_versions.get(campus_id, 0)
            if current_version != cached_version + len(reports):
                updated = Matcher.load(m.con, campus_id=campus_id)
            else:
                updated = cached.copy_for_update(m.con)
                for report in reports:
                    updated.upsert(report, con=m.con)
            app.state.matcher_cache[campus_id] = updated
            app.state.matcher_cache_versions[campus_id] = current_version

    def request_campus_id(user: CurrentUser | None) -> str:
        if user is None:
            return settings.default_campus_id
        if user.campus_id is None:
            raise HTTPException(status_code=403, detail="Account is not assigned to a campus.")
        return user.campus_id

    def persist_auth_user(payload: dict[str, Any]) -> dict[str, Any]:
        user_data = provider_user(payload)
        if user_data is None:
            return payload
        user_id, email, phone = valid_provider_user(user_data)
        con = store.connect(app.state.db_path, initialize=False)
        try:
            store.ensure_user(con, user_id, email=email, phone_e164=phone)
            row = store.get_user(con, user_id)
            con.commit()
        finally:
            con.close()
        if row is None:
            raise HTTPException(status_code=503, detail="Unable to persist the authenticated account.")
        return {
            **payload,
            "user": {
                **user_data,
                "role": row["role"],
                "campus_id": row["campus_id"],
            },
        }

    def auth_error(exc: AuthProviderError, *, invalid_credentials: bool = False) -> HTTPException:
        if invalid_credentials and exc.status_code in {400, 401, 403}:
            provider_message = str(exc).strip()
            if "confirm" in provider_message.lower() and "email" in provider_message.lower():
                return HTTPException(
                    status_code=401,
                    detail="Please verify your email address before signing in.",
                )
            return HTTPException(status_code=401, detail="Invalid email or password.")
        if exc.status_code in {400, 409, 422}:
            return HTTPException(status_code=400, detail=str(exc))
        if exc.status_code == 429:
            return HTTPException(status_code=429, detail="Authentication provider rate limit reached.")
        return HTTPException(status_code=503, detail="Authentication provider is unavailable.")

    @app.post("/api/auth/register", dependencies=[Depends(auth_rate_limit)])
    def register(payload: AuthRequest) -> dict:
        try:
            return persist_auth_user(app.state.auth_client.register(payload.email, payload.password))
        except AuthProviderError as exc:
            raise auth_error(exc) from exc

    @app.post("/api/auth/login", dependencies=[Depends(auth_rate_limit)])
    def login(payload: AuthRequest, request: Request) -> dict:
        app.state.rate_limiter.check_login_lockout(request)
        try:
            result = persist_auth_user(app.state.auth_client.login(payload.email, payload.password))
        except AuthProviderError as exc:
            if exc.status_code in {400, 401, 403}:
                app.state.rate_limiter.record_login_failure(request)
            raise auth_error(exc, invalid_credentials=True) from exc
        app.state.rate_limiter.clear_login_failures(request)
        return result

    @app.post("/api/auth/refresh", dependencies=[Depends(auth_rate_limit)])
    def refresh(payload: RefreshRequest) -> dict:
        try:
            return persist_auth_user(app.state.auth_client.refresh(payload.refresh_token))
        except AuthProviderError as exc:
            raise auth_error(exc, invalid_credentials=True) from exc

    @app.post("/api/auth/logout", dependencies=[Depends(auth_rate_limit)])
    def logout(
        user: Annotated[CurrentUser, Depends(current_user)],
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> dict:
        if credentials is None:
            raise HTTPException(status_code=401, detail="Bearer authentication required.")
        try:
            app.state.auth_client.logout(credentials.credentials)
        except AuthProviderError as exc:
            raise auth_error(exc) from exc
        return {"ok": True}

    @app.get("/api/auth/me")
    def me(user: Annotated[CurrentUser, Depends(current_user)]) -> dict:
        return {
            "id": user.id,
            "email": user.email,
            "phone_e164": user.phone_e164,
            "role": user.role,
            "campus_id": user.campus_id,
        }

    @app.get("/api/admin/users")
    def admin_users(user: Annotated[CurrentUser, Depends(require_role("campus_admin"))]) -> dict:
        campus_id = request_campus_id(user)
        con = store.connect(app.state.db_path, initialize=False)
        try:
            rows = store.list_users(con, campus_id=campus_id)
        finally:
            con.close()
        return {
            "users": [
                {
                    "id": row["id"],
                    "email": row["email"],
                    "phone_e164": row["phone_e164"],
                    "role": row["role"],
                    "campus_id": row["campus_id"],
                }
                for row in rows
            ]
        }

    @app.patch("/api/admin/users/{user_id}/role")
    def admin_set_role(
        user_id: str,
        payload: RoleUpdate,
        user: Annotated[CurrentUser, Depends(require_role("campus_admin"))],
    ) -> dict:
        campus_id = request_campus_id(user)
        con = store.connect(app.state.db_path, initialize=False)
        updated = None
        try:
            target = store.get_user(con, user_id)
            if target is None or target["campus_id"] != campus_id:
                raise HTTPException(status_code=404, detail="User not found in your campus.")
            if target["id"] == user.id:
                raise HTTPException(status_code=400, detail="You cannot change your own role.")
            store.set_user_role(con, user_id, payload.role, campus_id)
            con.commit()
            updated = store.get_user(con, user_id)
        finally:
            con.close()
        if updated is None:
            raise HTTPException(status_code=500, detail="The user role could not be updated.")
        return {
            "id": updated["id"],
            "email": updated["email"],
            "phone_e164": updated["phone_e164"],
            "role": updated["role"],
            "campus_id": updated["campus_id"],
        }

    @app.get("/api/health")
    def health() -> dict:
        with request_matcher(settings.default_campus_id) as m:
            reports = store.list_reports(m.con, include_pending=True, campus_id=settings.default_campus_id)
            return {
                "ok": True,
                "reports": len(reports),
                "pairs": len(store.truth_pairs(m.con, campus_id=settings.default_campus_id)),
            }

    @app.get("/api/public/stats")
    def public_stats(user: CurrentUser | None = Depends(optional_current_user)) -> dict:
        campus_id = request_campus_id(user)
        with request_matcher(campus_id) as m:
            visible = store.list_reports(m.con, campus_id=campus_id)
            return {
                "reports": len(visible),
                "lost": sum(1 for report in visible if report.kind == "lost"),
                "found": sum(1 for report in visible if report.kind == "found"),
                "pairs": len(store.truth_pairs(m.con, campus_id=campus_id)),
            }

    @app.get("/api/config/operating-point")
    def operating_point() -> dict:
        """The thresholds the served scorer actually applies.

        When a calibration is fitted for that scorer, its measured thresholds
        replace the configured defaults and the UI bands are remapped onto the
        same scale, so the meter and the decision can never disagree.
        """
        effective = calibration.operating_point(rerank.active_scorer())
        alert_at = effective["prob_alert"]
        calibrated = effective["calibration_version"] is not None
        payload = {
            "plausible_floor": effective["plausible_floor"],
            "prob_alert": alert_at,
            "margin_min": effective["margin_min"],
            "bands": {
                "strong": round(alert_at + (1.0 - alert_at) / 2, 4) if calibrated else settings.strong_band,
                "probable": round(alert_at, 4) if calibrated else settings.probable_band,
                "weak": round(effective["plausible_floor"], 4) if calibrated else settings.weak_band,
            },
            "models": {"text": TEXT_EMBEDDER, "image": IMAGE_EMBEDDER},
        }
        # Added only when a calibration is actually serving, so the documented
        # operating-point contract is unchanged on a fresh checkout.
        if calibrated:
            payload["calibration_version"] = effective["calibration_version"]
        return payload

    @app.get("/api/model/info")
    def model_info() -> dict:
        """The trust page: which models decide, and what they measured.

        Every field answers "can I rely on this 78%?" — the encoder revisions
        that produced the vectors, the scorer, the calibration fit and its
        measured error, and the most recent offline evaluation row.
        """
        served = rerank.load()
        fitted = calibration.load()
        con = store.connect(app.state.db_path, initialize=False)
        try:
            stale = encoders.stale_vector_models(con)
        finally:
            con.close()
        return {
            "encoders": encoders.active_encoders(),
            "scorer": served.scorer if served is not None else rerank.POSE_SCORER,
            "reranker": (
                {
                    "version": served.version,
                    "trained_at": served.trained_at,
                    "train_size": served.train_size,
                    "metrics": served.metrics,
                }
                if served is not None
                else None
            ),
            "calibration": (
                {
                    "version": fitted.version,
                    "method": fitted.method,
                    "scorer": fitted.scorer,
                    "fit_size": fitted.fit_size,
                    "metrics": fitted.metrics,
                }
                if fitted is not None
                else None
            ),
            "stale_vector_models": stale,
            "last_eval": latest_results(),
            "operating_point": calibration.operating_point(served.scorer if served is not None else rerank.POSE_SCORER),
        }

    @app.get("/api/campus")
    def campus() -> dict:
        c = get_campus()
        return {"campus": c.to_dict(), "stats": c.stats()}

    @app.get("/api/reports")
    def reports(
        kind: str | None = Query(default=None, pattern="^(lost|found)$"),
        limit: int = 25,
        cursor: str | None = Query(default=None, max_length=512),
        user: CurrentUser | None = Depends(optional_current_user),
    ) -> dict:
        campus_id = request_campus_id(user)
        page_size = min(max(limit, 1), MAX_PAGE_SIZE)
        decoded_cursor = _decode_cursor(cursor, 2)
        report_cursor = (decoded_cursor[0], decoded_cursor[1]) if decoded_cursor else None
        with request_matcher(campus_id) as m:
            rows = store.list_reports(m.con, kind=kind, limit=page_size + 1, cursor=report_cursor, campus_id=campus_id)
            has_next = len(rows) > page_size
            page = rows[:page_size]
            next_cursor = _encode_cursor((page[-1].created_at, page[-1].id)) if has_next else None
            return {"count": len(page), "reports": [_report_json(r) for r in page], "next_cursor": next_cursor}

    @app.get("/api/reports/{report_id}")
    def report(report_id: str, user: CurrentUser | None = Depends(optional_current_user)) -> dict:
        campus_id = request_campus_id(user)
        with request_matcher(campus_id) as m:
            r = store.get_report(m.con, report_id, campus_id=campus_id)
            if r is None or r.status == "pending_review":
                raise HTTPException(status_code=404, detail="report not found")
            rows = m.con.execute(
                """SELECT matches.* FROM matches
                   JOIN reports AS candidate ON candidate.id = matches.candidate_id
                   WHERE matches.query_id=? AND COALESCE(candidate.campus_id, ?) = ?
                   ORDER BY matches.prob DESC LIMIT 5""",
                (report_id, settings.default_campus_id, campus_id),
            ).fetchall()
            import json

            return {
                "report": _report_json(r),
                "matches": [
                    {
                        "candidate_id": row["candidate_id"],
                        "prob": row["prob"],
                        "score": row["score"],
                        "decision": row["decision"],
                        "reasons": json.loads(row["reasons_json"]),
                    }
                    for row in rows
                ],
            }

    @app.get("/api/reports/{report_id}/status")
    def report_status(report_id: str, user: CurrentUser | None = Depends(optional_current_user)) -> dict:
        campus_id = request_campus_id(user)
        with request_matcher(campus_id) as m:
            report = m.by_id.get(report_id)
            if report is None:
                raise HTTPException(status_code=404, detail="report not found")
            if report.status == "pending_review":
                return {"report_id": report_id, "status": "held_for_review"}
        if app.state.match_queue is None:
            return {"report_id": report_id, "status": "synchronous"}
        status = app.state.match_queue.get_status(report_id)
        if status is None:
            return {"report_id": report_id, "status": "not_queued"}
        return {"report_id": report_id, **status}

    @app.post("/api/extract", dependencies=[Depends(extract_rate_limit)])
    def extract_attributes(payload: ExtractRequest) -> dict:
        """Live preview for the report form: what did we read from the text?"""
        attrs = extract(payload.description).attrs
        colour = attrs.primary_color
        return {
            "attrs": attrs.to_dict(),
            "category": attrs.category,
            "completeness": attrs.completeness(),
            "primary_color_hex": COLORS.get(colour) if colour else None,
        }

    def file_report(
        m: Matcher,
        *,
        kind: str,
        description: str,
        node_key: str | None,
        latitude: float | None,
        longitude: float | None,
        location_accuracy_m: float | None,
        lost_from: str | None,
        lost_to: str | None,
        owner_user_id: str | None,
        campus_id: str,
        contact_email: str | None,
        contact_phone: str | None,
        website: str = "",
        form_started_at: float | None = None,
        reporter_role: str = "student",
        photos: list[bytes] | None = None,
    ) -> dict:
        """Shared ingest path for text-only and photo reports."""
        con = m.con
        campus = get_campus()
        if (latitude is None) != (longitude is None):
            raise HTTPException(status_code=422, detail="Latitude and longitude must be provided together.")
        # Both kinds can be pinned: a found item's exact spot, or where the owner
        # last had the lost item. Two points are what makes GPS distance evidence
        # possible at all (features.measure -> lostfound/geo.py).
        if location_accuracy_m is not None and latitude is None:
            raise HTTPException(status_code=422, detail="Location accuracy requires coordinates.")
        if location_accuracy_m is not None and location_accuracy_m < 0:
            raise HTTPException(status_code=422, detail="Location accuracy cannot be negative.")
        rid = store.next_id(con, "LR" if kind == "lost" else "FR")
        node = node_key if node_key in campus.nodes else None
        now_dt = now_utc()
        now = iso_utc(now_dt)
        # A lost report is an interval. With nothing more to go on, assume the
        # owner last had it within the last day -- not exactly *now*, which
        # would make every earlier find look impossible.
        default_from = iso_utc(now_dt - dt.timedelta(hours=24))

        if latitude is not None:
            location_note = "Last seen location" if kind == "lost" else "Current location"
        else:
            location_note = campus.label(node) if node else ""

        ownership_token = secrets.token_urlsafe(32)
        photo_payloads = photos or []
        if len(photo_payloads) > MAX_PHOTOS:
            raise HTTPException(status_code=422, detail=f"up to {MAX_PHOTOS} photos are supported")
        try:
            image_keys = save_report_photos(photo_payloads, rid) if photo_payloads else []
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        image_path = image_keys[0] if image_keys else None
        r = Report(
            id=rid,
            kind=kind,
            created_at=now,
            description=description,
            attrs={},
            node_key=node,
            location_note=location_note,
            latitude=latitude,
            longitude=longitude,
            location_accuracy_m=location_accuracy_m,
            lost_from=lost_from if lost_from else (default_from if kind == "lost" else None),
            lost_to=lost_to if lost_to else (now if kind == "lost" else None),
            status=(
                "pending_review"
                if needs_review(description, website=website, form_started_at=form_started_at)
                else "open"
            ),
            reporter_role=reporter_role,
            image_path=image_path,
            has_photo=bool(image_path),
            is_synthetic_art=False,
            campus_id=campus_id,
            campus_timezone=store.campus_timezone(con, campus_id),
        )
        attrs = enrich_report(r)
        r.attrs = attrs.to_dict()
        if len(image_keys) > 1:
            # Additional photo keys travel in the report payload; the first photo
            # stays in reports.image_path so every existing reader keeps working.
            r.attrs["_photo_keys"] = image_keys[1:]
        r.category = attrs.category
        store.insert_report(con, r)
        store.add_report_owner(
            con,
            r.id,
            user_id=owner_user_id,
            contact_email=contact_email,
            contact_phone=contact_phone,
            token_hash=hashlib.sha256(ownership_token.encode("utf-8")).hexdigest(),
        )

        if not settings.async_matching:
            if r.description:
                store.put_vector(
                    con,
                    r.id,
                    "text",
                    TEXT_EMBEDDER,
                    text_embedder(TEXT_EMBEDDER).encode(r.description),
                )
            if image_keys:
                embedder = image_embedder(IMAGE_EMBEDDER)
                vectors = [embedder.encode(img) for key in image_keys if (img := open_image(key)) is not None]
                pooled = aggregate_image_vector(vectors)
                if pooled is not None:
                    store.put_vector(con, r.id, "image", IMAGE_EMBEDDER, pooled)
        con.commit()
        update_matcher_cache(m, [r])
        response = {
            "report": _report_json(r),
            "date": now_dt.date().isoformat(),
            "ownership_token": ownership_token,
        }
        if settings.async_matching and r.status != "pending_review":
            try:
                app.state.match_queue.enqueue("rematch_report", {"report_id": r.id})
            except RedisError:
                log.exception(
                    "Report was saved but matching could not be queued",
                    extra={"event": "job_enqueue_failed", "report_id": r.id},
                )
                response["matching_status"] = "failed_to_queue"
                response["matching_error"] = "Background matching is unavailable; use the match action to retry."
            else:
                response["matching_status"] = "queued"
        elif settings.async_matching:
            response["matching_status"] = "held_for_review"
        return response

    @app.post("/api/reports", dependencies=[Depends(report_rate_limit)])
    def create_report(
        payload: NewReport,
        user: CurrentUser | None = Depends(optional_current_user),
    ) -> dict:
        contact_email, contact_phone = _report_contact(payload.contact_email, payload.contact_phone, user)
        campus_id = request_campus_id(user)
        with request_matcher(campus_id) as m:
            return file_report(
                m,
                kind=payload.kind,
                description=payload.description,
                node_key=payload.node_key,
                latitude=payload.latitude,
                longitude=payload.longitude,
                location_accuracy_m=payload.location_accuracy_m,
                lost_from=payload.lost_from,
                lost_to=payload.lost_to,
                owner_user_id=user.id if user else None,
                campus_id=campus_id,
                contact_email=contact_email,
                contact_phone=contact_phone,
                reporter_role=user.role if user else "student",
                website=payload.website,
                form_started_at=payload.form_started_at,
            )

    @app.post("/api/reports/{report_id}/claim", dependencies=[Depends(report_rate_limit)])
    def claim_report(
        report_id: str,
        payload: ClaimRequest,
        user: CurrentUser | None = Depends(optional_current_user),
    ) -> dict:
        campus_id = request_campus_id(user)
        with request_matcher(campus_id) as m:
            if store.get_report(m.con, report_id, campus_id=campus_id) is None:
                raise HTTPException(status_code=404, detail="report not found")
            expected_hash = store.report_owner_token_hash(m.con, report_id)
        if expected_hash is None:
            raise HTTPException(status_code=404, detail="report not found")
        supplied_hash = hashlib.sha256(payload.ownership_token.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(expected_hash, supplied_hash):
            raise HTTPException(status_code=403, detail="Invalid ownership token.")
        return {"ok": True, "report_id": report_id, "status": "ownership_verified"}

    def claim_pair(m: Matcher, query_id: str, candidate_id: str) -> tuple[dict, Report]:
        """The lost side of a plausible pair, or the right HTTP error.

        The question set is drawn from the lost report, so it is only handed out
        for a pair the scorer already considers plausible: a stranger cannot use
        this endpoint to fish attributes out of arbitrary found reports.
        """
        try:
            snap = m.snapshot(query_id, candidate_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        lost = m.by_id.get(snap["lost_id"]) or store.get_report(m.con, snap["lost_id"])
        if lost is None:
            raise HTTPException(status_code=404, detail="report not found")
        floor = calibration.operating_point(rerank.active_scorer())["plausible_floor"]
        if snap["prob"] < floor:
            raise HTTPException(
                status_code=422,
                detail="These two reports are not a plausible match, so there is nothing to claim.",
            )
        return snap, lost

    @app.get("/api/claims/challenge", dependencies=[Depends(extract_rate_limit)])
    def claim_challenge(
        query_id: str = Query(min_length=1, max_length=64),
        candidate_id: str = Query(min_length=1, max_length=64),
        user: CurrentUser | None = Depends(optional_current_user),
    ) -> dict:
        campus_id = request_campus_id(user)
        with request_matcher(campus_id) as m:
            _, lost = claim_pair(m, query_id, candidate_id)
            questions = recovery.challenge_for(lost)
            return {
                "lost_id": lost.id,
                "min_questions": recovery.MIN_QUESTIONS,
                "questions": questions,
                "verifiable_online": len(questions) >= recovery.MIN_QUESTIONS,
            }

    @app.post("/api/claims", dependencies=[Depends(report_rate_limit)])
    def create_claim(
        payload: ClaimAnswers,
        user: CurrentUser | None = Depends(optional_current_user),
    ) -> dict:
        campus_id = request_campus_id(user)
        with request_matcher(campus_id) as m:
            snap, lost = claim_pair(m, payload.query_id, payload.candidate_id)
            claimant_email = (payload.contact_email or "").strip().lower() or (user.email if user else None)
            claim_id = f"CL-{uuid.uuid4().hex[:10]}"
            token = recovery.new_claim_token()
            questions = recovery.challenge_for(lost)

            if len(questions) < recovery.MIN_QUESTIONS:
                # Too few distinguishing details to prove ownership online. The
                # claim is recorded for the desk rather than silently refused.
                store.add_claim(
                    m.con,
                    claim_id=claim_id,
                    query_id=lost.id,
                    candidate_id=snap["found_id"],
                    campus_id=campus_id,
                    claimant_user_id=user.id if user else None,
                    claimant_email=claimant_email,
                    token_hash=recovery.hash_token(token),
                    challenge=questions,
                    score=snap["prob"],
                    status="pending",
                )
                m.con.commit()
                return {
                    "claim_id": claim_id,
                    "claim_token": token,
                    "status": "pending",
                    "needs_staff": True,
                    "reason": (
                        "This report has too few distinguishing details to verify online. "
                        "The desk will check it against the item."
                    ),
                }

            verified, score = recovery.grade(lost, payload.answers)
            if not verified:
                # Deliberately says nothing about which answer was wrong, and no
                # claim row is created: the report rate limit is the guess budget.
                raise HTTPException(
                    status_code=403,
                    detail="Those answers do not match this report. Check what you filed and try again.",
                )

            code = recovery.new_handover_code()
            store.add_claim(
                m.con,
                claim_id=claim_id,
                query_id=lost.id,
                candidate_id=snap["found_id"],
                campus_id=campus_id,
                claimant_user_id=user.id if user else None,
                claimant_email=claimant_email,
                token_hash=recovery.hash_token(token),
                challenge=questions,
                score=snap["prob"],
                status="verified",
            )
            store.update_claim(
                m.con,
                claim_id,
                status="verified",
                handover_hash=recovery.hash_token(code),
                verified_at=iso_utc(now_utc()),
            )
            store.add_audit(
                m.con,
                actor_id=user.id if user else None,
                action="claim.verified",
                entity="claim",
                entity_id=claim_id,
                after={"query_id": lost.id, "candidate_id": snap["found_id"]},
            )
            notify.queue(
                m.con,
                dedupe_key=f"claim-verified:{claim_id}",
                subject=f"Claim {claim_id} verified - collect at the lost property desk",
                body=(
                    f"Your claim for {lost.id} was verified. Show the six-digit handover code "
                    "from the claim page at the desk; the code is not repeated by e-mail."
                ),
                audience="owner",
                report_id=lost.id,
                recipient=claimant_email,
            )
            m.con.commit()
            try:
                notify.dispatch(m.con)
            except Exception:  # noqa: BLE001 - delivery is best effort
                log.exception("Notification dispatch failed", extra={"event": "notify_failed", "report_id": lost.id})
            return {
                "claim_id": claim_id,
                "claim_token": token,
                "status": "verified",
                "needs_staff": False,
                "handover_code": code,
                "note": "This code is shown once. Bring it, and photo ID, to the lost property desk.",
            }

    @app.post("/api/claims/{claim_id}/lookup")
    def lookup_claim(claim_id: str, payload: ClaimLookup) -> dict:
        con = store.connect(app.state.db_path, initialize=False)
        try:
            claim = store.get_claim(con, claim_id)
            if claim is None or not recovery.token_matches(claim["token_hash"], payload.token):
                raise HTTPException(status_code=404, detail="claim not found")
            return {
                "claim_id": claim["id"],
                "status": claim["status"],
                "query_id": claim["query_id"],
                "candidate_id": claim["candidate_id"],
                "created_at": claim["created_at"],
                "verified_at": claim["verified_at"],
                "collected_at": claim["collected_at"],
            }
        finally:
            con.close()

    @app.get("/api/claims")
    def claims_list(
        user: Annotated[CurrentUser, Depends(require_role("staff"))],
        status: str | None = Query(default=None, pattern="^(pending|verified|collected|rejected|expired)$"),
        limit: int = 25,
    ) -> dict:
        campus_id = request_campus_id(user)
        con = store.connect(app.state.db_path, initialize=False)
        try:
            claims = store.list_claims(con, status=status, limit=min(max(limit, 1), MAX_PAGE_SIZE), campus_id=campus_id)
            # The code hash is never handed out; it is a verifier, not data.
            for claim in claims:
                claim.pop("handover_hash", None)
                claim.pop("token_hash", None)
            return {"count": len(claims), "claims": claims}
        finally:
            con.close()

    @app.post("/api/claims/{claim_id}/collect")
    def collect_claim(
        claim_id: str,
        payload: CollectRequest,
        user: Annotated[CurrentUser, Depends(require_role("staff"))],
    ) -> dict:
        """Close the counter: verify the handover code and mark both reports recovered."""
        campus_id = request_campus_id(user)
        with request_matcher(campus_id) as m:
            con = m.con
            claim = store.get_claim(con, claim_id, campus_id=campus_id)
            if claim is None:
                raise HTTPException(status_code=404, detail="claim not found")
            if claim["status"] == "collected":
                raise HTTPException(status_code=409, detail="This claim was already collected.")
            if claim["status"] != "verified":
                raise HTTPException(status_code=409, detail="This claim still needs staff verification.")

            attempts = store.increment_claim_attempts(con, claim_id)
            allowed = recovery.max_attempts()
            if attempts > allowed:
                store.update_claim(con, claim_id, status="expired")
                con.commit()
                raise HTTPException(status_code=429, detail="Too many wrong codes; the claim is closed.")
            if not recovery.code_matches(claim["handover_hash"], payload.code):
                con.commit()
                remaining = max(allowed - attempts, 0)
                raise HTTPException(
                    status_code=403,
                    detail=f"That handover code does not match. {remaining} attempt(s) left.",
                )

            store.update_claim(con, claim_id, status="collected", collected_at=iso_utc(now_utc()))
            for report_id in (claim["query_id"], claim["candidate_id"]):
                store.set_status(con, report_id, "recovered")
            store.add_audit(
                con,
                actor_id=user.id,
                action="claim.collected",
                entity="claim",
                entity_id=claim_id,
                before={"status": "verified"},
                after={"status": "collected", "query_id": claim["query_id"], "candidate_id": claim["candidate_id"]},
            )
            owner = store.report_contact(con, claim["query_id"])
            notify.queue(
                con,
                dedupe_key=f"claim-collected:{claim_id}",
                subject=f"{claim['query_id']} has been handed over",
                body="Your item was collected at the lost property desk. This case is now closed.",
                audience="owner",
                report_id=claim["query_id"],
                recipient=claim["claimant_email"] or owner.get("contact_email"),
            )
            con.commit()
            updated: list[Report] = []
            for report_id in (claim["query_id"], claim["candidate_id"]):
                report = store.get_report(con, report_id)
                if report is not None:
                    updated.append(report)
            update_matcher_cache(m, updated)
            try:
                notify.dispatch(con)
            except Exception:  # noqa: BLE001 - delivery is best effort
                log.exception(
                    "Notification dispatch failed", extra={"event": "notify_failed", "report_id": claim["query_id"]}
                )
            return {
                "ok": True,
                "claim_id": claim_id,
                "status": "collected",
                "reports": [report.id for report in updated],
            }

    @app.post("/api/reports/upload", dependencies=[Depends(report_rate_limit)])
    async def create_report_with_photo(
        kind: str = Form(pattern="^(lost|found)$"),
        # Optional: "descriptions and/or photographs" means a photo on its own is
        # a valid report. The photo is required on this endpoint, so the pair
        # (description, photo) can never both be empty here.
        description: str = Form(default="", max_length=1000),
        node_key: str | None = Form(default=None),
        latitude: float | None = Form(default=None),
        longitude: float | None = Form(default=None),
        location_accuracy_m: float | None = Form(default=None),
        lost_from: str | None = Form(default=None),
        lost_to: str | None = Form(default=None),
        contact_email: str | None = Form(default=None, max_length=254),
        contact_phone: str | None = Form(default=None, max_length=20),
        website: str = Form(default="", max_length=250),
        form_started_at: float | None = Form(default=None, ge=0),
        # `photo` keeps the original single-file contract; `photos` is the
        # multi-image form. Both are accepted so existing clients keep working.
        photo: UploadFile | None = File(default=None),
        photos: list[UploadFile] = File(default=[]),
        user: CurrentUser | None = Depends(optional_current_user),
    ) -> dict:
        uploads = ([photo] if photo is not None else []) + list(photos)
        if not uploads:
            raise HTTPException(status_code=422, detail="Attach at least one photo.")
        if len(uploads) > MAX_PHOTOS:
            raise HTTPException(status_code=422, detail=f"up to {MAX_PHOTOS} photos are supported")
        payloads: list[bytes] = []
        for upload in uploads:
            data = await _read_upload_bytes(upload)
            if not _has_supported_image_signature(data):
                raise HTTPException(status_code=400, detail="photo must be a JPEG or PNG image")
            payloads.append(data)
        resolved_email, resolved_phone = _report_contact(contact_email, contact_phone, user)
        campus_id = request_campus_id(user)
        try:
            with request_matcher(campus_id) as m:
                return file_report(
                    m,
                    kind=kind,
                    description=description,
                    node_key=node_key or None,
                    latitude=latitude,
                    longitude=longitude,
                    location_accuracy_m=location_accuracy_m,
                    lost_from=lost_from,
                    lost_to=lost_to,
                    owner_user_id=user.id if user else None,
                    campus_id=campus_id,
                    contact_email=resolved_email,
                    contact_phone=resolved_phone,
                    reporter_role=user.role if user else "student",
                    website=website,
                    form_started_at=form_started_at,
                    photos=payloads,
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/match/{report_id}", dependencies=[Depends(match_rate_limit)])
    def match(
        report_id: str,
        top: int = 5,
        user: CurrentUser | None = Depends(optional_current_user),
    ) -> dict:
        campus_id = request_campus_id(user)
        with request_matcher(campus_id) as m:
            try:
                result = m.match(report_id, top_k=top)
                if user is None or user.role not in {"staff", "campus_admin", "superadmin"}:
                    result["feedback"] = {}
                return result
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/feedback")
    def record_feedback(
        payload: FeedbackRequest,
        user: Annotated[CurrentUser, Depends(require_role("staff"))],
    ) -> dict:
        """Staff verdict on one candidate. This is the label the re-ranker trains on."""
        campus_id = request_campus_id(user)
        with request_matcher(campus_id) as m:
            con = m.con
            try:
                snap = m.snapshot(payload.query_id, payload.candidate_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

            store.add_feedback(
                con,
                query_id=payload.query_id,
                candidate_id=payload.candidate_id,
                action=payload.action,
                prob=snap["prob"],
                score=snap["score"],
                features={
                    "lost_id": snap["lost_id"],
                    "found_id": snap["found_id"],
                    "feats": snap["feats"],
                    "flags": snap["flags"],
                },
                campus_id=campus_id,
                staff=user.email or user.id,
                note=payload.note,
            )
            status = "matched" if payload.action == "confirm" else "open"
            store.set_status(con, payload.query_id, status)
            store.set_status(con, payload.candidate_id, status)
            con.commit()
            updated_reports: list[Report] = []
            for report_id in (payload.query_id, payload.candidate_id):
                updated_report = store.get_report(con, report_id)
                if updated_report is not None:
                    updated_reports.append(updated_report)
            update_matcher_cache(m, updated_reports)
            if payload.action == "confirm":
                owner = store.report_contact(con, snap["lost_id"])
                notify.queue(
                    con,
                    dedupe_key=f"match-confirmed:{snap['lost_id']}:{snap['found_id']}",
                    subject=f"A match was confirmed for {snap['lost_id']}",
                    body=(
                        f"Staff confirmed that {snap['lost_id']} and {snap['found_id']} are the same item. "
                        "Open your report to start the claim, or visit the lost property desk."
                    ),
                    audience="owner",
                    report_id=snap["lost_id"],
                    recipient=owner.get("contact_email"),
                )
                con.commit()
                try:
                    notify.dispatch(con)
                except Exception:  # noqa: BLE001 - delivery is best effort
                    log.exception(
                        "Notification dispatch failed",
                        extra={"event": "notify_failed", "report_id": snap["lost_id"]},
                    )
            return {
                "ok": True,
                "query_id": payload.query_id,
                "candidate_id": payload.candidate_id,
                "action": payload.action,
                "status": status,
                "counts": store.feedback_counts(con, campus_id=campus_id),
            }

    @app.get("/api/feedback")
    def get_feedback(
        user: Annotated[CurrentUser, Depends(require_role("staff"))],
        query_id: str | None = None,
        limit: int = 25,
        cursor: str | None = Query(default=None, max_length=512),
    ) -> dict:
        campus_id = request_campus_id(user)
        with request_matcher(campus_id) as m:
            if query_id:
                if query_id not in m.by_id:
                    raise HTTPException(status_code=404, detail="report not found")
                return {"labels": store.feedback_for_query(m.con, query_id, campus_id=campus_id)}
            page_size = min(max(limit, 1), MAX_PAGE_SIZE)
            decoded_cursor = _decode_cursor(cursor, 3)
            feedback_cursor = (decoded_cursor[0], decoded_cursor[1], decoded_cursor[2]) if decoded_cursor else None
            items = store.list_feedback(m.con, limit=page_size + 1, cursor=feedback_cursor, campus_id=campus_id)
            has_next = len(items) > page_size
            page = items[:page_size]
            next_cursor = None
            if has_next:
                last = page[-1]
                next_cursor = _encode_cursor((last["created_at"], last["query_id"], last["candidate_id"]))
            return {
                "counts": store.feedback_counts(m.con, campus_id=campus_id),
                "count": len(page),
                "items": page,
                "next_cursor": next_cursor,
            }

    @app.get("/api/stats")
    def stats(user: Annotated[CurrentUser, Depends(require_role("staff"))]) -> dict:
        campus_id = request_campus_id(user)
        with request_matcher(campus_id) as m:
            con = m.con
            by_cat: dict[str, int] = {}
            reports = store.list_reports(con, campus_id=campus_id)
            for r in reports:
                by_cat[r.category or "unknown"] = by_cat.get(r.category or "unknown", 0) + 1
            return {
                "reports": len(reports),
                "lost": sum(r.kind == "lost" for r in reports),
                "found": sum(r.kind == "found" for r in reports),
                "pairs": len(store.truth_pairs(con, campus_id=campus_id)),
                "matched": sum(1 for r in reports if r.status == "matched"),
                **store.feedback_counts(con, campus_id=campus_id),
                "by_category": dict(sorted(by_cat.items(), key=lambda kv: -kv[1])),
            }

    @app.get("/media/{object_key:path}", include_in_schema=False)
    def serve_local_media(
        object_key: str,
        expires: int = Query(),
        signature: str = Query(min_length=64, max_length=64),
    ) -> Response:
        object_store = get_object_store()
        if isinstance(object_store, LocalObjectStore):
            local_store = object_store
        elif object_key.startswith("legacy/"):
            local_store = get_local_object_store()
        else:
            raise HTTPException(status_code=404, detail="media not found")
        if not local_store.verify_signed_url(object_key, expires, signature):
            raise HTTPException(status_code=403, detail="media URL is invalid or expired")
        try:
            data = local_store.read(object_key)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="media not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="media not found") from exc
        media_type = mimetypes.guess_type(object_key)[0] or "image/jpeg"
        return Response(content=data, media_type=media_type, headers={"Cache-Control": "private, no-store"})

    # Serve the built single-page UI from the same origin when it exists, so
    # `cli serve` is a one-command demo. In development the Vite dev server
    # proxies /api and /media here instead.
    dist = ROOT / "web" / "dist"
    if (dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="ui")
    return app


app = create_app()
