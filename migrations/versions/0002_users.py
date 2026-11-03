"""add application user roles

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE users (
            id          TEXT PRIMARY KEY,
            email       TEXT UNIQUE,
            phone_e164  TEXT,
            role        TEXT NOT NULL DEFAULT 'student'
                        CHECK (role IN ('student','staff','campus_admin','superadmin')),
            campus_id   TEXT,
            created_at  TEXT NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE users")
