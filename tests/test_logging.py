"""Request correlation and structured logging.

Phase 0 promises that every request carries an id which is both logged and
returned to the client, so a user-reported problem can be traced back to the
exact log line.
"""

from __future__ import annotations

import json
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lostfound.logging import JsonFormatter, RequestContextMiddleware, TextFormatter, request_id_var


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    return app


def test_a_request_id_is_generated_and_echoed_back():
    with TestClient(_app()) as client:
        response = client.get("/ping")
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID")


def test_an_incoming_request_id_is_preserved():
    with TestClient(_app()) as client:
        response = client.get("/ping", headers={"X-Request-ID": "trace-me-123"})
    assert response.headers["X-Request-ID"] == "trace-me-123"


def test_ids_differ_between_requests():
    with TestClient(_app()) as client:
        first = client.get("/ping").headers["X-Request-ID"]
        second = client.get("/ping").headers["X-Request-ID"]
    assert first != second


def test_json_formatter_emits_structured_payload_with_the_request_id():
    token = request_id_var.set("rid-1")
    try:
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello", None, None)
        record.status = 200
        record.duration_ms = 1.5
        payload = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)

    assert payload["msg"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "rid-1"
    assert payload["status"] == 200
    assert payload["duration_ms"] == 1.5


def test_text_formatter_stays_human_readable():
    record = logging.LogRecord("test", logging.WARNING, __file__, 1, "careful", None, None)
    line = TextFormatter().format(record)
    assert "careful" in line
    assert "WARNING" in line
