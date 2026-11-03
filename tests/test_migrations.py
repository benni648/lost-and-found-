"""The initial migration must build exactly the schema the runtime expects.

This guards the Phase 0 deliverable: ``alembic upgrade head`` on an empty
database produces the same tables and indexes as ``store.connect()``, and
``downgrade base`` removes them again.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from lostfound import store

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TABLES = {
    "reports",
    "users",
    "id_sequences",
    "campus_match_versions",
    "schema_metadata",
    "report_owners",
    "campuses",
    "vectors",
    "report_embeddings",
    "ground_truth",
    "matches",
    "feedback",
    "audit_log",
    "claims",
    "notifications",
}


def _tables(path: Path) -> dict[str, str]:
    con = sqlite3.connect(path)
    try:
        return {
            name: " ".join(sql.split())
            for name, sql in con.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
            )
        }
    finally:
        con.close()


def _indexes(path: Path) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return {
            name
            for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")
        }
    finally:
        con.close()


def _run_alembic(db: Path, *args: str) -> None:
    # SQLAlchemy wants a POSIX-style absolute path for Windows drive letters:
    # `sqlite:///C:/dir/db.sqlite`. A relative path would break when pytest's
    # tmp dir and the project live on different drives.
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db.as_posix()}"}
    # Fixed argv with sys.executable and no shell: nothing here is untrusted.
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic {args} failed:\n{result.stdout}\n{result.stderr}"


def test_upgrade_head_builds_the_runtime_schema(tmp_path):
    db = tmp_path / "migrated.db"
    _run_alembic(db, "upgrade", "head")
    assert set(_tables(db)) == EXPECTED_TABLES


def test_migrated_schema_is_identical_to_store_connect(tmp_path):
    migrated = tmp_path / "migrated.db"
    _run_alembic(migrated, "upgrade", "head")

    runtime = tmp_path / "runtime.db"
    store.connect(runtime).close()

    assert _tables(migrated) == _tables(runtime)
    assert _indexes(migrated) == _indexes(runtime)


def test_downgrade_base_removes_every_table(tmp_path):
    db = tmp_path / "migrated.db"
    _run_alembic(db, "upgrade", "head")
    _run_alembic(db, "downgrade", "base")
    assert _tables(db) == {}


def test_phase_two_migration_adds_and_removes_new_schema(tmp_path):
    db = tmp_path / "phase_two.db"
    _run_alembic(db, "upgrade", "0004")

    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT INTO reports (id, kind, status, created_at, description, attrs_json) VALUES (?, ?, ?, ?, ?, ?)",
            ("LR-legacy", "lost", "pending_review", "2026-10-28T00:00:00", "legacy row", "{}"),
        )
        con.commit()
    finally:
        con.close()

    _run_alembic(db, "upgrade", "head")

    con = sqlite3.connect(db)
    try:
        report_columns = {row[1] for row in con.execute("PRAGMA table_info(reports)")}
        feedback_columns = {row[1] for row in con.execute("PRAGMA table_info(feedback)")}
        assert {"campus_id", "owner_user_id", "deleted_at", "pending_review"} <= report_columns
        assert {"campus_id", "model_version", "operating_point_version"} <= feedback_columns
        assert con.execute(
            "SELECT description, pending_review, created_at FROM reports WHERE id = 'LR-legacy'"
        ).fetchone() == ("legacy row", 1, "2026-10-27T18:30:00Z")
        assert (
            con.execute("SELECT created_at FROM reports WHERE id = 'LR-legacy'").fetchone()[0] == "2026-10-27T18:30:00Z"
        )
    finally:
        con.close()

    _run_alembic(db, "downgrade", "0004")
    assert set(_tables(db)) == EXPECTED_TABLES - {
        "campuses",
        "report_embeddings",
        "audit_log",
        "campus_match_versions",
        "schema_metadata",
        "claims",
        "notifications",
    }


def test_runtime_pending_review_flag_tracks_status(tmp_path):
    con = store.connect(tmp_path / "runtime.db")
    try:
        report = store.Report(
            id="LR-pending",
            kind="lost",
            created_at="2026-10-28T00:00:00",
            description="legacy review case",
            attrs={},
            status="pending_review",
        )
        store.insert_report(con, report)
        assert con.execute("SELECT pending_review FROM reports WHERE id = ?", (report.id,)).fetchone()[0] == 1

        store.set_status(con, report.id, "open")
        assert con.execute("SELECT pending_review FROM reports WHERE id = ?", (report.id,)).fetchone()[0] == 0
    finally:
        con.close()
