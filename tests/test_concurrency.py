from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from lostfound.api import create_app
from lostfound.ratelimit import RateLimiter


def test_fifty_parallel_reports_get_unique_ids_without_database_errors(tmp_path):
    app = create_app(tmp_path / "concurrent.db", rate_limiter=RateLimiter(None, disabled=True))
    with TestClient(app) as client:

        def submit(index: int):
            return client.post(
                "/api/reports",
                json={
                    "kind": "found",
                    "description": f"Found a blue bottle number {index} near the canteen",
                    "contact_email": "reporter@example.com",
                },
            )

        with ThreadPoolExecutor(max_workers=50) as executor:
            responses = list(executor.map(submit, range(50)))

    assert all(response.status_code == 200 for response in responses), [
        response.text for response in responses if response.status_code != 200
    ]
    report_ids = [response.json()["report"]["id"] for response in responses]
    assert len(set(report_ids)) == 50
    assert {int(report_id.split("-")[1]) for report_id in report_ids} == set(range(1, 51))
