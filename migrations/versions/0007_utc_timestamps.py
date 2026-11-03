"""normalize persisted timestamp strings to UTC

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from alembic import context

from lostfound.db import SQLAlchemyConnection
from lostfound.settings import settings
from lostfound.timeutil import normalize_legacy_timestamps

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE schema_metadata (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    if context.is_offline_mode():
        timezone = settings.default_campus_timezone.replace("'", "''")
        for table, key, campus_expr, fields in (
            (
                "reports",
                "id",
                f"COALESCE((SELECT timezone FROM campuses WHERE campuses.id=reports.campus_id), '{timezone}')",
                ("created_at", "lost_from", "lost_to"),
            ),
            (
                "feedback",
                "query_id",
                f"COALESCE((SELECT timezone FROM campuses WHERE campuses.id=COALESCE(feedback.campus_id, (SELECT campus_id FROM reports WHERE reports.id=feedback.query_id))), '{timezone}')",
                ("created_at",),
            ),
            (
                "matches",
                "query_id",
                f"COALESCE((SELECT timezone FROM campuses WHERE campuses.id=(SELECT campus_id FROM reports WHERE reports.id=matches.query_id)), '{timezone}')",
                ("created_at",),
            ),
            (
                "report_owners",
                "report_id",
                f"COALESCE((SELECT timezone FROM campuses WHERE campuses.id=(SELECT campus_id FROM reports WHERE reports.id=report_owners.report_id)), '{timezone}')",
                ("created_at",),
            ),
            ("users", "id", f"'{timezone}'", ("created_at",)),
            ("audit_log", "id", f"'{timezone}'", ("created_at",)),
        ):
            for field in fields:
                op.execute(
                    f"""UPDATE {table} SET {field}=CASE WHEN {field} IS NULL THEN NULL ELSE
                    to_char(CASE WHEN {field} ~ '(Z|[+-][0-9]{{2}}:[0-9]{{2}})$'
                    THEN {field}::timestamptz AT TIME ZONE 'UTC'
                    ELSE {field}::timestamp AT TIME ZONE {campus_expr} AT TIME ZONE 'UTC' END,
                    'YYYY-MM-DD"T"HH24:MI:SS"Z"') END"""
                )
        op.execute(
            """INSERT INTO schema_metadata (key, value) VALUES ('timestamp_format', 'utc-v1')
               ON CONFLICT(key) DO UPDATE SET value=excluded.value"""
        )
    else:
        normalize_legacy_timestamps(SQLAlchemyConnection(op.get_bind()), settings.default_campus_timezone)


def downgrade() -> None:
    op.execute("DROP TABLE schema_metadata")
