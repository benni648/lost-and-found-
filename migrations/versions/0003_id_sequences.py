"""add transactional report id allocation

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE id_sequences (
            prefix      TEXT PRIMARY KEY,
            next_value  INTEGER NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE id_sequences")
