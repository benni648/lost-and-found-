from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("sqlalchemy")

from lostfound.retrieval import ann_candidate_ids, recall_at_k


class FakePostgresConnection:
    class Dialect:
        name = "postgresql"

    dialect = Dialect()

    def __init__(self) -> None:
        self.calls = []

    def execute(self, query, parameters):
        self.calls.append((str(query), parameters))
        return [("FR-0001",), ("FR-0002",)]


def test_ann_retrieval_uses_index_dimensions_and_unions_modalities():
    connection = FakePostgresConnection()
    ids = ann_candidate_ids(
        connection,
        query_vectors={
            "hash-char-ngram-v1": np.zeros(512, dtype=np.float32),
            "hist-grid-v1": np.zeros(256, dtype=np.float32),
        },
        candidate_kind="found",
        candidate_limit=200,
        campus_id="campus-a",
    )

    assert ids == {"FR-0001", "FR-0002"}
    assert len(connection.calls) == 2
    text_query, text_params = connection.calls[0]
    image_query, image_params = connection.calls[1]
    assert "vector(512)" in text_query
    assert "idx_report_embeddings" not in text_query
    assert "vector(256)" in image_query
    assert "ORDER BY e.embedding::vector(512) <=> CAST(:query_vector AS vector(512))" in text_query
    assert "ORDER BY e.embedding::vector(256) <=> CAST(:query_vector AS vector(256))" in image_query
    assert "CAST(:campus_id AS TEXT) IS NULL" in text_query
    assert "COALESCE(r.campus_id, :default_campus_id) = :campus_id" in text_query
    assert text_params["candidate_limit"] == image_params["candidate_limit"] == 200
    assert text_params["campus_id"] == image_params["campus_id"] == "campus-a"


def test_ann_retrieval_rejects_non_postgres_connection():
    class SQLiteConnection:
        class Dialect:
            name = "sqlite"

        dialect = Dialect()

    with pytest.raises(ValueError, match="requires a PostgreSQL"):
        ann_candidate_ids(
            SQLiteConnection(),
            query_vectors={"hash-char-ngram-v1": np.zeros(512, dtype=np.float32)},
            candidate_kind="found",
        )


def test_ann_retrieval_rejects_invalid_vector_and_limits():
    connection = FakePostgresConnection()
    with pytest.raises(ValueError, match="non-finite"):
        ann_candidate_ids(
            connection,
            query_vectors={"hash-char-ngram-v1": np.full(512, np.nan, dtype=np.float32)},
            candidate_kind="found",
        )
    with pytest.raises(ValueError, match="positive"):
        ann_candidate_ids(
            connection,
            query_vectors={"hash-char-ngram-v1": np.zeros(512, dtype=np.float32)},
            candidate_kind="found",
            candidate_limit=0,
        )


def test_recall_at_k_measures_candidate_recall():
    assert recall_at_k(["FR-1", "FR-2", "FR-3"], ["FR-3", "FR-1", "FR-9"], k=2) == 1.0
    assert recall_at_k(["FR-1"], ["FR-1", "FR-2"], k=2) == 0.5
    assert recall_at_k([], [], k=5) == 1.0
    with pytest.raises(ValueError, match="positive"):
        recall_at_k([], [], k=0)
