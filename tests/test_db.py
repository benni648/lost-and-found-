from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest


@pytest.fixture
def database_module():
    pytest.importorskip("sqlalchemy")
    from lostfound import db

    return db


def test_sqlite_fallback_uses_configured_demo_path(monkeypatch, tmp_path, database_module):
    database = tmp_path / "nested" / "lostfound.db"
    monkeypatch.setattr(database_module, "settings", SimpleNamespace(database_url=None, database=database))

    engine = database_module.create_database_engine()
    try:
        assert engine.url.get_backend_name() == "sqlite"
        assert database.parent.is_dir()
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT 1").scalar_one() == 1
    finally:
        engine.dispose()


def test_sqlalchemy_compat_connection_preserves_repository_row_access(database_module):
    engine = database_module.create_database_engine("sqlite:///:memory:")
    connection = database_module.SQLAlchemyConnection(engine.connect())
    try:
        connection.execute("CREATE TABLE sample (id TEXT, amount INTEGER)")
        connection.execute("INSERT INTO sample (id, amount) VALUES (?, ?)", ("row-1", 7))
        row = connection.execute("SELECT id, amount FROM sample").fetchone()
        assert row["id"] == "row-1"
        assert row[1] == 7
        assert dict(row) == {"id": "row-1", "amount": 7}
        connection.commit()
    finally:
        connection.close()
        engine.dispose()


def test_sqlalchemy_compat_connection_accepts_list_of_sql_parameters(database_module):
    engine = database_module.create_database_engine("sqlite:///:memory:")
    connection = database_module.SQLAlchemyConnection(engine.connect())
    try:
        connection.execute("CREATE TABLE sample (id TEXT, amount INTEGER)")
        connection.execute("INSERT INTO sample (id, amount) VALUES (?, ?)", ["row-1", 7])
        row = connection.execute("SELECT id, amount FROM sample WHERE id=?", ["row-1"]).fetchone()

        assert row["id"] == "row-1"
        assert row["amount"] == 7
    finally:
        connection.close()
        engine.dispose()


def test_mapping_row_normalizes_aware_datetimes_to_utc(database_module):
    class Row:
        _mapping = {"created_at": dt.datetime(2026, 10, 29, 8, 0, tzinfo=dt.UTC)}

        def __iter__(self):
            return iter(self._mapping.values())

    row = database_module._MappingRow(Row())

    assert row["created_at"] == "2026-10-29T08:00:00Z"


def test_postgres_urls_select_sync_psycopg_driver(database_module):
    url = database_module.resolve_database_url("postgres://user:secret@localhost/lostfound")

    assert url.drivername == "postgresql+psycopg"
    assert url.username == "user"
    assert url.database == "lostfound"


def test_postgres_engine_can_be_created_with_optional_driver(database_module):
    engine = database_module.create_database_engine("postgresql://user:secret@localhost/lostfound")
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.url.drivername == "postgresql+psycopg"
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "url",
    ["mysql://user:secret@localhost/lostfound", "postgresql+asyncpg://user:secret@localhost/lostfound"],
)
def test_unsupported_database_drivers_fail_explicitly(url, database_module):
    with pytest.raises(ValueError, match="DATABASE_URL"):
        database_module.resolve_database_url(url)


def test_invalid_database_url_fails_explicitly(database_module):
    with pytest.raises(ValueError, match="DATABASE_URL"):
        database_module.resolve_database_url("not a database url")
