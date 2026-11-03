"""index match rows by candidate for affected-query refresh

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("idx_matches_candidate", "matches", ["candidate_id"])


def downgrade() -> None:
    op.drop_index("idx_matches_candidate", table_name="matches")
