"""SQLAlchemy engine construction for the Phase 2 database transition."""

from __future__ import annotations

import datetime as dt
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Connection, Engine, make_url
from sqlalchemy.exc import ArgumentError

from .settings import settings


def resolve_database_url(configured_url: str | URL | None = None) -> URL:
    """Resolve DATABASE_URL, defaulting to the existing SQLite demo database."""
    raw_url = settings.database_url if configured_url is None else configured_url
    if raw_url is None:
        return URL.create("sqlite", database=str(settings.database))

    if isinstance(raw_url, URL):
        url = raw_url
    elif raw_url.startswith("postgres://"):
        raw_url = "postgresql://" + raw_url.removeprefix("postgres://")
        try:
            url = make_url(raw_url)
        except ArgumentError as exc:
            raise ValueError("DATABASE_URL must be a valid SQLite or PostgreSQL URL.") from exc
    else:
        try:
            url = make_url(raw_url)
        except ArgumentError as exc:
            raise ValueError("DATABASE_URL must be a valid SQLite or PostgreSQL URL.") from exc

    if url.get_backend_name() == "postgresql":
        if url.drivername == "postgresql":
            url = url.set(drivername="postgresql+psycopg")
        elif url.drivername != "postgresql+psycopg":
            raise ValueError("PostgreSQL DATABASE_URL must use the synchronous psycopg driver.")
    elif url.get_backend_name() == "sqlite":
        if url.drivername != "sqlite":
            raise ValueError("SQLite DATABASE_URL must use the synchronous sqlite driver.")
    else:
        raise ValueError("DATABASE_URL must use SQLite or PostgreSQL.")

    return url


def create_database_engine(
    configured_url: str | URL | None = None,
    **engine_options: Any,
) -> Engine:
    """Create a pooled SQLAlchemy engine; use psycopg for PostgreSQL URLs."""
    url = resolve_database_url(configured_url)
    options = dict(engine_options)

    if url.get_backend_name() == "sqlite":
        if url.database not in (None, "", ":memory:"):
            Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
        connect_args = dict(options.pop("connect_args", {}))
        connect_args.setdefault("check_same_thread", False)
        options["connect_args"] = connect_args
    else:
        options.setdefault("pool_pre_ping", True)

    return create_engine(url, **options)


@lru_cache(maxsize=4)
def _cached_engine(url: URL) -> Engine:
    return create_database_engine(url)


def get_database_engine(configured_url: str | None = None) -> Engine:
    """Return a process-wide pooled engine for the resolved database URL."""
    return _cached_engine(resolve_database_url(configured_url))


class _MappingRow(dict):
    """Mapping row that preserves SQLite's commonly used integer indexing."""

    def __init__(self, row) -> None:
        values = tuple(
            value.astimezone(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
            if isinstance(value, dt.datetime) and value.tzinfo is not None
            else value
            for value in row
        )
        super().__init__(zip(row._mapping.keys(), values, strict=True))
        self._values = values

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


class _Result:
    def __init__(self, result) -> None:
        self._result = result

    def fetchone(self):
        row = self._result.fetchone()
        return _MappingRow(row) if row is not None else None

    def fetchall(self):
        return [_MappingRow(row) for row in self._result.fetchall()]

    def __iter__(self):
        return (_MappingRow(row) for row in self._result)


class SQLAlchemyConnection:
    """Small DB-API compatibility layer for the existing repository calls."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self.dialect = connection.dialect

    @property
    def in_transaction(self) -> bool:
        return self._connection.in_transaction()

    def execute(self, statement, parameters=()):
        if not isinstance(statement, str):
            result = self._connection.execute(statement, parameters)
        else:
            sql = statement if self.dialect.name == "sqlite" else re.sub(r"\?", "%s", statement)
            if isinstance(parameters, list):
                parameters = tuple(parameters)
            result = self._connection.exec_driver_sql(sql, parameters)
        return _Result(result)

    def executemany(self, statement: str, parameters) -> None:
        sql = statement if self.dialect.name == "sqlite" else re.sub(r"\?", "%s", statement)
        self._connection.exec_driver_sql(sql, parameters)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()
