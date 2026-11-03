"""add the recovery loop (claims) and the notification outbox

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-30
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Kept byte-identical (modulo whitespace) to `store.DDL`: the migration parity
# test compares the two schemas directly, so a drifted copy fails the suite.
_CLAIMS = """
CREATE TABLE IF NOT EXISTS claims (
    id               TEXT PRIMARY KEY,
    query_id         TEXT NOT NULL,
    candidate_id     TEXT NOT NULL,
    campus_id        TEXT,
    claimant_user_id TEXT,
    claimant_email   TEXT,
    token_hash       TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','verified','collected','rejected','expired')),
    challenge_json   TEXT NOT NULL,
    score            REAL NOT NULL DEFAULT 0,
    attempts         INTEGER NOT NULL DEFAULT 0,
    handover_hash    TEXT,
    created_at       TEXT NOT NULL,
    verified_at      TEXT,
    collected_at     TEXT
)
"""

_NOTIFICATIONS = """
CREATE TABLE IF NOT EXISTS notifications (
    id         TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    audience   TEXT NOT NULL DEFAULT 'owner'
               CHECK (audience IN ('owner','staff')),
    report_id  TEXT,
    recipient  TEXT,
    subject    TEXT NOT NULL,
    body       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending','sent','failed','suppressed')),
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    sent_at    TEXT
)
"""


def upgrade() -> None:
    op.execute(_CLAIMS)
    op.execute(_NOTIFICATIONS)
    op.execute("CREATE INDEX IF NOT EXISTS idx_claims_query ON claims(query_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_notifications_status ON notifications(status, created_at)")

    if op.get_bind().dialect.name == "postgresql":
        # The rest of the schema stores timestamps as native timestamptz on
        # PostgreSQL (revision 0009); these tables start that way.
        for table, columns in (("claims", ("created_at", "verified_at", "collected_at")),):
            for column in columns:
                op.execute(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" TYPE TIMESTAMPTZ USING "{column}"::timestamptz')
        op.execute('ALTER TABLE "notifications" ALTER COLUMN "created_at" TYPE TIMESTAMPTZ USING "created_at"::timestamptz')
        op.execute('ALTER TABLE "notifications" ALTER COLUMN "sent_at" TYPE TIMESTAMPTZ USING "sent_at"::timestamptz')


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_notifications_status")
    op.execute("DROP INDEX IF EXISTS idx_claims_status")
    op.execute("DROP INDEX IF EXISTS idx_claims_query")
    op.execute("DROP TABLE IF EXISTS notifications")
    op.execute("DROP TABLE IF EXISTS claims")
