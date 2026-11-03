from __future__ import annotations

from lostfound.config import TEXT_EMBEDDER
from lostfound.store import Report, connect, get_vector, insert_report
from lostfound.worker import ReportMatchProcessor


def test_report_match_processor_updates_worker_matcher_and_persists_results(tmp_path):
    database = tmp_path / "worker.db"
    con = connect(database)
    insert_report(
        con,
        Report(
            id="LR-0001",
            kind="lost",
            created_at="2026-10-28T10:00:00",
            description="lost a blue water bottle",
            attrs={"category": "bottle", "primary_color": "blue"},
            lost_from="2026-10-28T09:00:00",
            lost_to="2026-10-28T10:00:00",
        ),
    )
    insert_report(
        con,
        Report(
            id="FR-0001",
            kind="found",
            created_at="2026-10-28T11:00:00",
            description="found a blue water bottle",
            attrs={"category": "bottle", "primary_color": "blue"},
        ),
    )
    con.commit()
    con.close()

    processor = ReportMatchProcessor(database)
    result = processor("rematch_report", {"report_id": "LR-0001"})

    assert result["report_id"] == "LR-0001"
    assert result["status"] == "complete"
    assert result["matches"] == 1
    verify = connect(database, initialize=False)
    try:
        assert verify.execute("SELECT COUNT(*) FROM matches WHERE query_id='LR-0001'").fetchone()[0] == 1
        assert (
            verify.execute(
                "SELECT COUNT(*) FROM matches WHERE query_id='FR-0001' AND candidate_id='LR-0001'"
            ).fetchone()[0]
            == 1
        )
        assert get_vector(verify, "LR-0001", "text", TEXT_EMBEDDER) is not None
    finally:
        verify.close()


def test_worker_refreshes_its_matcher_after_another_process_adds_a_report(tmp_path):
    database = tmp_path / "worker-invalidation.db"
    con = connect(database)
    insert_report(
        con,
        Report(
            id="LR-0001",
            kind="lost",
            created_at="2026-10-28T10:00:00",
            description="lost a blue water bottle",
            attrs={"category": "bottle", "primary_color": "blue"},
        ),
    )
    insert_report(
        con,
        Report(
            id="FR-0001",
            kind="found",
            created_at="2026-10-28T11:00:00",
            description="found a blue water bottle",
            attrs={"category": "bottle", "primary_color": "blue"},
        ),
    )
    con.commit()
    con.close()

    processor = ReportMatchProcessor(database)
    processor("rematch_report", {"report_id": "FR-0001"})

    con = connect(database, initialize=False)
    insert_report(
        con,
        Report(
            id="LR-0002",
            kind="lost",
            created_at="2026-10-28T10:30:00",
            description="lost another blue water bottle",
            attrs={"category": "bottle", "primary_color": "blue"},
        ),
    )
    con.commit()
    con.close()

    processor("rematch_report", {"report_id": "FR-0001"})

    verify = connect(database, initialize=False)
    try:
        candidates = {
            row["candidate_id"] for row in verify.execute("SELECT candidate_id FROM matches WHERE query_id='FR-0001'")
        }
    finally:
        verify.close()

    assert candidates == {"LR-0001", "LR-0002"}
