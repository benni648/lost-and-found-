"""Versioned encoder registry (Phase 3, task A).

A stored embedding is only meaningful together with the exact model that
produced it: swap the encoder and every cosine similarity in the database
silently changes meaning. This module is the single place that answers
"which encoder, which revision, which preprocessing" for every vector we
write, and it can tell you which stored vectors are stale.

Nothing here loads a model. The metadata is declarative on purpose so the
registry can be served by ``GET /api/model/info`` and used by CI without
downloading weights. Instantiation still lives in :mod:`lostfound.embedders`.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Literal

from .settings import settings

EncoderKind = Literal["text", "image"]


@dataclass(frozen=True)
class EncoderInfo:
    """Everything needed to reproduce one embedding dimension."""

    kind: EncoderKind
    name: str
    dim: int
    revision: str
    preprocess: str
    requires: tuple[str, ...] = ()
    description: str = ""

    @property
    def preprocess_hash(self) -> str:
        """Short, stable hash of the preprocessing contract."""
        return hashlib.sha256(f"{self.name}|{self.dim}|{self.preprocess}".encode()).hexdigest()[:12]

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["preprocess_hash"] = self.preprocess_hash
        payload["requires"] = list(self.requires)
        return payload


_REGISTRY: dict[tuple[str, str], EncoderInfo] = {
    ("text", "hash-char-ngram-v1"): EncoderInfo(
        kind="text",
        name="hash-char-ngram-v1",
        dim=512,
        revision="1",
        preprocess="tokenize;word+n3/4/5 char n-grams;crc32 signed hash;log1p;l2",
        description="Offline hashed n-grams: deterministic, no downloads, CPU-only CI.",
    ),
    ("image", "hist-grid-v1"): EncoderInfo(
        kind="image",
        name="hist-grid-v1",
        dim=12 * 4 * 4 + 16 * 3 + 16,
        revision="1",
        preprocess="rgb;resize96bilinear;hsv12x4x4;rgb4x4grid;edges4x4;l2",
        description="Offline HSV histogram + spatial colour/edge grid.",
    ),
}


def encoder_info(kind: EncoderKind, name: str) -> EncoderInfo:
    try:
        return _REGISTRY[(kind, name)]
    except KeyError as exc:
        available = sorted(key for key_kind, key in _REGISTRY if key_kind == kind)
        raise KeyError(f"unknown {kind} encoder {name!r}; have {available}") from exc


def registry() -> list[EncoderInfo]:
    return sorted(_REGISTRY.values(), key=lambda info: (info.kind, info.name))


def active_encoders() -> dict[str, dict]:
    """Metadata for the encoders this process is configured to use."""
    return {
        "text": encoder_info("text", settings.text_embedder).as_dict(),
        "image": encoder_info("image", settings.image_embedder).as_dict(),
    }


def stale_vector_models(con, *, kind: str | None = None) -> dict[str, list[str]]:
    """Stored vector models that do not match the active encoder.

    A stale entry is a report whose stored embedding cannot be compared with
    newly embedded content without a re-embed. Returns ``{model: [model...]}``
    grouped by kind so an operator sees exactly what a backfill would touch.
    """
    postgres = getattr(getattr(con, "dialect", None), "name", None) == "postgresql"
    table, column = ("report_embeddings", "embedding::text") if postgres else ("vectors", "vec")
    query = f"SELECT DISTINCT kind, model FROM {table}"  # noqa: S608 - table name is a literal
    active = {"text": settings.text_embedder, "image": settings.image_embedder}
    out: dict[str, list[str]] = {}
    for row in con.execute(query):
        row_kind, model = row["kind"], row["model"]
        if kind is not None and row_kind != kind:
            continue
        if active.get(row_kind) == model:
            continue
        out.setdefault(row_kind, []).append(model)
    return {key: sorted(value) for key, value in out.items()}


__all__ = [
    "EncoderInfo",
    "EncoderKind",
    "active_encoders",
    "encoder_info",
    "registry",
    "stale_vector_models",
]
