"""SQLite/PostgreSQL storage: reports, vectors, matches and ground truth.

SQLite keeps the demo dependency-free; PostgreSQL uses pooled connections and
pgvector through the compatibility SQL adapter. The local ``vectors`` table
stores one BLOB per (report, kind, model), while PostgreSQL uses
``report_embeddings``.

Ground truth lives in its own table and is never read by the matcher.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .config import DB_PATH
from .settings import settings
from .timeutil import iso_utc, now_utc

DDL = """
CREATE TABLE IF NOT EXISTS reports (
    id               TEXT PRIMARY KEY,
    kind             TEXT NOT NULL CHECK (kind IN ('lost','found')),
    status           TEXT NOT NULL DEFAULT 'open',
    created_at       TEXT NOT NULL,
    lost_from        TEXT,
    lost_to          TEXT,
    node_key         TEXT,
    location_note    TEXT,
    latitude         REAL,
    longitude        REAL,
    location_accuracy_m REAL,
    description      TEXT NOT NULL,
    lang             TEXT NOT NULL DEFAULT 'en',
    category         TEXT,
    attrs_json       TEXT NOT NULL,
    image_path       TEXT,
    has_photo        INTEGER NOT NULL DEFAULT 0,
    is_synthetic_art INTEGER NOT NULL DEFAULT 0,
    reporter_role    TEXT NOT NULL DEFAULT 'student'
);
CREATE INDEX IF NOT EXISTS idx_reports_kind ON reports(kind);
CREATE INDEX IF NOT EXISTS idx_reports_cat  ON reports(category);

CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    email       TEXT UNIQUE,
    phone_e164  TEXT,
    role        TEXT NOT NULL DEFAULT 'student'
                CHECK (role IN ('student','staff','campus_admin','superadmin')),
    campus_id   TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS id_sequences (
    prefix      TEXT PRIMARY KEY,
    next_value  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS campus_match_versions (
    campus_id TEXT PRIMARY KEY,
    version   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS schema_metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS report_owners (
    report_id    TEXT PRIMARY KEY,
    user_id      TEXT,
    contact_email TEXT,
    contact_phone TEXT,
    token_hash   TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campuses (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    short     TEXT NOT NULL UNIQUE,
    domain    TEXT UNIQUE,
    latitude  REAL,
    longitude REAL,
    timezone  TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    status    TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS vectors (
    report_id TEXT NOT NULL,
    kind      TEXT NOT NULL,
    model     TEXT NOT NULL,
    dim       INTEGER NOT NULL,
    vec       BLOB NOT NULL,
    PRIMARY KEY (report_id, kind, model)
);

CREATE TABLE IF NOT EXISTS report_embeddings (
    report_id  TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    model      TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    embedding  BLOB NOT NULL,
    PRIMARY KEY (report_id, kind, model)
);
CREATE INDEX IF NOT EXISTS idx_report_embeddings_model ON report_embeddings(kind, model, dim);

CREATE TABLE IF NOT EXISTS ground_truth (
    item_id   TEXT PRIMARY KEY,
    lost_id   TEXT,
    found_id  TEXT,
    label     TEXT NOT NULL DEFAULT 'match'
);
CREATE INDEX IF NOT EXISTS idx_gt_lost ON ground_truth(lost_id);

CREATE TABLE IF NOT EXISTS feedback (
    query_id     TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    action       TEXT NOT NULL CHECK (action IN ('confirm','reject')),
    staff        TEXT,
    note         TEXT,
    prob         REAL,
    score        REAL,
    features_json TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (query_id, candidate_id)
);
CREATE INDEX IF NOT EXISTS idx_feedback_query ON feedback(query_id);

CREATE TABLE IF NOT EXISTS matches (
    query_id     TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    score        REAL NOT NULL,
    prob         REAL NOT NULL,
    decision     TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (query_id, candidate_id)
);
CREATE INDEX IF NOT EXISTS idx_matches_candidate ON matches(candidate_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id          TEXT PRIMARY KEY,
    actor_id    TEXT,
    action      TEXT NOT NULL,
    entity      TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    before_json TEXT,
    after_json  TEXT,
    created_at  TEXT NOT NULL,
    ip          TEXT,
    request_id  TEXT
);

-- Recovery loop: a claimant answers challenge questions about the *lost*
-- report, a verified claim gets a one-time handover code, and staff closing
-- the counter marks the pair collected. Answers are never stored, only the
-- questions that were asked and how many attempts were made.
CREATE TABLE IF NOT EXISTS claims (
    id               TEXT PRIMARY KEY,
    query_id         TEXT NOT NULL,
    candidate_id     TEXT NOT NULL,
    campus_id        TEXT,
    claimant_user_id TEXT,
    claimant_email   TEXT,
    token_hash       TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','verified','collected','rejected','expired')),
    challenge_json   TEXT NOT NULL,
    score            REAL NOT NULL DEFAULT 0,
    attempts         INTEGER NOT NULL DEFAULT 0,
    handover_hash    TEXT,
    created_at       TEXT NOT NULL,
    verified_at      TEXT,
    collected_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_claims_query ON claims(query_id);
CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status, created_at);

-- Transactional outbox. Rows are written in the same transaction as the state
-- change that caused them and delivered afterwards, so a notification is never
-- lost because delivery failed, and never sent twice because dedupe_key is
-- unique.
CREATE TABLE IF NOT EXISTS notifications (
    id         TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    audience   TEXT NOT NULL DEFAULT 'owner'
               CHECK (audience IN ('owner','staff')),
    report_id  TEXT,
    recipient  TEXT,
    subject    TEXT NOT NULL,
    body       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending','sent','failed','suppressed')),
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    sent_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_notifications_status ON notifications(status, created_at);
"""

# The only table names that may ever be interpolated into SQL. Validating
# against this set is what keeps the f-string statements below safe.
TABLES = frozenset(
    {
        "reports",
        "users",
        "id_sequences",
        "campus_match_versions",
        "schema_metadata",
        "report_owners",
        "campuses",
        "vectors",
        "report_embeddings",
        "ground_truth",
        "matches",
        "feedback",
        "audit_log",
        "claims",
        "notifications",
    }
)
RESETTABLES = TABLES - {"users", "campuses", "audit_log", "schema_metadata"}


@dataclass
class Report:
    id: str
    kind: str
    created_at: str
    description: str
    attrs: dict
    node_key: str | None = None
    location_note: str = ""
    latitude: float | None = None
    longitude: float | None = None
    location_accuracy_m: float | None = None
    lost_from: str | None = None
    lost_to: str | None = None
    lang: str = "en"
    status: str = "open"
    image_path: str | None = None
    has_photo: bool = False
    is_synthetic_art: bool = False
    reporter_role: str = "student"
    category: str | None = None
    campus_id: str | None = None
    campus_timezone: str = "Asia/Kolkata"
    extra: dict = field(default_factory=dict)

    def insert_tuple(self) -> tuple:
        return (
            self.id,
            self.kind,
            self.status,
            self.created_at,
            self.lost_from,
            self.lost_to,
            self.node_key,
            self.location_note,
            self.latitude,
            self.longitude,
            self.location_accuracy_m,
            self.description,
            self.lang,
            self.category,
            json.dumps(self.attrs),
            self.image_path,
            int(self.has_photo),
            int(self.is_synthetic_art),
            self.reporter_role,
            int(self.status == "pending_review"),
            self.campus_id,
        )


def connect(path: Path | str | None = None, *, initialize: bool = True):
    con: Any
    if path is None and settings.database_url:
        from .db import SQLAlchemyConnection, get_database_engine, resolve_database_url

        url = resolve_database_url()
        if url.get_backend_name() == "postgresql":
            from sqlalchemy.exc import SQLAlchemyError

            con = SQLAlchemyConnection(get_database_engine().connect())
            if initialize:
                try:
                    version = con.execute("SELECT version_num FROM alembic_version").fetchone()
                except SQLAlchemyError:
                    con.close()
                    raise
                if version is None:
                    con.close()
                    raise RuntimeError("PostgreSQL schema is not migrated; run `alembic upgrade head`.")
                if version["version_num"] != "0010":
                    con.close()
                    raise RuntimeError("PostgreSQL schema is not at revision 0010; run `alembic upgrade head`.")
                try:
                    con.execute("SELECT id FROM reports LIMIT 0")
                except SQLAlchemyError:
                    con.close()
                    raise
            return con
        if url.database:
            path = url.database

    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    if initialize:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(DDL)
        columns = {row["name"] for row in con.execute("PRAGMA table_info(reports)")}
        for column in ("latitude", "longitude", "location_accuracy_m"):
            if column not in columns:
                con.execute(f"ALTER TABLE reports ADD COLUMN {column} REAL")
        for column, definition in (
            ("campus_id", "TEXT"),
            ("owner_user_id", "TEXT"),
            ("deleted_at", "TEXT"),
            ("pending_review", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column not in columns:
                con.execute(f"ALTER TABLE reports ADD COLUMN {column} {definition}")
        report_columns = {row["name"] for row in con.execute("PRAGMA table_info(reports)")}
        if {"pending_review", "status"} <= report_columns:
            con.execute("UPDATE reports SET pending_review = 1 WHERE status = 'pending_review' AND pending_review = 0")
        feedback_columns = {row["name"] for row in con.execute("PRAGMA table_info(feedback)")}
        for column in ("campus_id", "model_version", "operating_point_version"):
            if column not in feedback_columns:
                con.execute(f"ALTER TABLE feedback ADD COLUMN {column} TEXT")
        feedback_columns = {row["name"] for row in con.execute("PRAGMA table_info(feedback)")}
        if {"campus_id", "kind", "created_at", "id"} <= report_columns:
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_reports_campus_kind_created ON reports(campus_id, kind, created_at, id)"
            )
        if {"latitude", "longitude"} <= report_columns:
            con.execute("CREATE INDEX IF NOT EXISTS idx_reports_location ON reports(latitude, longitude)")
        if "deleted_at" in report_columns:
            con.execute("CREATE INDEX IF NOT EXISTS idx_reports_not_deleted ON reports(deleted_at)")
        if {"campus_id", "created_at"} <= feedback_columns:
            con.execute("CREATE INDEX IF NOT EXISTS idx_feedback_campus_created ON feedback(campus_id, created_at)")
        if "created_at" in report_columns:
            from .timeutil import normalize_legacy_timestamps

            normalize_legacy_timestamps(con, settings.default_campus_timezone)
        con.commit()
    return con


def reset(con: sqlite3.Connection) -> None:
    for table in sorted(RESETTABLES):
        # `table` is drawn from the fixed TABLES set, never from input.
        con.execute(f"DELETE FROM {table}")  # noqa: S608
    con.commit()


def campus_timezone(con, campus_id: str | None) -> str:
    if campus_id:
        row = con.execute("SELECT timezone FROM campuses WHERE id=?", (campus_id,)).fetchone()
        if row is not None:
            return row["timezone"]
    return settings.default_campus_timezone


def campus_match_version(con, campus_id: str) -> int:
    row = con.execute("SELECT version FROM campus_match_versions WHERE campus_id=?", (campus_id,)).fetchone()
    return int(row["version"]) if row else 0


def bump_campus_match_version(con, campus_id: str) -> None:
    con.execute(
        """INSERT INTO campus_match_versions (campus_id, version) VALUES (?, 1)
           ON CONFLICT(campus_id) DO UPDATE SET version=campus_match_versions.version + 1""",
        (campus_id,),
    )


def next_id(con: sqlite3.Connection, prefix: str) -> str:
    if getattr(getattr(con, "dialect", None), "name", None) == "postgresql":
        ids = con.execute("SELECT id FROM reports WHERE id LIKE ?", (f"{prefix}-%",)).fetchall()
        n = max((int(row["id"].rsplit("-", 1)[1]) for row in ids), default=0) + 1
        row = con.execute(
            """INSERT INTO id_sequences (prefix, next_value) VALUES (?, ?)
               ON CONFLICT(prefix) DO UPDATE
               SET next_value=GREATEST(id_sequences.next_value, excluded.next_value - 1) + 1
               RETURNING next_value""",
            (prefix, n + 1),
        ).fetchone()
        con.commit()
        return f"{prefix}-{int(row['next_value']) - 1:04d}"
    if con.in_transaction:
        raise RuntimeError("next_id must be called outside an active transaction.")
    con.execute("BEGIN IMMEDIATE")
    row = con.execute("SELECT next_value FROM id_sequences WHERE prefix=?", (prefix,)).fetchone()
    if row:
        n = int(row["next_value"])
        con.execute("UPDATE id_sequences SET next_value=? WHERE prefix=?", (n + 1, prefix))
    else:
        ids = con.execute("SELECT id FROM reports WHERE id LIKE ?", (f"{prefix}-%",)).fetchall()
        n = max((int(item["id"].rsplit("-", 1)[1]) for item in ids), default=0) + 1
        con.execute("INSERT INTO id_sequences (prefix, next_value) VALUES (?, ?)", (prefix, n + 1))
    con.commit()
    return f"{prefix}-{n:04d}"


def ensure_user(
    con: sqlite3.Connection,
    user_id: str,
    *,
    email: str | None = None,
    phone_e164: str | None = None,
) -> None:
    con.execute(
        """INSERT INTO users (id, email, phone_e164, role, campus_id, created_at)
           VALUES (?, ?, ?, 'student', ?, ?)
           ON CONFLICT(id) DO UPDATE SET
               email=COALESCE(excluded.email, users.email),
               phone_e164=COALESCE(excluded.phone_e164, users.phone_e164),
               campus_id=COALESCE(users.campus_id, excluded.campus_id)""",
        (
            user_id,
            email,
            phone_e164,
            settings.default_campus_id,
            iso_utc(now_utc()),
        ),
    )


def get_user(con: sqlite3.Connection, user_id: str) -> sqlite3.Row | None:
    return con.execute("SELECT id, email, phone_e164, role, campus_id FROM users WHERE id=?", (user_id,)).fetchone()


def list_users(con: sqlite3.Connection, *, campus_id: str | None = None) -> list[sqlite3.Row]:
    if campus_id is None:
        return con.execute(
            "SELECT id, email, phone_e164, role, campus_id FROM users ORDER BY email, id"
        ).fetchall()
    return con.execute(
        "SELECT id, email, phone_e164, role, campus_id FROM users WHERE campus_id=? ORDER BY email, id",
        (campus_id,),
    ).fetchall()


def set_user_role(con: sqlite3.Connection, user_id: str, role: str, campus_id: str | None = None) -> None:
    roles = {"student", "staff", "campus_admin", "superadmin"}
    if role not in roles:
        raise ValueError(f"unknown role: {role}")
    con.execute(
        """INSERT INTO users (id, role, campus_id, created_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET role=excluded.role, campus_id=excluded.campus_id""",
        (user_id, role, campus_id, iso_utc(now_utc())),
    )


def add_report_owner(
    con: sqlite3.Connection,
    report_id: str,
    *,
    user_id: str | None,
    contact_email: str | None,
    contact_phone: str | None,
    token_hash: str,
) -> None:
    con.execute(
        """INSERT INTO report_owners
           (report_id, user_id, contact_email, contact_phone, token_hash, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            report_id,
            user_id,
            contact_email,
            contact_phone,
            token_hash,
            iso_utc(now_utc()),
        ),
    )


def report_contact(con: sqlite3.Connection, report_id: str) -> dict:
    """Where to reach the person who filed a report, if they left a contact."""
    row = con.execute(
        "SELECT user_id, contact_email, contact_phone FROM report_owners WHERE report_id=?",
        (report_id,),
    ).fetchone()
    return dict(row) if row else {}


def report_owner_token_hash(con: sqlite3.Connection, report_id: str) -> str | None:
    row = con.execute("SELECT token_hash FROM report_owners WHERE report_id=?", (report_id,)).fetchone()
    return row["token_hash"] if row else None


def add_audit(
    con,
    *,
    actor_id: str | None,
    action: str,
    entity: str,
    entity_id: str,
    before: dict | None = None,
    after: dict | None = None,
    created_at: str | None = None,
) -> str:
    audit_id = uuid.uuid4().hex
    con.execute(
        """INSERT INTO audit_log
           (id, actor_id, action, entity, entity_id, before_json, after_json, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            audit_id,
            actor_id,
            action,
            entity,
            entity_id,
            json.dumps(before) if before is not None else None,
            json.dumps(after) if after is not None else None,
            iso_utc(created_at) if created_at else iso_utc(now_utc()),
        ),
    )
    return audit_id


# ------------------------------------------------------------------- claims
CLAIM_STATUSES = frozenset({"pending", "verified", "collected", "rejected", "expired"})


def add_claim(
    con,
    *,
    claim_id: str,
    query_id: str,
    candidate_id: str,
    campus_id: str | None,
    claimant_user_id: str | None,
    claimant_email: str | None,
    token_hash: str,
    challenge: list[dict],
    score: float,
    status: str = "pending",
    created_at: str | None = None,
) -> None:
    if status not in CLAIM_STATUSES:
        raise ValueError(f"unknown claim status: {status}")
    con.execute(
        """INSERT INTO claims
           (id, query_id, candidate_id, campus_id, claimant_user_id, claimant_email,
            token_hash, status, challenge_json, score, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            claim_id,
            query_id,
            candidate_id,
            campus_id,
            claimant_user_id,
            claimant_email,
            token_hash,
            status,
            json.dumps(challenge),
            score,
            iso_utc(created_at) if created_at else iso_utc(now_utc()),
        ),
    )


def _claim(row) -> dict:
    claim = dict(row)
    claim["challenge"] = json.loads(claim.pop("challenge_json"))
    return claim


def get_claim(con, claim_id: str, *, campus_id: str | None = None) -> dict | None:
    query = "SELECT * FROM claims WHERE id=?"
    args: list = [claim_id]
    if campus_id is not None:
        query += " AND COALESCE(campus_id, ?) = ?"
        args.extend((settings.default_campus_id, campus_id))
    row = con.execute(query, args).fetchone()
    return _claim(row) if row else None


def list_claims(
    con,
    *,
    query_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    campus_id: str | None = None,
) -> list[dict]:
    query = "SELECT * FROM claims"
    args: list = []
    clauses: list[str] = []
    if query_id is not None:
        clauses.append("query_id=?")
        args.append(query_id)
    if status is not None:
        clauses.append("status=?")
        args.append(status)
    if campus_id is not None:
        clauses.append("COALESCE(campus_id, ?) = ?")
        args.extend((settings.default_campus_id, campus_id))
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC, id DESC LIMIT ?"
    args.append(limit)
    return [_claim(row) for row in con.execute(query, args)]


def update_claim(
    con,
    claim_id: str,
    *,
    status: str,
    handover_hash: str | None = None,
    verified_at: str | None = None,
    collected_at: str | None = None,
) -> None:
    if status not in CLAIM_STATUSES:
        raise ValueError(f"unknown claim status: {status}")
    con.execute(
        """UPDATE claims SET status=?, handover_hash=COALESCE(?, handover_hash),
                            verified_at=COALESCE(?, verified_at),
                            collected_at=COALESCE(?, collected_at)
           WHERE id=?""",
        (status, handover_hash, verified_at, collected_at, claim_id),
    )


def increment_claim_attempts(con, claim_id: str) -> int:
    con.execute("UPDATE claims SET attempts = attempts + 1 WHERE id=?", (claim_id,))
    row = con.execute("SELECT attempts FROM claims WHERE id=?", (claim_id,)).fetchone()
    return int(row["attempts"]) if row else 0


# -------------------------------------------------------------- notifications
def add_notification(
    con,
    *,
    notification_id: str,
    dedupe_key: str,
    subject: str,
    body: str,
    audience: str = "owner",
    report_id: str | None = None,
    recipient: str | None = None,
    created_at: str | None = None,
) -> bool:
    """Queue one notification; returns False when ``dedupe_key`` already exists."""
    cursor = con.execute(
        """INSERT INTO notifications
           (id, dedupe_key, audience, report_id, recipient, subject, body, created_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(dedupe_key) DO NOTHING""",
        (
            notification_id,
            dedupe_key,
            audience,
            report_id,
            recipient,
            subject,
            body,
            iso_utc(created_at) if created_at else iso_utc(now_utc()),
        ),
    )
    return cursor.rowcount > 0


def pending_notifications(con, limit: int = 50) -> list[dict]:
    return [
        dict(row)
        for row in con.execute(
            "SELECT * FROM notifications WHERE status='pending' ORDER BY created_at, id LIMIT ?",
            (limit,),
        )
    ]


def mark_notification(
    con,
    notification_id: str,
    status: str,
    *,
    error: str | None = None,
    sent_at: str | None = None,
) -> None:
    if status not in {"pending", "sent", "failed", "suppressed"}:
        raise ValueError(f"unknown notification status: {status}")
    con.execute(
        """UPDATE notifications
           SET status=?, attempts=attempts + 1, last_error=?, sent_at=COALESCE(?, sent_at)
           WHERE id=?""",
        (status, error, iso_utc(sent_at) if sent_at else None, notification_id),
    )


def notification_counts(con) -> dict[str, int]:
    counts = {"pending": 0, "sent": 0, "failed": 0, "suppressed": 0}
    for row in con.execute("SELECT status, COUNT(*) AS n FROM notifications GROUP BY status"):
        counts[row["status"]] = int(row["n"])
    return counts


def insert_report(con: sqlite3.Connection, r: Report) -> None:
    r.campus_id = r.campus_id or settings.default_campus_id
    timezone = campus_timezone(con, r.campus_id)
    r.created_at = iso_utc(r.created_at, timezone)
    r.lost_from = iso_utc(r.lost_from, timezone) if r.lost_from else None
    r.lost_to = iso_utc(r.lost_to, timezone) if r.lost_to else None
    con.execute(
        """INSERT INTO reports
           (id, kind, status, created_at, lost_from, lost_to, node_key, location_note,
            latitude, longitude, location_accuracy_m, description, lang, category, attrs_json, image_path, has_photo,
            is_synthetic_art, reporter_role, pending_review, campus_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             kind=excluded.kind, status=excluded.status, created_at=excluded.created_at,
             lost_from=excluded.lost_from, lost_to=excluded.lost_to, node_key=excluded.node_key,
             location_note=excluded.location_note, latitude=excluded.latitude, longitude=excluded.longitude,
             location_accuracy_m=excluded.location_accuracy_m, description=excluded.description,
             lang=excluded.lang, category=excluded.category, attrs_json=excluded.attrs_json,
             image_path=excluded.image_path, has_photo=excluded.has_photo,
             is_synthetic_art=excluded.is_synthetic_art, reporter_role=excluded.reporter_role,
             pending_review=excluded.pending_review, campus_id=excluded.campus_id""",
        r.insert_tuple(),
    )
    bump_campus_match_version(con, r.campus_id)


def row_to_report(row: sqlite3.Row) -> Report:
    try:
        report_timezone = row["campus_timezone"]
    except (IndexError, KeyError):
        report_timezone = settings.default_campus_timezone
    return Report(
        id=row["id"],
        kind=row["kind"],
        created_at=iso_utc(row["created_at"], report_timezone),
        description=row["description"],
        attrs=json.loads(row["attrs_json"]),
        node_key=row["node_key"],
        location_note=row["location_note"],
        latitude=row["latitude"],
        longitude=row["longitude"],
        location_accuracy_m=row["location_accuracy_m"],
        lost_from=iso_utc(row["lost_from"], report_timezone) if row["lost_from"] else None,
        lost_to=iso_utc(row["lost_to"], report_timezone) if row["lost_to"] else None,
        lang=row["lang"],
        status=row["status"],
        image_path=row["image_path"],
        has_photo=bool(row["has_photo"]),
        is_synthetic_art=bool(row["is_synthetic_art"]),
        reporter_role=row["reporter_role"],
        category=row["category"],
        campus_id=row["campus_id"] or settings.default_campus_id,
        campus_timezone=report_timezone,
    )


def get_report(con: sqlite3.Connection, report_id: str, *, campus_id: str | None = None) -> Report | None:
    query = """SELECT reports.*,
                      COALESCE((SELECT timezone FROM campuses
                                WHERE campuses.id=COALESCE(reports.campus_id, ?)), ?) AS campus_timezone
               FROM reports WHERE id = ?"""
    args = [settings.default_campus_id, settings.default_campus_timezone, report_id]
    if campus_id is not None:
        query += " AND COALESCE(campus_id, ?) = ?"
        args.extend((settings.default_campus_id, campus_id))
    row = con.execute(query, args).fetchone()
    return row_to_report(row) if row else None


def list_reports(
    con,
    kind: str | None = None,
    limit: int | None = None,
    *,
    include_pending: bool = False,
    cursor: tuple[str, str] | None = None,
    campus_id: str | None = None,
) -> list[Report]:
    q = """SELECT reports.*,
                  COALESCE((SELECT timezone FROM campuses
                            WHERE campuses.id=COALESCE(reports.campus_id, ?)), ?) AS campus_timezone
           FROM reports"""
    args: list = [settings.default_campus_id, settings.default_campus_timezone]
    if not include_pending:
        q += " WHERE status != 'pending_review'"
    has_where = not include_pending
    if campus_id is not None:
        q += " AND COALESCE(campus_id, ?) = ?" if has_where else " WHERE COALESCE(campus_id, ?) = ?"
        args.extend((settings.default_campus_id, campus_id))
        has_where = True
    if kind:
        q += " AND kind = ?" if has_where else " WHERE kind = ?"
        args.append(kind)
        has_where = True
    if cursor:
        q += (
            " AND (created_at < ? OR (created_at = ? AND id < ?))"
            if has_where
            else " WHERE created_at < ? OR (created_at = ? AND id < ?)"
        )
        args.extend((cursor[0], cursor[0], cursor[1]))
    q += " ORDER BY created_at DESC, id DESC"
    if limit is not None:
        q += " LIMIT ?"
        args.append(limit)
    return [row_to_report(r) for r in con.execute(q, args)]


def count(con, table: str) -> int:
    if table not in TABLES:
        raise ValueError(f"unknown table: {table!r}")
    # The name is validated against TABLES directly above, so this is not an
    # injection surface: it can only ever be one of five known literals.
    return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608


def put_vector(con, report_id: str, kind: str, model: str, vec: np.ndarray) -> None:
    if getattr(getattr(con, "dialect", None), "name", None) == "postgresql":
        vector = np.asarray(vec, dtype=np.float32)
        if vector.ndim != 1 or not np.isfinite(vector).all():
            raise ValueError("embedding vector must be a one-dimensional array of finite values")
        literal = "[" + ",".join(format(float(value), ".9g") for value in vector) + "]"
        con.execute(
            """INSERT INTO report_embeddings (report_id, kind, model, dim, embedding)
               VALUES (?, ?, ?, ?, CAST(? AS vector))
               ON CONFLICT(report_id, kind, model) DO UPDATE SET
                 dim=excluded.dim, embedding=excluded.embedding""",
            (report_id, kind, model, len(vector), literal),
        )
        return
    con.execute(
        """INSERT INTO vectors (report_id, kind, model, dim, vec) VALUES (?,?,?,?,?)
           ON CONFLICT(report_id, kind, model) DO UPDATE SET dim=excluded.dim, vec=excluded.vec""",
        (report_id, kind, model, len(vec), vec.astype(np.float32).tobytes()),
    )


def _decode_vector(value, dimension: int) -> np.ndarray:
    if isinstance(value, bytes | bytearray | memoryview):
        return np.frombuffer(value, dtype=np.float32).copy()
    if isinstance(value, str):
        vector = np.fromstring(value.strip().strip("[]"), sep=",", dtype=np.float32)
        if len(vector) != dimension:
            raise ValueError(f"stored embedding has {len(vector)} values; expected {dimension}")
        return vector
    raise TypeError("database returned an unsupported embedding representation")


def get_vector(con, report_id: str, kind: str, model: str) -> np.ndarray | None:
    postgres = getattr(getattr(con, "dialect", None), "name", None) == "postgresql"
    query = (
        "SELECT dim, embedding::text AS vec FROM report_embeddings WHERE report_id=? AND kind=? AND model=?"
        if postgres
        else "SELECT dim, vec FROM vectors WHERE report_id=? AND kind=? AND model=?"
    )
    row = con.execute(
        query,
        (report_id, kind, model),
    ).fetchone()
    if not row:
        return None
    return _decode_vector(row["vec"], int(row["dim"]))


def load_vectors(con, kind: str, model: str, *, campus_id: str | None = None) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    postgres = getattr(getattr(con, "dialect", None), "name", None) == "postgresql"
    query = (
        "SELECT e.report_id, e.dim, e.embedding::text AS vec FROM report_embeddings AS e "
        "JOIN reports AS r ON r.id=e.report_id WHERE e.kind=? AND e.model=?"
        if postgres
        else "SELECT v.report_id, v.dim, v.vec FROM vectors AS v JOIN reports AS r ON r.id=v.report_id "
        "WHERE v.kind=? AND v.model=?"
    )
    args: list[str] = [kind, model]
    if campus_id is not None:
        query += " AND COALESCE(r.campus_id, ?) = ?"
        args.extend((settings.default_campus_id, campus_id))
    for row in con.execute(
        query,
        args,
    ):
        out[row["report_id"]] = _decode_vector(row["vec"], int(row["dim"]))
    return out


def add_ground_truth(con, item_id: str, lost_id: str | None, found_id: str | None) -> None:
    con.execute(
        """INSERT INTO ground_truth (item_id, lost_id, found_id) VALUES (?,?,?)
           ON CONFLICT(item_id) DO UPDATE SET lost_id=excluded.lost_id, found_id=excluded.found_id""",
        (item_id, lost_id, found_id),
    )


def truth_pairs(con, *, campus_id: str | None = None) -> dict[str, str]:
    """lost_id -> found_id for every item that has both reports."""
    query = (
        "SELECT gt.lost_id, gt.found_id FROM ground_truth AS gt "
        "JOIN reports AS lost ON lost.id=gt.lost_id "
        "JOIN reports AS found ON found.id=gt.found_id "
        "WHERE gt.lost_id IS NOT NULL AND gt.found_id IS NOT NULL"
    )
    args: list[str] = []
    if campus_id is not None:
        query += " AND COALESCE(lost.campus_id, ?) = ? AND COALESCE(found.campus_id, ?) = ?"
        args.extend((settings.default_campus_id, campus_id, settings.default_campus_id, campus_id))
    return {row["lost_id"]: row["found_id"] for row in con.execute(query, args)}


def add_feedback(
    con,
    *,
    query_id: str,
    candidate_id: str,
    action: str,
    prob: float | None,
    score: float | None,
    features: dict,
    campus_id: str | None = None,
    staff: str | None = None,
    note: str | None = None,
    created_at: str | None = None,
) -> None:
    con.execute(
        """INSERT INTO feedback
           (query_id, candidate_id, action, staff, note, prob, score, features_json, created_at, campus_id)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(query_id, candidate_id) DO UPDATE SET
             action=excluded.action, staff=excluded.staff, note=excluded.note,
             prob=excluded.prob, score=excluded.score,
             features_json=excluded.features_json, created_at=excluded.created_at,
             campus_id=excluded.campus_id""",
        (
            query_id,
            candidate_id,
            action,
            staff,
            note,
            prob,
            score,
            json.dumps(features),
            iso_utc(created_at, campus_timezone(con, campus_id)) if created_at else iso_utc(now_utc()),
            campus_id,
        ),
    )


def feedback_for_query(con, query_id: str, *, campus_id: str | None = None) -> dict[str, str]:
    query = (
        "SELECT f.candidate_id, f.action FROM feedback AS f "
        "JOIN reports AS query ON query.id=f.query_id "
        "JOIN reports AS candidate ON candidate.id=f.candidate_id WHERE f.query_id=?"
    )
    args = [query_id]
    if campus_id is not None:
        query += (
            " AND COALESCE(f.campus_id, ?) = ?"
            " AND COALESCE(query.campus_id, ?) = ?"
            " AND COALESCE(candidate.campus_id, ?) = ?"
        )
        args.extend(
            (
                settings.default_campus_id,
                campus_id,
                settings.default_campus_id,
                campus_id,
                settings.default_campus_id,
                campus_id,
            )
        )
    return {row["candidate_id"]: row["action"] for row in con.execute(query, args)}


def feedback_counts(con, *, campus_id: str | None = None) -> dict[str, int]:
    counts = {"confirmed": 0, "rejected": 0}
    query = (
        "SELECT f.action, COUNT(*) AS n FROM feedback AS f "
        "JOIN reports AS query ON query.id=f.query_id "
        "JOIN reports AS candidate ON candidate.id=f.candidate_id"
    )
    args: list[str] = []
    if campus_id is not None:
        query += (
            " WHERE COALESCE(f.campus_id, ?) = ?"
            " AND COALESCE(query.campus_id, ?) = ?"
            " AND COALESCE(candidate.campus_id, ?) = ?"
        )
        args.extend(
            (
                settings.default_campus_id,
                campus_id,
                settings.default_campus_id,
                campus_id,
                settings.default_campus_id,
                campus_id,
            )
        )
    elif campus_id is None:
        query += " WHERE 1=1"
    query += " GROUP BY f.action"
    for row in con.execute(query, args):
        counts["confirmed" if row["action"] == "confirm" else "rejected"] = int(row["n"])
    return counts


def list_feedback(
    con,
    limit: int | None = None,
    cursor: tuple[str, str, str] | None = None,
    *,
    campus_id: str | None = None,
) -> list[dict]:
    query = (
        "SELECT f.* FROM feedback AS f "
        "JOIN reports AS query ON query.id=f.query_id "
        "JOIN reports AS candidate ON candidate.id=f.candidate_id"
    )
    args: list[str | int] = []
    has_where = False
    if campus_id is not None:
        query += (
            " WHERE COALESCE(f.campus_id, ?) = ?"
            " AND COALESCE(query.campus_id, ?) = ?"
            " AND COALESCE(candidate.campus_id, ?) = ?"
        )
        args.extend((settings.default_campus_id, campus_id))
        args.extend((settings.default_campus_id, campus_id, settings.default_campus_id, campus_id))
        has_where = True
    if cursor:
        query += " AND" if has_where else " WHERE"
        query += (
            " (f.created_at < ? OR (f.created_at = ? AND (f.query_id < ? OR (f.query_id = ? AND f.candidate_id < ?)))"
        )
        query += ")"
        args.extend((cursor[0], cursor[0], cursor[1], cursor[1], cursor[2]))
    query += " ORDER BY f.created_at DESC, f.query_id DESC, f.candidate_id DESC"
    if limit is not None:
        query += " LIMIT ?"
        args.append(limit)
    return [dict(row) for row in con.execute(query, args)]


def set_status(con, report_id: str, status: str) -> None:
    row = con.execute(
        "SELECT COALESCE(campus_id, ?) AS campus_id FROM reports WHERE id=?",
        (settings.default_campus_id, report_id),
    ).fetchone()
    con.execute(
        "UPDATE reports SET status=?, pending_review=? WHERE id=?",
        (status, int(status == "pending_review"), report_id),
    )
    if row is not None:
        bump_campus_match_version(con, row["campus_id"])


def save_matches(con, query_id: str, ranked: list[dict], created_at: str, decision: str = "candidate") -> None:
    con.execute("DELETE FROM matches WHERE query_id = ?", (query_id,))
    con.executemany(
        "INSERT INTO matches (query_id, candidate_id, score, prob, decision, reasons_json, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        [
            (
                query_id,
                m["candidate_id"],
                m["score"],
                m["prob"],
                m.get("decision", decision),
                json.dumps(m["reasons"]),
                created_at,
            )
            for m in ranked
        ],
    )


def match_query_ids_for_candidate(con, candidate_id: str, campus_id: str) -> set[str]:
    rows = con.execute(
        """SELECT m.query_id
           FROM matches AS m
           JOIN reports AS query ON query.id = m.query_id
           JOIN reports AS candidate ON candidate.id = m.candidate_id
           WHERE m.candidate_id = ?
             AND COALESCE(query.campus_id, ?) = ?
             AND COALESCE(candidate.campus_id, ?) = ?""",
        (
            candidate_id,
            settings.default_campus_id,
            campus_id,
            settings.default_campus_id,
            campus_id,
        ),
    )
    return {row["query_id"] for row in rows}
