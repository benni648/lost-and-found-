"""store persisted timestamps as native PostgreSQL timestamptz

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-29
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TIMESTAMP_COLUMNS = {
    "reports": ("created_at", "lost_from", "lost_to", "deleted_at"),
    "users": ("created_at",),
    "report_owners": ("created_at",),
    "matches": ("created_at",),
    "feedback": ("created_at",),
    "audit_log": ("created_at",),
}


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, columns in _TIMESTAMP_COLUMNS.items():
        for column in columns:
            op.execute(
                f'ALTER TABLE "{table}" ALTER COLUMN "{column}" TYPE TIMESTAMPTZ '
                f'USING NULLIF("{column}"::text, \'\')::timestamptz'
            )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, columns in _TIMESTAMP_COLUMNS.items():
        for column in columns:
            op.execute(
                f'ALTER TABLE "{table}" ALTER COLUMN "{column}" TYPE TEXT '
                f'USING CASE WHEN "{column}" IS NULL THEN NULL '
                f"ELSE to_char(\"{column}\" AT TIME ZONE 'UTC', "
                "'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') END"
            )
