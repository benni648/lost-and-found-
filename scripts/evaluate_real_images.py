"""Evaluate the active image encoder on real product-identity pairs.

This is an evaluation, not production training. Stanford Online Products
provides identity classes, so same-class image pairs are positives and pairs
from different classes are negatives. The script reads images directly from
the ZIP archive and does not extract it.
"""

from __future__ import annotations

import argparse
import json
import random
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "datasets" / "stanford-online-products" / "Stanford_Online_Products.zip"


def _cosine(left: np.ndarray, right: np.ndarray) -> float:
    denom = float(np.linalg.norm(left) * np.linalg.norm(right))
    return float(np.dot(left, right) / denom) if denom else 0.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the active image encoder on Stanford products")
    parser.add_argument("--archive", type=Path, default=ARCHIVE)
    parser.add_argument("--pairs", type=int, default=250)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", type=Path, default=ROOT / "ml" / "eval" / "real-image-baseline.json")
    args = parser.parse_args(argv)
    if args.pairs < 1:
        parser.error("--pairs must be positive")
    if not args.archive.exists():
        raise SystemExit(f"dataset archive not found: {args.archive}")

    from lostfound.embedders import image_embedder
    from lostfound.settings import settings

    rng = random.Random(args.seed)  # noqa: S311 - reproducible benchmark sampling
    embedder = image_embedder(settings.image_embedder)
    with zipfile.ZipFile(args.archive) as source:
        by_class: dict[str, list[str]] = {}
        for member in source.namelist():
            path = Path(member)
            if len(path.parts) >= 3 and path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
                by_class.setdefault(path.parts[1], []).append(member)
        classes = [members for members in by_class.values() if len(members) >= 2]
        if not classes:
            raise SystemExit("no product classes with at least two images were found")

        all_members = [member for members in classes for member in members]
        class_by_member = {member: class_id for class_id, members in by_class.items() for member in members}
        cache: dict[str, np.ndarray] = {}

        def encode(member: str) -> np.ndarray:
            vector = cache.get(member)
            if vector is not None:
                return vector
            with Image.open(source.open(member)) as image:
                vector = embedder.encode(image)
            cache[member] = vector
            return vector

        positive_scores: list[float] = []
        negative_scores: list[float] = []
        for _ in range(args.pairs):
            members = rng.choice(classes)
            left, right = rng.sample(members, 2)
            positive_scores.append(_cosine(encode(left), encode(right)))

            negative_left = left
            negative_right = rng.choice(all_members)
            while class_by_member[negative_right] == class_by_member[negative_left]:
                negative_right = rng.choice(all_members)
            negative_scores.append(_cosine(encode(negative_left), encode(negative_right)))

    positives = np.asarray(positive_scores)
    negatives = np.asarray(negative_scores)
    result = {
        "dataset": "stanford_online_products",
        "encoder": settings.image_embedder,
        "pairs": args.pairs,
        "seed": args.seed,
        "positive_mean_cosine": round(float(positives.mean()), 6),
        "negative_mean_cosine": round(float(negatives.mean()), 6),
        "separation": round(float(positives.mean() - negatives.mean()), 6),
        "positive_above_negative_rate": round(float(np.mean(positives > negatives)), 6),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
