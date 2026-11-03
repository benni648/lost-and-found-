from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("sqlalchemy")

from lostfound import store
from lostfound.db import SQLAlchemyConnection


class FakeRow(dict):
    def __init__(self, values):
        super().__init__(values)
        self._values = tuple(values.values())

    def __getitem__(self, key):
        return self._values[key] if isinstance(key, int) else super().__getitem__(key)

    @property
    def _mapping(self):
        return self

    def __iter__(self):
        return iter(self._values)


class FakeResult:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows

    def __iter__(self):
        return iter(self.fetchall())


class FakeSQLAlchemyConnection:
    class Dialect:
        name = "postgresql"

    dialect = Dialect()

    def __init__(self):
        self.calls = []
        self.commits = 0

    def exec_driver_sql(self, statement, parameters=()):
        self.calls.append((statement, parameters))
        if statement.startswith("SELECT id FROM reports"):
            return FakeResult()
        if "SELECT dim, embedding::text AS vec" in statement:
            return FakeResult([FakeRow({"dim": 3, "vec": "[0.25,-0.5,1]"})])
        if "RETURNING next_value" in statement:
            return FakeResult([FakeRow({"next_value": 2})])
        return FakeResult()

    def execute(self, statement, parameters=None):
        self.calls.append((str(statement), parameters))
        return FakeResult()

    def in_transaction(self):
        return False

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


def test_postgres_connection_translates_dbapi_placeholders():
    raw = FakeSQLAlchemyConnection()
    connection = SQLAlchemyConnection(raw)

    connection.execute("SELECT * FROM reports WHERE id=? AND kind=?", ("LR-0001", "lost"))

    assert raw.calls == [("SELECT * FROM reports WHERE id=%s AND kind=%s", ("LR-0001", "lost"))]


def test_postgres_id_allocator_uses_atomic_upsert():
    connection = SQLAlchemyConnection(FakeSQLAlchemyConnection())

    assert store.next_id(connection, "FR") == "FR-0001"
    assert connection._connection.commits == 1
    allocator_sql = connection._connection.calls[-1][0]
    assert "ON CONFLICT(prefix) DO UPDATE" in allocator_sql
    assert "RETURNING next_value" in allocator_sql


def test_postgres_vectors_write_and_read_pgvector_text():
    raw = FakeSQLAlchemyConnection()
    connection = SQLAlchemyConnection(raw)

    store.put_vector(connection, "LR-1", "text", "hash-char-ngram-v1", np.array([0.25, -0.5], dtype=np.float32))
    insert_sql, insert_params = raw.calls[-1]
    assert "INSERT INTO report_embeddings" in insert_sql
    assert "CAST(%s AS vector)" in insert_sql
    assert insert_params[-1] == "[0.25,-0.5]"

    vector = store.get_vector(connection, "LR-1", "text", "hash-char-ngram-v1")
    assert np.array_equal(vector, np.array([0.25, -0.5, 1], dtype=np.float32))
