"""Opt-in checks against a real Redis-compatible rate-limit service."""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from lostfound.ratelimit import RateLimiter


@pytest.mark.redis_live
def test_redis_sliding_window_enforces_limit():
    redis_url = os.getenv("REDIS_TEST_URL")
    if not redis_url:
        pytest.skip("Set REDIS_TEST_URL to run the live Redis rate-limit test.")

    limiter = RateLimiter(redis_url)
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/test",
            "raw_path": b"/api/test",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
        }
    )
    bucket = f"redis-live-test:{uuid.uuid4().hex}"
    try:
        limiter.enforce(request, bucket, 1, 60)
        with pytest.raises(HTTPException) as exc_info:
            limiter.enforce(request, bucket, 1, 60)
        assert exc_info.value.status_code == 429
    finally:
        if limiter._redis is not None:
            limiter._redis.close()
