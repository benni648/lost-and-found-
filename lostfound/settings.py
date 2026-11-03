"""Runtime configuration — the single reader of the environment (12-factor).

Everything the app can be configured with lives here as a field on
:class:`Settings`. Other modules must **not** read ``os.environ`` directly; they
import the resolved values from :mod:`lostfound.config`, which re-exports this
object's fields under their historical names.

Precedence, highest first: process environment > ``.env`` file > defaults.
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# `lostfound/settings.py` -> parents[1] is the repository root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]

_DEFAULT_CORS = "http://localhost:5173,http://127.0.0.1:5173"


class Settings(BaseSettings):
    """All runtime configuration. Field names map to upper-case env vars."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------------------------------------------------------------- runtime
    env: str = "dev"

    # ------------------------------------------------------------------ paths
    data_dir: Path = PROJECT_ROOT / "data"
    # `None` means "derive from data_dir" — see the properties below, so a
    # DATA_DIR override moves media/campus/db together unless each is set.
    media_dir: Path | None = None
    campus_json: Path | None = None
    db_path: Path | None = None
    eval_dir: Path | None = None

    # ------------------------------------------------------------ database
    # PostgreSQL/pgvector is selected when DATABASE_URL is configured;
    # otherwise the SQLite demo database remains the default.
    database_url: str | None = None
    redis_url: str | None = None
    async_matching: bool = False
    default_campus_id: str = Field(default="default", min_length=1, max_length=128)
    default_campus_timezone: str = "Asia/Kolkata"

    # ----------------------------------------------------------- photo storage
    object_storage_backend: Literal["local", "s3"] = "local"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_region: str = "auto"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    photo_signing_secret: str | None = None

    # ----------------------------------------------------------- Supabase Auth
    supabase_url: str | None = None
    supabase_anon_key: str | None = None

    # ----------------------------------------------------------- corpus sim
    sim_now: dt.datetime = dt.datetime(2026, 10, 26, 9, 0, 0)
    sim_window_days: int = 21
    default_seed: int = 20261026
    default_item_count: int = 260

    # ------------------------------------------------------------ embeddings
    text_embedder: str = "hash-char-ngram-v1"
    image_embedder: str = "hist-grid-v1"

    # ---------------------------------------------------------- phase 3 models
    # Generated artifacts. They are absent on a fresh checkout, which is the
    # documented fallback: hand-set scorer + raw (uncalibrated) probability.
    calibration_file: Path | None = None
    reranker_file: Path | None = None

    # ------------------------------------------------------- operating point
    # The matcher's decision thresholds. Served to the UI so the two can never
    # disagree (see docs/DEVELOPMENT_PLAN.md, topic 6).
    plausible_floor: float = Field(default=0.30, ge=0, le=1)
    prob_alert: float = Field(default=0.55, ge=0, le=1)
    margin_min: float = Field(default=0.06, ge=0, le=1)
    # UI verdict bands, same single source of truth.
    strong_threshold: float = Field(default=0.75, ge=0, le=1)
    probable_threshold: float | None = Field(default=None, ge=0, le=1)
    weak_threshold: float | None = Field(default=None, ge=0, le=1)
    # Precision the auto-alert decision must clear when a calibrated scorer
    # derives its own threshold (Phase 3 calibration, Phase 10 routing).
    alert_precision_target: float = Field(default=0.90, ge=0, le=1)

    # ------------------------------------------------------- notifications
    # With no SMTP host, notifications are delivered to the structured log.
    # That is a real delivery path for a demo and a one-line swap for campus
    # IT to point at its own mail relay.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "lostfound@localhost"
    smtp_starttls: bool = True

    # --------------------------------------------------------- recovery loop
    claim_max_attempts: int = Field(default=5, ge=1, le=20)

    # ----------------------------------------------------------- web/server
    cors_origins: str = _DEFAULT_CORS
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # -------------------------------------------------------------- logging
    log_level: str = "INFO"
    log_format: str = "json"

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, value: str) -> str:
        origins = [origin.strip() for origin in value.split(",") if origin.strip()]
        if not origins:
            raise ValueError("CORS_ORIGINS must contain at least one explicit origin.")
        normalized: list[str] = []
        for origin in origins:
            parsed = urlsplit(origin)
            if parsed.path == "/" and not parsed.query and not parsed.fragment:
                origin = origin[:-1]
                parsed = urlsplit(origin)
            try:
                port = parsed.port
            except ValueError as exc:
                raise ValueError("CORS_ORIGINS contains an invalid port.") from exc
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or not parsed.hostname
                or "*" in parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username is not None
                or parsed.password is not None
                or (port is not None and not 1 <= port <= 65535)
            ):
                raise ValueError("CORS_ORIGINS must contain absolute HTTP(S) origins, not wildcards or paths.")
            normalized.append(origin)
        return ",".join(normalized)

    @field_validator("default_campus_timezone")
    @classmethod
    def validate_default_campus_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("DEFAULT_CAMPUS_TIMEZONE must be a valid IANA timezone.") from exc
        return value

    @model_validator(mode="after")
    def validate_operating_point(self) -> Settings:
        if self.prob_alert < self.plausible_floor:
            raise ValueError("PROB_ALERT must be greater than or equal to PLAUSIBLE_FLOOR.")
        if self.weak_band > self.probable_band:
            raise ValueError("UI verdict bands must be ordered weak <= probable <= strong.")
        return self

    # ------------------------------------------------------------- derived
    @property
    def media(self) -> Path:
        return self.media_dir or self.data_dir / "media"

    @property
    def campus_file(self) -> Path:
        return self.campus_json or self.data_dir / "campus.json"

    @property
    def database(self) -> Path:
        return self.db_path or self.data_dir / "lostfound.db"

    @property
    def evaluations(self) -> Path:
        return self.eval_dir or PROJECT_ROOT / "ml" / "eval"

    @property
    def calibration_path(self) -> Path:
        return self.calibration_file or PROJECT_ROOT / "ml" / "calibration" / "params.json"

    @property
    def reranker_path(self) -> Path:
        return self.reranker_file or PROJECT_ROOT / "ml" / "reranker" / "model.json"

    @property
    def cors_origin_list(self) -> list[str]:
        """Allow-list of browser origins, parsed from the comma-separated value."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def probable_band(self) -> float:
        return self.probable_threshold if self.probable_threshold is not None else self.prob_alert

    @property
    def strong_band(self) -> float:
        return max(self.strong_threshold, self.probable_band)

    @property
    def weak_band(self) -> float:
        return self.weak_threshold if self.weak_threshold is not None else self.plausible_floor

    @property
    def is_production(self) -> bool:
        return self.env.lower() in {"prod", "production"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Resolved settings, computed once per process."""
    return Settings()


settings = get_settings()

__all__ = ["PROJECT_ROOT", "Settings", "get_settings", "settings"]
