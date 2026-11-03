from __future__ import annotations

import json

import pytest
from redis.exceptions import ResponseError

from lostfound.queue import Job, RedisJobQueue, process_one


class FakeRedis:
    def __init__(self) -> None:
        self.added = []
        self.acked = []
        self.read_entries = []
        self.group_created = False
        self.status = {}

    def xadd(self, stream, fields):
        self.added.append((stream, fields))
        return f"{len(self.added)}-0"

    def xgroup_create(self, stream, group, id, mkstream):
        if self.group_created:
            raise ResponseError("BUSYGROUP Consumer Group name already exists")
        self.group_created = True

    def xautoclaim(self, stream, group, consumer, min_idle_time, start_id, count):
        return ["0-0", []]

    def xreadgroup(self, group, consumer, streams, count, block):
        if not self.read_entries:
            return []
        return [(next(iter(streams)), [self.read_entries.pop(0)])]

    def xack(self, stream, group, job_id):
        self.acked.append((stream, group, job_id))

    def hset(self, key, mapping):
        self.status.setdefault(key, {}).update(mapping)

    def expire(self, key, seconds):
        return key in self.status

    def hgetall(self, key):
        return dict(self.status.get(key, {}))

    def pipeline(self, transaction):
        assert transaction is True
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, client):
        self.client = client
        self.commands = []

    def xadd(self, stream, fields):
        self.commands.append(("xadd", (stream, fields)))
        return self

    def hset(self, key, mapping):
        self.commands.append(("hset", (key, mapping)))
        return self

    def expire(self, key, seconds):
        self.commands.append(("expire", (key, seconds)))
        return self

    def execute(self):
        results = []
        for name, args in self.commands:
            results.append(getattr(self.client, name)(*args))
        return results


def _queue(fake: FakeRedis, **kwargs) -> RedisJobQueue:
    return RedisJobQueue("redis://unused", client=fake, block_ms=0, reclaim_idle_ms=0, **kwargs)


def test_queue_enqueues_validated_jobs_as_stream_entries():
    fake = FakeRedis()
    queue = _queue(fake)

    job_id = queue.enqueue("rematch_report", {"report_id": "LR-0001"})

    assert job_id == "1-0"
    stream, fields = fake.added[0]
    assert stream == "lostfound:jobs"
    assert fields["name"] == "rematch_report"
    assert fields["attempt"] == "1"
    assert json.loads(fields["payload"]) == {"report_id": "LR-0001"}
    assert queue.get_status("LR-0001")["status"] == "queued"

    with pytest.raises(ValueError, match="unsupported job"):
        queue.enqueue("train_model", {})
    with pytest.raises(ValueError, match="report_id"):
        queue.enqueue("rematch_report", {})


def test_worker_acknowledges_successful_job():
    fake = FakeRedis()
    fake.read_entries.append(("5-0", {"name": "rematch_report", "payload": '{"report_id":"LR-0001"}', "attempt": "1"}))
    queue = _queue(fake)
    observed = []

    assert process_one(queue, lambda name, payload: observed.append((name, payload)))
    assert observed == [("rematch_report", {"report_id": "LR-0001"})]
    assert fake.acked == [("lostfound:jobs", "lostfound-workers", "5-0")]
    assert queue.get_status("LR-0001")["status"] == "complete"


def test_worker_retries_then_dead_letters_failed_jobs():
    fake = FakeRedis()
    fake.read_entries.append(("5-0", {"name": "rematch_report", "payload": '{"report_id":"LR-0001"}', "attempt": "1"}))
    queue = _queue(fake, max_attempts=2)
    job = Job("5-0", "rematch_report", {"report_id": "LR-0001"}, 1)

    queue.retry_or_dead_letter(job, RuntimeError("temporary failure"))
    assert fake.added[-1][0] == "lostfound:jobs"
    assert fake.added[-1][1]["attempt"] == "2"
    assert fake.acked[-1][-1] == "5-0"

    queue.retry_or_dead_letter(Job("6-0", job.name, job.payload, 2), RuntimeError("still failing"))
    assert fake.added[-1][0] == "lostfound:jobs:dead"
    assert fake.added[-1][1]["error"] == "RuntimeError: background job failed"
    assert fake.acked[-1][-1] == "6-0"
