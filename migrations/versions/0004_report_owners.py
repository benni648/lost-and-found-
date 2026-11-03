"""add private report ownership records

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE report_owners (
            report_id     TEXT PRIMARY KEY,
            user_id       TEXT,
            contact_email TEXT,
            contact_phone TEXT,
            token_hash    TEXT NOT NULL,
            created_at    TEXT NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE report_owners")
