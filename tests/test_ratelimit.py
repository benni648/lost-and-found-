from __future__ import annotations

import pytest
from fastapi import HTTPException
from redis.exceptions import RedisError
from starlette.requests import Request

from lostfound.ratelimit import RateLimiter


class _FakeRedis:
    def __init__(self):
        self.eval_results = [[1, 0], [0, 7]]
        self.eval_calls = []
        self.ttl_value = -2
        self.failure_count = 0
        self.expirations = []
        self.values = {}
        self.deleted = []

    def eval(self, *args):
        self.eval_calls.append(args)
        result = self.eval_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def ttl(self, key):
        return self.ttl_value

    def incr(self, key):
        self.failure_count += 1
        return self.failure_count

    def expire(self, key, seconds):
        self.expirations.append((key, seconds))

    def set(self, key, value, ex):
        self.values[key] = (value, ex)

    def delete(self, *keys):
        self.deleted.extend(keys)


def _request(authorization: str | None = None) -> Request:
    headers = []
    if authorization:
        headers.append((b"authorization", authorization.encode()))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/test",
            "raw_path": b"/api/test",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
        }
    )


def test_redis_sliding_window_calls_lua_and_returns_retry_after(monkeypatch):
    redis = _FakeRedis()
    monkeypatch.setattr("lostfound.ratelimit.Redis.from_url", lambda *args, **kwargs: redis)
    limiter = RateLimiter("redis://localhost")
    request = _request("Bearer sensitive-access-token")

    limiter.enforce(request, "match", 1, 60, session=True)
    assert redis.eval_calls[0][1] == 1
    assert redis.eval_calls[0][2].startswith("match:127.0.0.1:")

    with pytest.raises(HTTPException) as exc_info:
        limiter.enforce(request, "match", 1, 60, session=True)
    assert exc_info.value.status_code == 429
    assert exc_info.value.headers["Retry-After"] == "7"
    assert "sensitive-access-token" not in repr(redis.eval_calls)


def test_redis_backend_returns_503_when_limiter_is_unavailable(monkeypatch):
    redis = _FakeRedis()
    redis.eval_results = [RedisError("unavailable")]
    monkeypatch.setattr("lostfound.ratelimit.Redis.from_url", lambda *args, **kwargs: redis)
    limiter = RateLimiter("redis://localhost")

    with pytest.raises(HTTPException) as exc_info:
        limiter.enforce(_request(), "extract", 60, 60)
    assert exc_info.value.status_code == 503


def test_redis_login_lockout_sets_checks_and_clears_keys(monkeypatch):
    redis = _FakeRedis()
    monkeypatch.setattr("lostfound.ratelimit.Redis.from_url", lambda *args, **kwargs: redis)
    limiter = RateLimiter("redis://localhost")
    request = _request()

    for _ in range(5):
        limiter.record_login_failure(request)
    assert redis.failure_count == 5
    assert redis.expirations == [("auth:failures:127.0.0.1", 900)]
    assert redis.values["auth:locked:127.0.0.1"] == ("1", 900)

    redis.ttl_value = 120
    with pytest.raises(HTTPException) as exc_info:
        limiter.check_login_lockout(request)
    assert exc_info.value.status_code == 429
    assert exc_info.value.headers["Retry-After"] == "120"

    limiter.clear_login_failures(request)
    assert redis.deleted == ["auth:failures:127.0.0.1", "auth:locked:127.0.0.1"]


def test_production_rate_limiter_requires_redis():
    with pytest.raises(ValueError, match="REDIS_URL is required in production"):
        RateLimiter(None, required=True)
