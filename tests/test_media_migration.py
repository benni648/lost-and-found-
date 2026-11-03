from __future__ import annotations

import sqlite3

import pytest

from lostfound import store
from lostfound.store import Report
from scripts.migrate_local_photos import migrate_photos


class MemoryPhotoStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_photo(self, key: str, data: bytes) -> str:
        self.objects[key] = data
        return key


def _report(con: sqlite3.Connection, report_id: str, image_path: str) -> None:
    store.insert_report(
        con,
        Report(
            id=report_id,
            kind="lost",
            created_at="2026-10-28T00:00:00",
            description="test photo",
            attrs={},
            image_path=image_path,
            has_photo=True,
        ),
    )


def test_migration_dry_run_does_not_upload_or_update_paths(tmp_path):
    db = tmp_path / "reports.db"
    project_root = tmp_path / "project"
    photo = project_root / "data" / "media" / "old.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"jpeg")
    con = store.connect(db)
    _report(con, "FR-0001", "data/media/old.jpg")
    con.commit()
    object_store = MemoryPhotoStore()
    try:
        count, planned = migrate_photos(con, object_store, project_root=project_root)
        assert count == 0
        assert planned == ["FR-0001"]
        assert object_store.objects == {}
        assert con.execute("SELECT image_path FROM reports WHERE id='FR-0001'").fetchone()[0] == "data/media/old.jpg"
    finally:
        con.close()


def test_migration_uploads_before_updating_and_is_rerunnable(tmp_path):
    db = tmp_path / "reports.db"
    project_root = tmp_path / "project"
    photo = project_root / "data" / "media" / "old.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"jpeg")
    con = store.connect(db)
    _report(con, "FR-0001", "data/media/old.jpg")
    con.commit()
    object_store = MemoryPhotoStore()
    try:
        count, migrated = migrate_photos(con, object_store, project_root=project_root, apply=True)
        assert (count, migrated) == (1, ["FR-0001"])
        assert object_store.objects == {"reports/FR-0001.jpg": b"jpeg"}
        assert con.execute("SELECT image_path FROM reports WHERE id='FR-0001'").fetchone()[0] == "reports/FR-0001.jpg"
        assert photo.is_file()
        assert migrate_photos(con, object_store, project_root=project_root, apply=True) == (0, [])
    finally:
        con.close()


def test_missing_photo_fails_before_uploading_anything(tmp_path):
    project_root = tmp_path / "project"
    existing = project_root / "data" / "media" / "present.jpg"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"jpeg")
    con = store.connect(tmp_path / "reports.db")
    _report(con, "FR-0001", "data/media/present.jpg")
    _report(con, "FR-0002", "data/media/missing.jpg")
    con.commit()
    object_store = MemoryPhotoStore()
    try:
        with pytest.raises(FileNotFoundError, match="FR-0002"):
            migrate_photos(con, object_store, project_root=project_root, apply=True)
        assert object_store.objects == {}
        image_path = con.execute("SELECT image_path FROM reports WHERE id='FR-0001'").fetchone()[0]
        assert image_path == "data/media/present.jpg"
    finally:
        con.close()


def test_migration_rejects_source_paths_outside_project_root(tmp_path):
    project_root = tmp_path / "project"
    con = store.connect(tmp_path / "reports.db")
    _report(con, "FR-0001", "../outside.jpg")
    con.commit()
    try:
        with pytest.raises(ValueError, match="escapes project root"):
            migrate_photos(con, MemoryPhotoStore(), project_root=project_root, apply=True)
    finally:
        con.close()
