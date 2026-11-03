"""Structured logging and request correlation.

One JSON object per line in production so logs stay greppable and machine
parseable; a human-readable format for local development. Every line emitted
while handling a request carries that request's id, which is also returned in
the ``X-Request-ID`` response header so a user-reported problem can be traced
from the browser back to the exact log line.

Phase 9 adds metrics, traces and error reporting on top of this skeleton.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

# Set per request by RequestContextMiddleware; readable from anywhere that runs
# inside that request, including the sync threadpool endpoints.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes we lift out of a LogRecord and onto the JSON payload.
_EXTRA_KEYS = ("method", "path", "status", "duration_ms", "client", "event", "report_id")


def current_request_id() -> str | None:
    return request_id_var.get()


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


class JsonFormatter(logging.Formatter):
    """Emit one compact JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None) or request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        for key in _EXTRA_KEYS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """Readable single-line output for local development."""

    def format(self, record: logging.LogRecord) -> str:
        request_id = getattr(record, "request_id", None) or request_id_var.get()
        prefix = f"[{request_id}] " if request_id else ""
        parts = [f"{self.formatTime(record, '%H:%M:%S')}", f"{record.levelname:<7}", prefix + record.getMessage()]
        for key in ("status", "duration_ms", "method", "path"):
            value = getattr(record, key, None)
            if value is not None:
                parts.append(f"{key}={value}")
        line = " ".join(parts)
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Install the root handler. Idempotent, so tests can call it freely."""
    formatter: logging.Formatter = TextFormatter() if fmt == "text" else JsonFormatter()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Uvicorn installs its own handlers; route them through ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, log one line per request, and echo the id back."""

    def __init__(self, app: ASGIApp, header: str = "X-Request-ID") -> None:
        super().__init__(app)
        self.header = header

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get(self.header) or new_request_id()
        token = request_id_var.set(rid)
        logger = get_logger("lostfound.request")
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.exception(
                "request failed",
                extra={
                    "request_id": rid,
                    "method": request.method,
                    "path": request.url.path,
                    "status": 500,
                    "duration_ms": duration_ms,
                },
            )
            request_id_var.reset(token)
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.info(
            "request",
            extra={
                "request_id": rid,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        response.headers[self.header] = rid
        request_id_var.reset(token)
        return response


__all__ = [
    "JsonFormatter",
    "RequestContextMiddleware",
    "TextFormatter",
    "configure_logging",
    "current_request_id",
    "get_logger",
    "new_request_id",
    "request_id_var",
]
