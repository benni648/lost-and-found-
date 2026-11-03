"""Redis Streams queue for durable, retryable background jobs."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from redis import Redis
from redis.exceptions import RedisError, ResponseError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Job:
    id: str
    name: str
    payload: dict[str, Any]
    attempt: int


class JobExecutionError(RuntimeError):
    """A known operational failure that should be retried by the worker."""


class RedisJobQueue:
    def __init__(
        self,
        redis_url: str,
        *,
        stream: str = "lostfound:jobs",
        group: str = "lostfound-workers",
        consumer: str | None = None,
        block_ms: int = 5000,
        reclaim_idle_ms: int = 30_000,
        max_attempts: int = 5,
        client: Any | None = None,
    ) -> None:
        if not redis_url and client is None:
            raise ValueError("REDIS_URL is required for background jobs.")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.client: Any = client or Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=max(2, block_ms / 1000 + 2),
        )
        self.stream = stream
        self.group = group
        self.consumer = consumer or f"worker-{uuid.uuid4().hex}"
        self.block_ms = block_ms
        self.reclaim_idle_ms = reclaim_idle_ms
        self.max_attempts = max_attempts
        self.dead_letter_stream = f"{stream}:dead"

    def enqueue(self, name: str, payload: dict[str, Any]) -> str:
        if name != "rematch_report":
            raise ValueError(f"unsupported job type: {name}")
        if not isinstance(payload.get("report_id"), str) or not payload["report_id"]:
            raise ValueError("rematch_report jobs require a report_id")
        report_id = payload["report_id"]
        status_key = self._status_key(report_id)
        pipeline = self.client.pipeline(transaction=True)
        pipeline.xadd(
            self.stream,
            {"name": name, "payload": json.dumps(payload, separators=(",", ":")), "attempt": "1"},
        )
        pipeline.hset(status_key, mapping={"status": "queued", "updated_at": str(int(time.time()))})
        pipeline.expire(status_key, 7 * 24 * 60 * 60)
        result = pipeline.execute()
        return str(result[0])

    def reserve(self) -> Job | None:
        self._ensure_group()
        reclaimed = self.client.xautoclaim(
            self.stream,
            self.group,
            self.consumer,
            min_idle_time=self.reclaim_idle_ms,
            start_id="0-0",
            count=1,
        )
        entries = reclaimed[1] if len(reclaimed) > 1 else []
        if not entries:
            response = self.client.xreadgroup(
                self.group,
                self.consumer,
                {self.stream: ">"},
                count=1,
                block=self.block_ms,
            )
            if not response:
                return None
            entries = response[0][1]
        if not entries:
            return None
        job_id, fields = entries[0]
        try:
            name = fields["name"]
            payload = json.loads(fields["payload"])
            attempt = int(fields.get("attempt", "1"))
        except (KeyError, TypeError, ValueError) as exc:
            self._dead_letter(str(job_id), fields, f"invalid job message: {exc}")
            return None
        if not isinstance(payload, dict):
            self._dead_letter(str(job_id), fields, "job payload must be an object")
            return None
        return Job(str(job_id), name, payload, attempt)

    def acknowledge(self, job: Job) -> None:
        self.client.xack(self.stream, self.group, job.id)

    def mark_status(self, report_id: str, status: str, result: dict[str, Any] | None = None) -> None:
        if status not in {"queued", "processing", "retrying", "complete", "failed"}:
            raise ValueError(f"unsupported job status: {status}")
        values = {"status": status, "updated_at": str(int(time.time()))}
        if result is not None:
            values["result"] = json.dumps(result, separators=(",", ":"))
        key = self._status_key(report_id)
        self.client.hset(key, mapping=values)
        self.client.expire(key, 7 * 24 * 60 * 60)

    def get_status(self, report_id: str) -> dict[str, str] | None:
        values = self.client.hgetall(self._status_key(report_id))
        return values or None

    def retry_or_dead_letter(self, job: Job, error: Exception) -> None:
        fields = {
            "name": job.name,
            "payload": json.dumps(job.payload, separators=(",", ":")),
            "attempt": str(job.attempt + 1),
        }
        error_text = f"{type(error).__name__}: background job failed"
        if job.attempt >= self.max_attempts:
            self.client.xadd(self.dead_letter_stream, {**fields, "error": error_text})
            report_id = job.payload.get("report_id")
            if isinstance(report_id, str):
                self.mark_status(report_id, "failed", {"error": error_text})
            logger.error("Background job moved to dead-letter stream", extra={"job_id": job.id})
        else:
            self.client.xadd(self.stream, fields)
            report_id = job.payload.get("report_id")
            if isinstance(report_id, str):
                self.mark_status(report_id, "retrying")
            logger.warning(
                "Background job requeued",
                extra={"job_id": job.id, "attempt": job.attempt + 1},
            )
        self.client.xack(self.stream, self.group, job.id)

    def _ensure_group(self) -> None:
        try:
            self.client.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def _dead_letter(self, job_id: str, fields: dict[str, str], reason: str) -> None:
        self.client.xadd(self.dead_letter_stream, {**fields, "error": reason[:1000]})
        try:
            payload = json.loads(fields.get("payload", "{}"))
        except (TypeError, ValueError):
            payload = {}
        report_id = payload.get("report_id") if isinstance(payload, dict) else None
        if isinstance(report_id, str):
            self.mark_status(report_id, "failed", {"error": "invalid job message"})
        self.client.xack(self.stream, self.group, job_id)

    def _status_key(self, report_id: str) -> str:
        digest = hashlib.sha256(report_id.encode()).hexdigest()
        return f"{self.stream}:status:{digest}"


def process_one(queue: RedisJobQueue, handler) -> bool:
    """Run one reserved job. Handler errors are retried or dead-lettered."""
    job = queue.reserve()
    if job is None:
        return False
    try:
        report_id = job.payload.get("report_id")
        if isinstance(report_id, str):
            queue.mark_status(report_id, "processing")
        result = handler(job.name, job.payload)
    except JobExecutionError as exc:
        queue.retry_or_dead_letter(job, exc)
        return True
    report_id = job.payload.get("report_id")
    if isinstance(report_id, str):
        queue.mark_status(report_id, "complete", result if isinstance(result, dict) else None)
    queue.acknowledge(job)
    return True


__all__ = ["Job", "JobExecutionError", "RedisError", "RedisJobQueue", "process_one"]
