from __future__ import annotations

import importlib
import sqlite3
import struct

import pytest

from lostfound import store

backfill_script = importlib.import_module("scripts.backfill_sqlite_to_pg")
initial_migration = importlib.import_module("migrations.versions.0001_initial")


def test_vector_blob_is_encoded_for_pgvector():
    payload = struct.pack("<3f", 0.25, -0.5, 1.0)

    assert backfill_script.encode_vector_blob(payload, 3) == "[0.25,-0.5,1]"


@pytest.mark.parametrize(
    ("payload", "dimension", "message"),
    [
        (b"\x00\x00", 1, "does not match"),
        (struct.pack("<1f", float("nan")), 1, "non-finite"),
    ],
)
def test_invalid_vector_payload_fails_before_backfill(payload, dimension, message):
    with pytest.raises(ValueError, match=message):
        backfill_script.encode_vector_blob(payload, dimension)


def test_backfill_upsert_sql_updates_rows_by_primary_key():
    query = backfill_script._upsert_sql(
        "reports",
        ("id", "description", "pending_review"),
        ("id",),
    )

    assert 'INSERT INTO "reports"' in query
    assert 'ON CONFLICT ("id") DO UPDATE SET "description" = EXCLUDED."description"' in query
    assert '"pending_review" = EXCLUDED."pending_review"' in query


def test_backfill_sql_rejects_unrecognized_identifiers():
    with pytest.raises(ValueError, match="fixed target schema"):
        backfill_script._upsert_sql("reports; DROP TABLE reports", ("id",), ("id",))


def test_legacy_report_backfill_selection_includes_derived_pending_flag():
    selected = backfill_script._selected_columns("reports", {"id", "status"}, ("id", "status", "pending_review"))

    assert selected == ("id", "status", "pending_review")


def test_legacy_report_without_pending_review_gets_derived_flag(tmp_path):
    db = tmp_path / "source.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE reports (id TEXT, status TEXT)")
    con.execute("INSERT INTO reports VALUES ('LR-legacy', 'pending_review')")
    con.commit()
    con.row_factory = sqlite3.Row
    try:
        rows = backfill_script._mapped_rows(
            con,
            "reports",
            {"id", "status"},
            ("id", "status", "pending_review"),
        )
        assert next(rows) == {"id": "LR-legacy", "status": "pending_review", "pending_review": 1}
    finally:
        con.close()


def test_source_inspection_counts_database_rows(tmp_path):
    db = tmp_path / "source.db"
    con = store.connect(db)
    try:
        store.insert_report(
            con,
            store.Report(
                id="LR-0001",
                kind="lost",
                created_at="2026-10-28T00:00:00",
                description="test",
                attrs={},
            ),
        )
        con.commit()
    finally:
        con.close()

    counts = backfill_script.inspect(db)

    assert counts["reports"] == 1
    assert counts["vectors"] == 0


def test_backfill_refuses_a_non_postgres_target(tmp_path):
    source = tmp_path / "source.db"
    source.touch()

    with pytest.raises(ValueError, match="target must be PostgreSQL"):
        backfill_script.backfill(source, "sqlite:///not-a-target.db")


def test_initial_migration_uses_postgres_bytea_for_legacy_vector_storage():
    sqlite_statements = initial_migration._upgrade_statements("sqlite")
    postgres_statements = initial_migration._upgrade_statements("postgresql")
    sqlite_vectors = next(statement for statement in sqlite_statements if "CREATE TABLE vectors" in statement)
    postgres_vectors = next(statement for statement in postgres_statements if "CREATE TABLE vectors" in statement)

    assert "vec       BLOB NOT NULL" in sqlite_vectors
    assert "vec       BYTEA NOT NULL" in postgres_vectors
