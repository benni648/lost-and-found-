"""track campus report changes for cross-process matcher invalidation

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE campus_match_versions (
            campus_id TEXT PRIMARY KEY,
            version   INTEGER NOT NULL DEFAULT 0
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE campus_match_versions")
