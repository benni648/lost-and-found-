"""Report-matching worker with per-campus matcher snapshots.

Run with ``python -m lostfound.worker`` after configuring ``REDIS_URL``.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from redis.exceptions import RedisError

from . import store
from .config import IMAGE_EMBEDDER, TEXT_EMBEDDER, settings
from .embedders import image_embedder, text_embedder
from .logging import configure_logging, get_logger
from .matching import Matcher
from .pipeline import open_image
from .queue import JobExecutionError, RedisJobQueue, process_one


def _sqlalchemy_error_types() -> tuple[type[BaseException], ...]:
    try:
        from sqlalchemy.exc import SQLAlchemyError
    except ImportError:
        return ()
    return (SQLAlchemyError,)


class ReportMatchProcessor:
    """Maintain worker-local matchers and refresh them when campus data changes."""

    def __init__(self, database_path: Path | str | None = None) -> None:
        self.database_path = database_path
        self.matchers: dict[str, Matcher] = {}
        self.matcher_versions: dict[str, int] = {}

    def __call__(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name != "rematch_report":
            raise ValueError(f"unsupported job type: {name}")
        report_id = payload.get("report_id")
        if not isinstance(report_id, str) or not report_id:
            raise ValueError("rematch_report job is missing report_id")

        con = None
        try:
            con = store.connect(self.database_path, initialize=False)
            report = store.get_report(con, report_id)
            if report is None:
                raise LookupError(f"report not found: {report_id}")
            if report.status == "pending_review":
                return {"report_id": report_id, "status": "held_for_review"}

            campus_id = report.campus_id or settings.default_campus_id
            current_version = store.campus_match_version(con, campus_id)
            matcher = self.matchers.get(campus_id)
            if matcher is None or self.matcher_versions.get(campus_id) != current_version:
                matcher = Matcher.load(con, campus_id=campus_id)
                self.matchers[campus_id] = matcher
                self.matcher_versions[campus_id] = current_version
            matcher.con = con
            vectors_changed = False
            if store.get_vector(con, report.id, "text", TEXT_EMBEDDER) is None:
                store.put_vector(
                    con,
                    report.id,
                    "text",
                    TEXT_EMBEDDER,
                    text_embedder(TEXT_EMBEDDER).encode(report.description),
                )
                vectors_changed = True
            if report.image_path and store.get_vector(con, report.id, "image", IMAGE_EMBEDDER) is None:
                image = open_image(report.image_path)
                if image is None:
                    raise OSError(f"unable to read photo for report {report_id}")
                try:
                    store.put_vector(
                        con,
                        report.id,
                        "image",
                        IMAGE_EMBEDDER,
                        image_embedder(IMAGE_EMBEDDER).encode(image),
                    )
                finally:
                    image.close()
                vectors_changed = True
            if vectors_changed:
                store.bump_campus_match_version(con, campus_id)
                self.matcher_versions[campus_id] = store.campus_match_version(con, campus_id)
            matcher.upsert(report, con=con)
            affected_query_ids = {ranked.candidate.id for ranked in matcher.rank(report, top_k=200)}
            affected_query_ids.update(store.match_query_ids_for_candidate(con, report_id, campus_id))
            result = matcher.match(report_id)
            rematched_queries = 0
            for query_id in sorted(affected_query_ids):
                if query_id in matcher.by_id and query_id != report_id:
                    matcher.match(query_id)
                    rematched_queries += 1
            con.commit()
            return {
                "report_id": report_id,
                "status": "complete",
                "decision": result["decision"],
                "matches": len(result["matches"]),
                "affected_queries": rematched_queries,
            }
        except (KeyError, LookupError, OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
            raise JobExecutionError(f"unable to rematch report {report_id}: {exc}") from exc
        except _sqlalchemy_error_types() as exc:
            raise JobExecutionError(f"unable to rematch report {report_id}: {exc}") from exc
        finally:
            if con is not None:
                con.close()


def run_worker(
    *,
    redis_url: str | None = None,
    database_path: Path | str | None = None,
    stop_after: int | None = None,
) -> int:
    resolved_redis_url = redis_url or settings.redis_url
    if not resolved_redis_url:
        raise ValueError("REDIS_URL is required to run the background worker.")
    queue = RedisJobQueue(resolved_redis_url)
    processor = ReportMatchProcessor(database_path)
    log = get_logger("lostfound.worker")
    handled = 0
    log.info("Worker started", extra={"event": "worker_start"})
    while stop_after is None or handled < stop_after:
        try:
            did_work = process_one(queue, processor)
        except RedisError:
            log.exception("Redis queue operation failed", extra={"event": "worker_queue_error"})
            time.sleep(2)
            continue
        if did_work:
            handled += 1
        elif stop_after is not None:
            break
    return handled


def main() -> int:
    configure_logging(settings.log_level, settings.log_format)
    try:
        run_worker()
    except (RedisError, ValueError):
        logging.getLogger("lostfound.worker").exception("Unable to start background worker")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
