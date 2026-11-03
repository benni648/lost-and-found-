"""Idempotently backfill a SQLite demo database into PostgreSQL + pgvector.

Run Alembic migrations through revision 0009 before starting the backfill.
Without ``--target`` (or ``DATABASE_URL``), this only inspects the source.
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import struct
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TypeVar

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lostfound.settings import settings  # noqa: E402

BATCH_SIZE = 500
RowT = TypeVar("RowT")
TABLE_SPECS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("campuses", ("id",), ("id", "name", "short", "domain", "latitude", "longitude", "timezone", "status")),
    ("users", ("id",), ("id", "email", "phone_e164", "role", "campus_id", "created_at")),
    ("id_sequences", ("prefix",), ("prefix", "next_value")),
    (
        "reports",
        ("id",),
        (
            "id",
            "kind",
            "status",
            "created_at",
            "lost_from",
            "lost_to",
            "node_key",
            "location_note",
            "latitude",
            "longitude",
            "location_accuracy_m",
            "description",
            "lang",
            "category",
            "attrs_json",
            "image_path",
            "has_photo",
            "is_synthetic_art",
            "reporter_role",
            "campus_id",
            "owner_user_id",
            "deleted_at",
            "pending_review",
        ),
    ),
    (
        "report_owners",
        ("report_id",),
        ("report_id", "user_id", "contact_email", "contact_phone", "token_hash", "created_at"),
    ),
    ("ground_truth", ("item_id",), ("item_id", "lost_id", "found_id", "label")),
    (
        "matches",
        ("query_id", "candidate_id"),
        ("query_id", "candidate_id", "score", "prob", "decision", "reasons_json", "created_at"),
    ),
    (
        "feedback",
        ("query_id", "candidate_id"),
        (
            "query_id",
            "candidate_id",
            "action",
            "staff",
            "note",
            "prob",
            "score",
            "features_json",
            "created_at",
            "campus_id",
            "model_version",
            "operating_point_version",
        ),
    ),
    (
        "audit_log",
        ("id",),
        (
            "id",
            "actor_id",
            "action",
            "entity",
            "entity_id",
            "before_json",
            "after_json",
            "created_at",
            "ip",
            "request_id",
        ),
    ),
)
REQUIRED_SOURCE_COLUMNS: dict[str, set[str]] = {
    "campuses": {"id", "name", "short"},
    "users": {"id", "role", "created_at"},
    "id_sequences": {"prefix", "next_value"},
    "reports": {"id", "kind", "status", "created_at", "description", "attrs_json"},
    "report_owners": {"report_id", "token_hash", "created_at"},
    "ground_truth": {"item_id"},
    "matches": {"query_id", "candidate_id", "score", "prob", "decision", "reasons_json", "created_at"},
    "feedback": {"query_id", "candidate_id", "action", "features_json", "created_at"},
    "audit_log": {"id", "action", "entity", "entity_id", "created_at"},
}
TARGET_COLUMNS = {table: set(columns) for table, _, columns in TABLE_SPECS}
TARGET_COLUMNS["report_embeddings"] = {"report_id", "kind", "model", "dim", "embedding"}


def _source_tables(connection: sqlite3.Connection) -> set[str]:
    return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _source_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}  # noqa: S608


def inspect(source: Path) -> dict[str, int]:
    """Return row counts for source tables, treating absent older tables as empty."""
    if not source.exists():
        raise FileNotFoundError(f"no SQLite database at {source}")
    con = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    try:
        tables = _source_tables(con)
        counts = {
            table: int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608
            for table, _, _ in TABLE_SPECS
            if table in tables
        }
        vector_tables = [table for table in ("vectors", "report_embeddings") if table in tables]
        counts["vectors"] = sum(
            int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608
            for table in vector_tables
        )
        return counts
    finally:
        con.close()


def encode_vector_blob(payload: bytes, dimension: int) -> str:
    """Convert the runtime's float32 BLOB into pgvector's text input format."""
    if dimension < 1 or len(payload) != dimension * 4:
        raise ValueError(f"vector payload size does not match dimension {dimension}")
    values = struct.unpack(f"<{dimension}f", payload)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("vector payload contains a non-finite value")
    return "[" + ",".join(format(value, ".9g") for value in values) + "]"


def _batches(rows: Iterator[RowT], batch_size: int = BATCH_SIZE) -> Iterator[list[RowT]]:
    while batch := list(_take(rows, batch_size)):
        yield batch


def _take(rows: Iterator[RowT], size: int) -> Iterator[RowT]:
    for _ in range(size):
        try:
            yield next(rows)
        except StopIteration:
            return


def _upsert_sql(table: str, columns: tuple[str, ...], keys: tuple[str, ...], *, vector: bool = False) -> str:
    allowed_columns = TARGET_COLUMNS.get(table)
    if (
        allowed_columns is None
        or not columns
        or not keys
        or not set(columns) <= allowed_columns
        or not set(keys) <= set(columns)
        or (vector and table != "report_embeddings")
    ):
        raise ValueError("backfill SQL identifiers must match the fixed target schema")
    quoted_columns = ", ".join(f'"{column}"' for column in columns)
    values = ", ".join(
        f"CAST(:{column} AS vector)" if vector and column == "embedding" else f":{column}" for column in columns
    )
    conflict_keys = ", ".join(f'"{key}"' for key in keys)
    updates = [column for column in columns if column not in keys]
    if updates:
        action = "DO UPDATE SET " + ", ".join(f'"{column}" = EXCLUDED."{column}"' for column in updates)
    else:
        action = "DO NOTHING"
    return (
        f'INSERT INTO "{table}" ({quoted_columns}) VALUES ({values}) '  # noqa: S608
        f"ON CONFLICT ({conflict_keys}) {action}"  # noqa: S608
    )


def _selected_columns(table: str, available_columns: set[str], target_columns: tuple[str, ...]) -> tuple[str, ...]:
    selected = tuple(column for column in target_columns if column in available_columns)
    if table == "reports" and "pending_review" not in selected:
        selected += ("pending_review",)
    return selected


def _mapped_rows(
    source: sqlite3.Connection,
    table: str,
    available_columns: set[str],
    target_columns: tuple[str, ...],
) -> Iterator[dict[str, Any]]:
    selected = _selected_columns(table, available_columns, target_columns)
    if not selected:
        return
    source_selected = tuple(column for column in selected if column in available_columns)
    quoted_columns = ", ".join('"' + column + '"' for column in source_selected)
    query = f'SELECT {quoted_columns} FROM "{table}"'  # noqa: S608
    for row in source.execute(query):
        values = {column: row[column] for column in source_selected}
        if table == "reports":
            values.setdefault("pending_review", int(values.get("status") == "pending_review"))
        yield values


def _embedding_rows(source: sqlite3.Connection, source_table: str, columns: set[str]) -> Iterator[dict[str, Any]]:
    blob_column = "vec" if "vec" in columns else "embedding"
    required = {"report_id", "kind", "model", "dim", blob_column}
    if not required <= columns:
        missing = sorted(required - columns)
        raise ValueError(f"source {source_table} table is missing columns: {', '.join(missing)}")
    query = f'SELECT "report_id", "kind", "model", "dim", "{blob_column}" FROM "{source_table}"'  # noqa: S608
    for row in source.execute(query):
        yield {
            "report_id": row["report_id"],
            "kind": row["kind"],
            "model": row["model"],
            "dim": int(row["dim"]),
            "embedding": encode_vector_blob(row[blob_column], int(row["dim"])),
        }


def _target_table_columns(connection: Any, table: str) -> set[str]:
    from sqlalchemy import text  # noqa: PLC0415

    columns = connection.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = :table"
        ),
        {"table": table},
    ).scalars().all()
    if not columns:
        raise RuntimeError(f"PostgreSQL schema is missing table {table!r}; run Alembic upgrade head first.")
    return set(columns)


def backfill(source_path: Path, target_url: str | None = None) -> dict[str, int]:
    """Upsert source rows into a migrated PostgreSQL database in one transaction."""
    if not source_path.exists():
        raise FileNotFoundError(f"no SQLite database at {source_path}")
    raw_target = target_url if target_url is not None else settings.database_url
    if not raw_target:
        raise ValueError("provide --target or configure DATABASE_URL for PostgreSQL")

    try:
        from sqlalchemy.engine import make_url  # noqa: PLC0415
        from sqlalchemy.exc import ArgumentError  # noqa: PLC0415
    except ImportError as exc:
        message = "install database dependencies with pip install -e '.[postgres,migrations]'"
        raise ImportError(message) from exc

    from sqlalchemy import text  # noqa: PLC0415

    from lostfound.db import create_database_engine  # noqa: PLC0415

    normalized_target = (
        "postgresql://" + raw_target.removeprefix("postgres://") if raw_target.startswith("postgres://") else raw_target
    )
    try:
        url = make_url(normalized_target)
    except ArgumentError as exc:
        raise ValueError("target must be a valid PostgreSQL URL") from exc
    if url.get_backend_name() != "postgresql":
        raise ValueError("backfill target must be PostgreSQL")

    source = sqlite3.connect(f"{source_path.resolve().as_uri()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    engine = None
    counts: dict[str, int] = {}
    try:
        engine = create_database_engine(raw_target)
        assert engine is not None
        source_tables = _source_tables(source)
        if "reports" not in source_tables:
            raise ValueError("SQLite source is missing the reports table")
        with engine.begin() as target:
            extension = target.execute(
                text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
            ).scalar_one()
            if not extension:
                raise RuntimeError("PostgreSQL extension pgvector is not installed; run Alembic upgrade head.")

            for table, keys, columns in TABLE_SPECS:
                if table not in source_tables:
                    counts[table] = 0
                    continue
                available = _source_columns(source, table)
                missing_required = REQUIRED_SOURCE_COLUMNS[table] - available
                if missing_required:
                    raise ValueError(
                        f"source {table} table is missing required columns: {', '.join(sorted(missing_required))}"
                    )
                selected = _selected_columns(table, available, columns)
                missing_keys = set(keys) - set(selected)
                if missing_keys:
                    raise ValueError(f"source {table} table is missing key columns: {', '.join(sorted(missing_keys))}")
                target_columns = _target_table_columns(target, table)
                if not set(selected) <= target_columns:
                    missing = sorted(set(selected) - target_columns)
                    raise RuntimeError(f"PostgreSQL {table} table is missing columns: {', '.join(missing)}")
                statement = text(_upsert_sql(table, selected, keys))
                count = 0
                for batch in _batches(_mapped_rows(source, table, available, columns)):
                    target.execute(statement, batch)
                    count += len(batch)
                counts[table] = count

            source_embedding_tables = [table for table in ("vectors", "report_embeddings") if table in source_tables]
            embedding_count = 0
            for source_embedding_table in source_embedding_tables:
                vector_columns = _source_columns(source, source_embedding_table)
                embedding_target_columns = _target_table_columns(target, "report_embeddings")
                required_embedding_columns = {"report_id", "kind", "model", "dim", "embedding"}
                if not required_embedding_columns <= embedding_target_columns:
                    raise RuntimeError("PostgreSQL report_embeddings schema is incomplete; run Alembic upgrade head.")
                statement = text(
                    _upsert_sql(
                        "report_embeddings",
                        ("report_id", "kind", "model", "dim", "embedding"),
                        ("report_id", "kind", "model"),
                        vector=True,
                    )
                )
                for batch in _batches(_embedding_rows(source, source_embedding_table, vector_columns)):
                    target.execute(statement, batch)
                    embedding_count += len(batch)
            counts["vectors"] = embedding_count
    finally:
        source.close()
        if engine is not None:
            engine.dispose()
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Idempotently backfill SQLite into PostgreSQL + pgvector")
    parser.add_argument("--source", default=None, help="SQLite file (default: settings DB_PATH)")
    parser.add_argument("--target", default=None, help="PostgreSQL URL (defaults to DATABASE_URL)")
    args = parser.parse_args(argv)

    source = Path(args.source) if args.source else settings.database
    try:
        if args.target or settings.database_url:
            counts = backfill(source, args.target)
            print(f"Backfill complete from {source}:")
            for table, count in counts.items():
                print(f"  {table:<18} {count}")
            print("Repeated runs update matching primary keys and do not duplicate rows.")
            return 0

        counts = inspect(source)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (ValueError, RuntimeError, sqlite3.Error, ImportError) as exc:
        print(f"backfill failed: {exc}", file=sys.stderr)
        return 3

    total = sum(counts.values())
    print(f"source: {source}")
    for table, count in counts.items():
        print(f"  {table:<18} {count}")
    print(f"  {'total':<18} {total}")
    if total == 0:
        print("\nNothing to migrate: run `python -m lostfound.cli seed` first.", file=sys.stderr)
        return 1
    print("\nInspect-only: provide --target or configure DATABASE_URL to backfill.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
