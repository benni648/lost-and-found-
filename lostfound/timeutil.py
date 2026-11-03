"""UTC timestamp parsing and one-time normalization for persisted records."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

UTC = dt.UTC
DEFAULT_TIMEZONE = "Asia/Kolkata"
TIMESTAMP_FORMAT_VERSION = "utc-v1"


def parse_utc(value: str | dt.datetime, source_timezone: str = DEFAULT_TIMEZONE) -> dt.datetime:
    """Parse an ISO timestamp as aware UTC; naive legacy values use campus time."""
    parsed = dt.datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(source_timezone))
    return parsed.astimezone(UTC)


def iso_utc(value: str | dt.datetime, source_timezone: str = DEFAULT_TIMEZONE) -> str:
    """Return a canonical ISO-8601 UTC value with a trailing ``Z``."""
    return parse_utc(value, source_timezone).isoformat(timespec="seconds").replace("+00:00", "Z")


def now_utc() -> dt.datetime:
    return dt.datetime.now(UTC)


def normalize_legacy_timestamps(connection, default_timezone: str = DEFAULT_TIMEZONE) -> None:
    """Upgrade timestamp strings in place exactly once, preserving campus wall time."""
    marker = connection.execute("SELECT value FROM schema_metadata WHERE key='timestamp_format'").fetchone()
    if marker is not None and marker["value"] == TIMESTAMP_FORMAT_VERSION:
        return

    timezones = {row["id"]: row["timezone"] for row in connection.execute("SELECT id, timezone FROM campuses")}

    def timezone_for(campus_id: str | None) -> str:
        timezone = timezones.get(campus_id) if campus_id else None
        selected = timezone or default_timezone
        ZoneInfo(selected)
        return selected

    def update_rows(
        query: str,
        update: str,
        keys: tuple[str, ...],
        fields: tuple[str, ...],
        resolve_timezone,
    ) -> None:
        for row in connection.execute(query):
            timezone = resolve_timezone(row)
            values = [iso_utc(row[field], timezone) if row[field] is not None else None for field in fields]
            connection.execute(update, (*values, *(row[key] for key in keys)))

    update_rows(
        "SELECT id, campus_id, created_at, lost_from, lost_to FROM reports",
        "UPDATE reports SET created_at=?, lost_from=?, lost_to=? WHERE id=?",
        ("id",),
        ("created_at", "lost_from", "lost_to"),
        lambda row: timezone_for(row["campus_id"]),
    )
    update_rows(
        """SELECT f.query_id, f.candidate_id, f.created_at, f.campus_id, r.campus_id AS report_campus_id
           FROM feedback AS f LEFT JOIN reports AS r ON r.id=f.query_id""",
        "UPDATE feedback SET created_at=? WHERE query_id=? AND candidate_id=?",
        ("query_id", "candidate_id"),
        ("created_at",),
        lambda row: timezone_for(row["campus_id"] or row["report_campus_id"]),
    )
    for row in connection.execute(
        """SELECT m.query_id, m.candidate_id, m.created_at, r.campus_id
           FROM matches AS m LEFT JOIN reports AS r ON r.id=m.query_id"""
    ):
        connection.execute(
            "UPDATE matches SET created_at=? WHERE query_id=? AND candidate_id=?",
            (
                iso_utc(row["created_at"], timezone_for(row["campus_id"])),
                row["query_id"],
                row["candidate_id"],
            ),
        )
    for row in connection.execute(
        """SELECT o.report_id, o.created_at, r.campus_id
           FROM report_owners AS o LEFT JOIN reports AS r ON r.id=o.report_id"""
    ):
        connection.execute(
            "UPDATE report_owners SET created_at=? WHERE report_id=?",
            (iso_utc(row["created_at"], timezone_for(row["campus_id"])), row["report_id"]),
        )
    for row in connection.execute("SELECT id, created_at FROM users"):
        connection.execute(
            "UPDATE users SET created_at=? WHERE id=?",
            (iso_utc(row["created_at"], default_timezone), row["id"]),
        )
    for row in connection.execute("SELECT id, created_at FROM audit_log"):
        connection.execute(
            "UPDATE audit_log SET created_at=? WHERE id=?",
            (iso_utc(row["created_at"], default_timezone), row["id"]),
        )

    connection.execute(
        """INSERT INTO schema_metadata (key, value) VALUES ('timestamp_format', ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
        (TIMESTAMP_FORMAT_VERSION,),
    )
