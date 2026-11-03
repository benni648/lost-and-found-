"""Re-embed stored report photos with a selected image encoder.

The command is additive: it writes vectors under the new model name and does
not delete reports or vectors produced by an older encoder.
"""

from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path

from PIL import Image

from lostfound import store
from lostfound.embedders import image_embedder
from lostfound.objects import get_object_store
from lostfound.settings import settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-embed stored report photos")
    parser.add_argument("--db", type=Path, default=settings.database)
    parser.add_argument("--model", default="hist-grid-v1")
    args = parser.parse_args(argv)

    embedder = image_embedder(args.model)
    object_store = get_object_store()
    con = store.connect(args.db, initialize=False)
    embedded = skipped = 0
    try:
        for report in store.list_reports(con, include_pending=True):
            if not report.image_path:
                skipped += 1
                continue
            try:
                with Image.open(BytesIO(object_store.read(report.image_path))) as image:
                    vector = embedder.encode(image)
            except (OSError, ValueError):
                skipped += 1
                continue
            store.put_vector(con, report.id, "image", embedder.name, vector)
            embedded += 1
        con.commit()
    finally:
        con.close()

    print(f"database: {args.db}")
    print(f"model: {embedder.name}")
    print(f"embedded: {embedded}")
    print(f"skipped: {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
