"""add campus, audit, soft-delete, and pgvector foundations

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_REPORT_COLUMNS = (
    ("campus_id", "TEXT"),
    ("owner_user_id", "TEXT"),
    ("deleted_at", "TEXT"),
    ("pending_review", "INTEGER NOT NULL DEFAULT 0"),
)
_FEEDBACK_COLUMNS = (
    ("campus_id", "TEXT"),
    ("model_version", "TEXT"),
    ("operating_point_version", "TEXT"),
)


def upgrade() -> None:
    is_postgresql = op.get_bind().dialect.name == "postgresql"
    if is_postgresql:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    for column, definition in _REPORT_COLUMNS:
        op.execute(f"ALTER TABLE reports ADD COLUMN {column} {definition}")
    op.execute("UPDATE reports SET pending_review = 1 WHERE status = 'pending_review' AND pending_review = 0")
    for column, definition in _FEEDBACK_COLUMNS:
        op.execute(f"ALTER TABLE feedback ADD COLUMN {column} {definition}")

    op.execute(
        """
        CREATE TABLE campuses (
            id        TEXT PRIMARY KEY,
            name      TEXT NOT NULL,
            short     TEXT NOT NULL UNIQUE,
            domain    TEXT UNIQUE,
            latitude  REAL,
            longitude REAL,
            timezone  TEXT NOT NULL DEFAULT 'Asia/Kolkata',
            status    TEXT NOT NULL DEFAULT 'active'
        )
        """
    )
    embedding_type = "vector" if is_postgresql else "BLOB"
    op.execute(
        f"""
        CREATE TABLE report_embeddings (
            report_id  TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
            kind       TEXT NOT NULL,
            model      TEXT NOT NULL,
            dim        INTEGER NOT NULL,
            embedding  {embedding_type} NOT NULL,
            PRIMARY KEY (report_id, kind, model)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE audit_log (
            id          TEXT PRIMARY KEY,
            actor_id    TEXT,
            action      TEXT NOT NULL,
            entity      TEXT NOT NULL,
            entity_id   TEXT NOT NULL,
            before_json TEXT,
            after_json  TEXT,
            created_at  TEXT NOT NULL,
            ip          TEXT,
            request_id  TEXT
        )
        """
    )
    op.execute("CREATE INDEX idx_reports_campus_kind_created ON reports(campus_id, kind, created_at, id)")
    op.execute("CREATE INDEX idx_reports_location ON reports(latitude, longitude)")
    op.execute("CREATE INDEX idx_reports_not_deleted ON reports(deleted_at)")
    op.execute("CREATE INDEX idx_feedback_campus_created ON feedback(campus_id, created_at)")
    op.execute("CREATE INDEX idx_report_embeddings_model ON report_embeddings(kind, model, dim)")

    if is_postgresql:
        op.execute(
            """
            CREATE INDEX idx_report_embeddings_hash_text_hnsw
            ON report_embeddings USING hnsw ((embedding::vector(512)) vector_cosine_ops)
            WHERE kind = 'text' AND model = 'hash-char-ngram-v1' AND dim = 512
            """
        )
        op.execute(
            """
            CREATE INDEX idx_report_embeddings_hist_image_hnsw
            ON report_embeddings USING hnsw ((embedding::vector(256)) vector_cosine_ops)
            WHERE kind = 'image' AND model = 'hist-grid-v1' AND dim = 256
            """
        )
        op.execute(
            """
            CREATE INDEX idx_report_embeddings_sentence_text_hnsw
            ON report_embeddings USING hnsw ((embedding::vector(1024)) vector_cosine_ops)
            WHERE kind = 'text' AND model = 'sentence-transformers' AND dim = 1024
            """
        )
        op.execute(
            """
            CREATE INDEX idx_report_embeddings_dino_image_hnsw
            ON report_embeddings USING hnsw ((embedding::vector(384)) vector_cosine_ops)
            WHERE kind = 'image' AND model = 'dinov2-vits14' AND dim = 384
            """
        )


def downgrade() -> None:
    op.execute("DROP INDEX idx_report_embeddings_model")
    op.execute("DROP INDEX idx_feedback_campus_created")
    op.execute("DROP INDEX idx_reports_not_deleted")
    op.execute("DROP INDEX idx_reports_location")
    op.execute("DROP INDEX idx_reports_campus_kind_created")
    op.execute("DROP TABLE audit_log")
    op.execute("DROP TABLE report_embeddings")
    op.execute("DROP TABLE campuses")
    for column, _ in reversed(_FEEDBACK_COLUMNS):
        op.execute(f"ALTER TABLE feedback DROP COLUMN {column}")
    for column, _ in reversed(_REPORT_COLUMNS):
        op.execute(f"ALTER TABLE reports DROP COLUMN {column}")
