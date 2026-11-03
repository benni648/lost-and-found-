"""PostgreSQL/pgvector candidate retrieval for the exhaustive feature scorer."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence

import numpy as np

from .settings import settings

MODEL_DIMENSIONS = {
    "hash-char-ngram-v1": 512,
    "hist-grid-v1": 256,
}


def _vector_literal(vector: np.ndarray, expected_dimension: int) -> str:
    values = np.asarray(vector, dtype=np.float32)
    if values.ndim != 1 or values.size != expected_dimension:
        raise ValueError(f"query vector must have exactly {expected_dimension} dimensions")
    if not np.isfinite(values).all():
        raise ValueError("query vector contains a non-finite value")
    return "[" + ",".join(format(float(value), ".9g") for value in values) + "]"


_ANN_SQL_BY_DIMENSION = {
    256: """
        SELECT e.report_id
        FROM report_embeddings AS e
        JOIN reports AS r ON r.id = e.report_id
        WHERE e.kind = :embedding_kind
          AND e.model = :model
          AND e.dim = :dimension
          AND r.kind = :candidate_kind
          AND r.status != 'pending_review'
          AND r.deleted_at IS NULL
          AND (CAST(:campus_id AS TEXT) IS NULL OR COALESCE(r.campus_id, :default_campus_id) = :campus_id)
        ORDER BY e.embedding::vector(256) <=> CAST(:query_vector AS vector(256))
        LIMIT :candidate_limit
    """,
    384: """
        SELECT e.report_id
        FROM report_embeddings AS e
        JOIN reports AS r ON r.id = e.report_id
        WHERE e.kind = :embedding_kind
          AND e.model = :model
          AND e.dim = :dimension
          AND r.kind = :candidate_kind
          AND r.status != 'pending_review'
          AND r.deleted_at IS NULL
          AND (CAST(:campus_id AS TEXT) IS NULL OR COALESCE(r.campus_id, :default_campus_id) = :campus_id)
        ORDER BY e.embedding::vector(384) <=> CAST(:query_vector AS vector(384))
        LIMIT :candidate_limit
    """,
    512: """
        SELECT e.report_id
        FROM report_embeddings AS e
        JOIN reports AS r ON r.id = e.report_id
        WHERE e.kind = :embedding_kind
          AND e.model = :model
          AND e.dim = :dimension
          AND r.kind = :candidate_kind
          AND r.status != 'pending_review'
          AND r.deleted_at IS NULL
          AND (CAST(:campus_id AS TEXT) IS NULL OR COALESCE(r.campus_id, :default_campus_id) = :campus_id)
        ORDER BY e.embedding::vector(512) <=> CAST(:query_vector AS vector(512))
        LIMIT :candidate_limit
    """,
    1024: """
        SELECT e.report_id
        FROM report_embeddings AS e
        JOIN reports AS r ON r.id = e.report_id
        WHERE e.kind = :embedding_kind
          AND e.model = :model
          AND e.dim = :dimension
          AND r.kind = :candidate_kind
          AND r.status != 'pending_review'
          AND r.deleted_at IS NULL
          AND (CAST(:campus_id AS TEXT) IS NULL OR COALESCE(r.campus_id, :default_campus_id) = :campus_id)
        ORDER BY e.embedding::vector(1024) <=> CAST(:query_vector AS vector(1024))
        LIMIT :candidate_limit
    """,
}


def ann_candidate_ids(
    connection,
    *,
    query_vectors: Mapping[str, np.ndarray],
    candidate_kind: str,
    candidate_limit: int = 200,
    campus_id: str | None = None,
) -> set[str]:
    """Union model-specific pgvector shortlists; the caller performs final scoring.

    The partial HNSW indexes require exact model/kind/dimension predicates and
    a fixed vector cast in the ordering expression. Unknown model dimensions
    are rejected rather than silently bypassing indexed retrieval.
    """
    if candidate_kind not in {"lost", "found"}:
        raise ValueError("candidate_kind must be 'lost' or 'found'")
    if candidate_limit < 1:
        raise ValueError("candidate_limit must be positive")
    if not query_vectors:
        return set()
    if connection.dialect.name != "postgresql":
        raise ValueError("ANN retrieval requires a PostgreSQL connection.")
    from sqlalchemy import text

    found: set[str] = set()
    for model, vector in query_vectors.items():
        try:
            dimension = MODEL_DIMENSIONS[model]
        except KeyError as exc:
            raise ValueError(f"no pgvector index dimension is registered for model {model!r}") from exc
        embedding_kind = "image" if model == "hist-grid-v1" else "text"
        parameters = {
            "embedding_kind": embedding_kind,
            "model": model,
            "dimension": dimension,
            "candidate_kind": candidate_kind,
            "query_vector": _vector_literal(vector, dimension),
            "candidate_limit": candidate_limit,
            "default_campus_id": settings.default_campus_id,
        }
        parameters["campus_id"] = campus_id
        rows = connection.execute(
            text(_ANN_SQL_BY_DIMENSION[dimension]),
            parameters,
        )
        found.update(str(row[0]) for row in rows)
    return found


def recall_at_k(approximate: Collection[str], exhaustive: Sequence[str], k: int = 10) -> float:
    """Recall of the ANN candidate shortlist relative to an exhaustive ranking."""
    if k < 1:
        raise ValueError("k must be positive")
    expected = set(exhaustive[:k])
    if not expected:
        return 1.0
    return len(expected.intersection(approximate)) / len(expected)
