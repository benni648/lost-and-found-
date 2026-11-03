"""Copy legacy report photos into the configured object store.

The script updates image_path only after each object upload succeeds and never
deletes source files. Re-running it is safe: migrated object keys are skipped.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lostfound import store  # noqa: E402
from lostfound.objects import get_object_store, object_key_for_report  # noqa: E402
from lostfound.settings import PROJECT_ROOT, settings  # noqa: E402


class PhotoStore(Protocol):
    def put_photo(self, key: str, data: bytes) -> str: ...


def _source_path(project_root: Path, image_path: str) -> Path:
    source = (project_root / image_path).resolve()
    root = project_root.resolve()
    if not source.is_relative_to(root):
        raise ValueError(f"photo path escapes project root: {image_path!r}")
    return source


def migrate_photos(
    con: sqlite3.Connection,
    photo_store: PhotoStore | None,
    *,
    project_root: Path = PROJECT_ROOT,
    apply: bool = False,
) -> tuple[int, list[str]]:
    """Return the number migrated and planned report IDs; do not remove originals."""
    rows = con.execute(
        "SELECT id, image_path FROM reports WHERE image_path IS NOT NULL AND image_path != '' ORDER BY id"
    ).fetchall()
    pending: list[tuple[str, Path, str]] = []
    for row in rows:
        report_id, image_path = str(row["id"]), str(row["image_path"]).replace("\\", "/")
        # Object keys from a previous run, or newly-created reports, need no work.
        if image_path.startswith("reports/"):
            continue
        source = _source_path(project_root, image_path)
        if not source.is_file():
            raise FileNotFoundError(f"photo for report {report_id} does not exist: {source}")
        pending.append((report_id, source, object_key_for_report(report_id)))

    if not apply:
        return 0, [report_id for report_id, _, _ in pending]
    if photo_store is None:
        raise ValueError("an object store is required when applying the migration")

    migrated: list[str] = []
    for report_id, source, key in pending:
        stored_key = photo_store.put_photo(key, source.read_bytes())
        if stored_key != key:
            raise RuntimeError(f"object store returned unexpected key for report {report_id}")
        con.execute("UPDATE reports SET image_path=? WHERE id=?", (key, report_id))
        migrated.append(report_id)
    con.commit()
    return len(migrated), migrated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=settings.database)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
        help="Root used to resolve existing relative photo paths.",
    )
    parser.add_argument("--apply", action="store_true", help="Upload photos and update report paths.")
    args = parser.parse_args(argv)

    try:
        con = store.connect(args.database, initialize=False)
        try:
            count, report_ids = migrate_photos(
                con,
                get_object_store() if args.apply else None,
                project_root=args.project_root,
                apply=args.apply,
            )
        finally:
            con.close()
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        print(f"photo migration failed: {exc}", file=sys.stderr)
        return 2

    if args.apply:
        print(f"Migrated {count} report photo(s); source files were retained.")
    else:
        print(f"Dry run: {len(report_ids)} report photo(s) are ready to migrate.")
        if report_ids:
            print("Run again with --apply to upload and update the database.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
