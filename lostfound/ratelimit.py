"""Shared Redis-backed sliding-window rate limits with a local dev backend."""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import HTTPException, Request, status
from redis import Redis
from redis.exceptions import RedisError

_SLIDING_WINDOW = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    return {0, math.ceil(window - (now - tonumber(oldest[2])))}
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, window)
return {1, 0}
"""


class RateLimiter:
    def __init__(self, redis_url: str | None, *, required: bool = False, disabled: bool = False):
        if required and not redis_url:
            raise ValueError("REDIS_URL is required in production.")
        self._redis = Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2) if redis_url else None
        self._disabled = disabled
        self._lock = threading.Lock()
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._failures: dict[str, tuple[int, float]] = {}
        self._locked_until: dict[str, float] = {}

    @staticmethod
    def _ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    @classmethod
    def _key(cls, request: Request, bucket: str, *, session: bool = False) -> str:
        ip = cls._ip(request)
        token = request.headers.get("authorization", "")
        if session and token.lower().startswith("bearer "):
            token_hash = hashlib.sha256(token[7:].encode("utf-8")).hexdigest()[:20]
            return f"{bucket}:{ip}:{token_hash}"
        return f"{bucket}:{ip}"

    def enforce(self, request: Request, bucket: str, limit: int, window_seconds: int, *, session: bool = False) -> None:
        if self._disabled:
            return
        key = self._key(request, bucket, session=session)
        now = time.time()
        if self._redis is not None:
            try:
                allowed, retry_after = self._redis.eval(
                    _SLIDING_WINDOW,
                    1,
                    key,
                    now,
                    window_seconds,
                    limit,
                    f"{now}:{uuid.uuid4().hex}",
                )
            except RedisError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Rate-limit service is unavailable.",
                ) from exc
            if not int(allowed):
                raise self._too_many(int(retry_after))
            return

        with self._lock:
            events = self._events[key]
            cutoff = now - window_seconds
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                raise self._too_many(max(1, int(window_seconds - (now - events[0]))))
            events.append(now)

    def check_login_lockout(self, request: Request) -> None:
        if self._disabled:
            return
        key = self._key(request, "auth:locked")
        if self._redis is not None:
            try:
                ttl = self._redis.ttl(key)
            except RedisError as exc:
                raise HTTPException(status_code=503, detail="Rate-limit service is unavailable.") from exc
            if ttl > 0:
                raise self._too_many(ttl, "Login temporarily locked after repeated failures.")
            return
        with self._lock:
            until = self._locked_until.get(key, 0)
            if until > time.monotonic():
                raise self._too_many(int(until - time.monotonic()), "Login temporarily locked after repeated failures.")
            self._locked_until.pop(key, None)

    def record_login_failure(self, request: Request) -> None:
        if self._disabled:
            return
        key = self._key(request, "auth:failures")
        locked_key = self._key(request, "auth:locked")
        if self._redis is not None:
            try:
                count = self._redis.incr(key)
                if count == 1:
                    self._redis.expire(key, 900)
                if count >= 5:
                    self._redis.set(locked_key, "1", ex=900)
            except RedisError as exc:
                raise HTTPException(status_code=503, detail="Rate-limit service is unavailable.") from exc
            return
        with self._lock:
            count, expires_at = self._failures.get(key, (0, time.monotonic() + 900))
            if expires_at <= time.monotonic():
                count, expires_at = 0, time.monotonic() + 900
            count += 1
            self._failures[key] = (count, expires_at)
            if count >= 5:
                self._locked_until[locked_key] = time.monotonic() + 900

    def clear_login_failures(self, request: Request) -> None:
        if self._disabled:
            return
        key = self._key(request, "auth:failures")
        locked_key = self._key(request, "auth:locked")
        if self._redis is not None:
            try:
                self._redis.delete(key, locked_key)
            except RedisError as exc:
                raise HTTPException(status_code=503, detail="Rate-limit service is unavailable.") from exc
            return
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(locked_key, None)

    @staticmethod
    def _too_many(retry_after: int, detail: str = "Rate limit exceeded.") -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
            headers={"Retry-After": str(max(1, retry_after))},
        )


def rate_limit(bucket: str, limit: int, window_seconds: int, *, session: bool = False) -> Callable:
    def enforce(request: Request) -> None:
        request.app.state.rate_limiter.enforce(request, bucket, limit, window_seconds, session=session)

    return enforce
