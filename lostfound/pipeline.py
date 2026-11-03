"""Ingest pipeline: corpus -> enriched reports -> embeddings -> database.

The enrichment step is where the two modalities meet. Text always gives us the
attribute record; a photo contributes the one thing text often omits -- the
colour -- and only when the reporter did not state it, plus any printed serial
that OCR can read (see :mod:`lostfound.ocr`). That is the same masked-evidence
rule the scorer uses downstream.
"""

from __future__ import annotations

import warnings
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, UnidentifiedImageError

from . import ocr, store
from .campus import Campus, get_campus
from .colorspace import name_color
from .config import DEFAULT_ITEM_COUNT, DEFAULT_SEED, IMAGE_EMBEDDER, TEXT_EMBEDDER
from .corpus import Corpus, generate_corpus
from .embedders import image_embedder, text_embedder
from .objects import LocalObjectStore, get_object_store, object_key_for_report
from .schema import Attributes
from .textnorm import extract

MAX_IMAGE_PIXELS = 40_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


def open_image(path: str) -> Image.Image | None:
    """Load a report photo through the configured private object store."""
    try:
        return Image.open(BytesIO(get_object_store().read(path))).convert("RGB")
    except (OSError, ValueError):
        return None


def dominant_colour(path: str) -> tuple[str, float] | None:
    img = open_image(path)
    if img is None:
        return None
    w, h = img.size
    crop = img.crop((int(w * 0.30), int(h * 0.30), int(w * 0.70), int(h * 0.70)))
    pixel = crop.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
    if not isinstance(pixel, tuple) or len(pixel) < 3:
        return None
    return name_color((int(pixel[0]), int(pixel[1]), int(pixel[2])))


def save_report_photo(data: bytes, report_id: str, media_dir: Path | None = None) -> str:
    """Normalise an uploaded photo and store it as a JPEG.

    Re-encoding is deliberate: it strips EXIF (location, device, sometimes the
    person's name), bounds the file size, and guarantees the matcher sees a
    plain RGB image. Raises ``ValueError`` if the bytes are not an image.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            opened = Image.open(BytesIO(data))
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("that file is not a readable image") from exc

    if opened.width * opened.height > MAX_IMAGE_PIXELS:
        raise ValueError("image dimensions exceed the 40 megapixel limit")
    try:
        opened.load()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError("that file is not a readable image") from exc

    img = opened.convert("RGB")
    img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    output = BytesIO()
    img.save(output, "JPEG", quality=90, optimize=True)
    object_store = LocalObjectStore(media_dir) if media_dir is not None else get_object_store()
    return object_store.put_photo(object_key_for_report(report_id), output.getvalue())


MAX_PHOTOS = 4


def save_report_photos(files: list[bytes], report_id: str, media_dir: Path | None = None) -> list[str]:
    """Store up to ``MAX_PHOTOS`` photos for one report, in order.

    The first key is the report's primary photo (``reports.image_path``); the
    rest are additional views of the same item.
    """
    if len(files) > MAX_PHOTOS:
        raise ValueError(f"up to {MAX_PHOTOS} photos are supported")
    return [
        save_report_photo(data, report_id if index == 0 else f"{report_id}-p{index + 1}", media_dir)
        for index, data in enumerate(files)
    ]


def aggregate_image_vector(vectors) -> np.ndarray | None:
    """Max-pool several photo embeddings into one report vector, then L2-normalise.

    Max-pooling keeps a distinguishing detail seen in any single photo instead of
    averaging it away, which is the same reason colour evidence is not averaged.
    """
    usable = [np.asarray(vector, dtype=np.float32) for vector in vectors if vector is not None]
    if not usable:
        return None
    pooled = np.vstack(usable).max(axis=0)
    norm = float(np.linalg.norm(pooled))
    return pooled / norm if norm > 0 else pooled


def enrich_report(report) -> Attributes:
    attrs = extract(report.description).attrs
    if report.image_path:
        colour = dominant_colour(report.image_path)
        if colour and not attrs.primary_color:
            name, dist = colour
            attrs.set("primary_color", name, 0.55 if dist < 45 else 0.4)
            attrs.confidence["primary_color_source"] = 1.0  # from the photo, not the text
        printed = ocr.extract_visible_text(report.image_path)
        if printed:
            # Union of what the reporter typed and what the photo actually says;
            # a token seen in both is not counted twice.
            stated = {value.upper() for value in attrs.visible_text}
            merged = list(attrs.visible_text) + [t for t in printed if t.upper() not in stated]
            attrs.set("visible_text", merged[: ocr.MAX_VISIBLE_TEXT], 0.6)
            attrs.confidence["visible_text_source"] = 1.0  # read from the photo
    return attrs


def build_database(
    db_path: Path | str | None = None,
    n_items: int = DEFAULT_ITEM_COUNT,
    seed: int = DEFAULT_SEED,
    render_images: bool = True,
    campus: Campus | None = None,
    verbose: bool = True,
) -> dict:
    campus = campus or get_campus()
    corpus: Corpus = generate_corpus(n_items=n_items, seed=seed, campus=campus, render_images=render_images)

    con = store.connect(db_path)
    store.reset(con)

    ten = text_embedder(TEXT_EMBEDDER)
    iem = image_embedder(IMAGE_EMBEDDER)

    for report in corpus.reports:
        attrs = enrich_report(report)
        report.attrs = attrs.to_dict()
        report.category = attrs.category or report.category
        store.insert_report(con, report)

        store.put_vector(con, report.id, "text", ten.name, ten.encode(report.description))
        if report.image_path:
            img = open_image(report.image_path)
            if img is not None:
                store.put_vector(con, report.id, "image", iem.name, iem.encode(img))

    for lost_id, found_id in corpus.truth.items():
        store.add_ground_truth(con, corpus.report_to_item[lost_id], lost_id, found_id)
    for item in corpus.items:
        if item.has_found_report and not item.has_lost_report:
            store.add_ground_truth(con, item.item_id, None, None)

    con.commit()

    stats = {
        "reports": store.count(con, "reports"),
        "lost": sum(1 for r in corpus.reports if r.kind == "lost"),
        "found": sum(1 for r in corpus.reports if r.kind == "found"),
        "pairs": len(corpus.truth),
        "vectors_text": con.execute("SELECT COUNT(*) FROM vectors WHERE kind='text'").fetchone()[0],
        "vectors_image": con.execute("SELECT COUNT(*) FROM vectors WHERE kind='image'").fetchone()[0],
        "photos": sum(1 for r in corpus.reports if r.has_photo),
        "db": str(db_path or "data/lostfound.db"),
        "text_embedder": ten.name,
        "image_embedder": iem.name,
    }
    if verbose:
        for k, v in stats.items():
            print(f"  {k:<14} {v}")
    con.close()
    return stats
