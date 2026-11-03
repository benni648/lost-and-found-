"""Prepare downloaded public datasets for the Findly training pipeline.

This command creates a compact JSONL manifest without extracting multi-gigabyte
archives. It intentionally does not invent lost/found labels: public datasets
are recorded as visual/category supervision, while matching labels come from
the synthetic corpus or staff-confirmed reports.

Example:
    .venv\\Scripts\\python.exe scripts\\prepare_real_datasets.py
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sqlite3
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / "datasets"
DEFAULT_OUTPUT = DATASETS / "findly_manifest.jsonl"

DATASET_INFO = {
    "amazon_berkeley_objects": {
        "license": "CC BY 4.0",
        "source": "https://amazon-berkeley-objects.s3.us-east-1.amazonaws.com/index.html",
        "supervision": "image, product metadata",
    },
    "stanford_online_products": {
        "license": "See official dataset terms",
        "source": "https://cvgl.stanford.edu/projects/lifted_struct/",
        "supervision": "product identity classes",
    },
    "open_images": {
        "license": "Per-image licensing and attribution must be checked",
        "source": "https://storage.googleapis.com/openimages/web/index.html",
        "supervision": "object bounding boxes",
    },
    "findly_synthetic": {
        "license": "Project-generated data",
        "source": "lostfound/corpus.py",
        "supervision": "lost/found pairs and report attributes",
    },
}


def _write(handle, row: dict) -> None:
    handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")


def _add_synthetic(handle, database: Path) -> int:
    if not database.exists():
        return 0
    con = sqlite3.connect(database)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, kind, description, category, image_path, campus_id FROM reports ORDER BY id"
        )
        count = 0
        for row in rows:
            _write(
                handle,
                {
                    "dataset": "findly_synthetic",
                    "sample_id": row["id"],
                    "kind": row["kind"],
                    "description": row["description"],
                    "category": row["category"],
                    "image_path": row["image_path"],
                    "campus_id": row["campus_id"],
                    "label_type": "lost_found_pair",
                    "source_path": str(database),
                },
            )
            count += 1
        return count
    finally:
        con.close()


def _add_abo(handle, archive: Path, limit: int | None) -> int:
    if not archive.exists():
        return 0
    count = 0
    with zipfile.ZipFile(archive) if archive.suffix.lower() == ".zip" else _tar(archive) as source:
        for member in source.namelist():
            if not member.startswith("images/small/") or not member.lower().endswith((".jpg", ".jpeg", ".png")):
                continue
            _write(
                handle,
                {
                    "dataset": "amazon_berkeley_objects",
                    "sample_id": Path(member).stem,
                    "archive_member": member,
                    "label_type": "image",
                    "source_path": str(archive),
                },
            )
            count += 1
            if limit is not None and count >= limit:
                break
    return count


class _TarAdapter:
    def __init__(self, archive: Path):
        self._path = archive
        self._archive = None

    def __enter__(self):
        import tarfile

        self._archive = tarfile.open(self._path)
        return self

    def __exit__(self, *_args):
        if self._archive is not None:
            self._archive.close()

    def namelist(self):
        if self._archive is None:
            raise RuntimeError("archive is not open")
        return (member.name for member in self._archive)


def _tar(archive: Path) -> _TarAdapter:
    return _TarAdapter(archive)


def _add_sop(handle, archive: Path, limit: int | None) -> int:
    if not archive.exists():
        return 0
    count = 0
    with zipfile.ZipFile(archive) as source:
        for member in source.namelist():
            path = Path(member)
            if len(path.parts) < 3 or path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
                continue
            _write(
                handle,
                {
                    "dataset": "stanford_online_products",
                    "sample_id": path.stem,
                    "class_id": path.parts[1],
                    "archive_member": member,
                    "label_type": "product_identity",
                    "source_path": str(archive),
                },
            )
            count += 1
            if limit is not None and count >= limit:
                break
    return count


def _add_open_images(handle, annotations: Path, limit: int | None) -> int:
    if not annotations.exists():
        return 0
    count = 0
    with annotations.open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            _write(
                handle,
                {
                    "dataset": "open_images",
                    "sample_id": row["ImageID"],
                    "label_name": row["LabelName"],
                    "confidence": float(row["Confidence"]),
                    "bbox": [float(row[key]) for key in ("XMin", "XMax", "YMin", "YMax")],
                    "label_type": "object_detection",
                    "source_path": str(annotations),
                },
            )
            count += 1
            if limit is not None and count >= limit:
                break
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a Findly real-data manifest")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "lostfound.db")
    parser.add_argument("--limit", type=int, default=10000, help="maximum rows per public dataset")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be positive")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    counts = {}
    with args.output.open("w", encoding="utf-8") as handle:
        _write(
            handle,
            {
                "record_type": "provenance",
                "created_at": dt.datetime.now(dt.UTC).isoformat(),
                "datasets": DATASET_INFO,
            },
        )
        counts["findly_synthetic"] = _add_synthetic(handle, args.database)
        counts["amazon_berkeley_objects"] = _add_abo(
            handle, DATASETS / "amazon-berkeley-objects" / "abo-images-small.tar", args.limit
        )
        counts["stanford_online_products"] = _add_sop(
            handle, DATASETS / "stanford-online-products" / "Stanford_Online_Products.zip", args.limit
        )
        counts["open_images"] = _add_open_images(
            handle, DATASETS / "open-images" / "train-annotations-bbox.csv", args.limit
        )

    print(f"manifest: {args.output}")
    for name, count in counts.items():
        print(f"{name}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
