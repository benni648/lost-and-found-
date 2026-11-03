import datetime as dt

from lostfound.features import hours_between
from lostfound.store import Report, connect, insert_report
from lostfound.timeutil import iso_utc, normalize_legacy_timestamps, parse_utc


def test_aware_timestamps_are_normalized_to_utc():
    parsed = parse_utc("2026-10-26T09:00:00+05:30")
    assert parsed == dt.datetime(2026, 10, 26, 3, 30, tzinfo=dt.UTC)


def test_naive_legacy_timestamp_uses_the_campus_timezone():
    assert iso_utc("2026-10-26T03:30:00", "Asia/Kolkata") == "2026-10-25T22:00:00Z"


def test_iso_utc_returns_canonical_z_suffix():
    assert iso_utc("2026-10-26T03:30:00Z").endswith("Z")


def test_legacy_timestamp_normalization_uses_campus_timezone_and_is_idempotent(tmp_path):
    con = connect(tmp_path / "timezone.db")
    con.execute(
        "INSERT INTO campuses (id, name, short, timezone) VALUES (?, ?, ?, ?)",
        ("campus-a", "Campus A", "CA", "Asia/Kolkata"),
    )
    con.execute(
        """INSERT INTO reports
           (id, kind, status, created_at, description, attrs_json, campus_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("LR-timezone", "lost", "open", "2026-10-28T00:00:00", "legacy", "{}", "campus-a"),
    )
    con.commit()
    con.execute("DELETE FROM schema_metadata WHERE key='timestamp_format'")

    normalize_legacy_timestamps(con)
    created_at = con.execute("SELECT created_at FROM reports WHERE id='LR-timezone'").fetchone()[0]
    normalize_legacy_timestamps(con)
    repeated = con.execute("SELECT created_at FROM reports WHERE id='LR-timezone'").fetchone()[0]

    assert created_at == "2026-10-27T18:30:00Z"
    assert repeated == created_at
    con.close()


def test_new_report_timestamps_are_stored_as_utc_for_its_campus(tmp_path):
    con = connect(tmp_path / "new-report-timezone.db")
    con.execute(
        "INSERT INTO campuses (id, name, short, timezone) VALUES (?, ?, ?, ?)",
        ("campus-a", "Campus A", "CA", "Asia/Kolkata"),
    )
    report = Report(
        id="LR-new",
        kind="lost",
        created_at="2026-10-28T12:00:00",
        lost_from="2026-10-28T10:00:00",
        lost_to="2026-10-28T11:00:00",
        description="A black wallet",
        attrs={},
        campus_id="campus-a",
    )

    insert_report(con, report)
    con.commit()
    saved = con.execute("SELECT created_at, lost_from, lost_to FROM reports WHERE id=?", (report.id,)).fetchone()

    assert tuple(saved) == (
        "2026-10-28T06:30:00Z",
        "2026-10-28T04:30:00Z",
        "2026-10-28T05:30:00Z",
    )
    con.close()


def test_hours_between_accepts_an_aware_browser_timestamp():
    """The UI posts Date.toISOString(), which used to raise TypeError."""
    lost = Report(
        id="LR-1",
        kind="lost",
        created_at="2026-10-26T10:00:00",
        lost_from="2026-10-26T04:00:00",
        lost_to="2026-10-26T06:00:00",
        description="black bottle",
        attrs={},
    )
    found = Report(
        id="FR-1",
        kind="found",
        created_at="2026-10-26T06:30:00Z",
        description="black bottle",
        attrs={},
    )
    hours, impossible = hours_between(lost, found)
    assert impossible is False
    assert 0 <= hours < 24
