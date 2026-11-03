"""Measure local API latency under concurrent Phase 2 write and match load."""

from __future__ import annotations

import argparse
import json
import logging
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from lostfound.api import create_app
from lostfound.pipeline import build_database
from lostfound.ratelimit import RateLimiter


def _percentile(samples: list[float], percentile: int) -> float:
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, (len(ordered) * percentile + 99) // 100 - 1))
    return round(ordered[index], 2)


def _measure(client: TestClient, requests: list[tuple[str, dict[str, Any]]], workers: int) -> dict[str, Any]:
    def send(request: tuple[str, dict[str, Any]]) -> tuple[float, int, dict[str, Any]]:
        path, payload = request
        started = time.perf_counter()
        response = client.post(path, json=payload) if path == "/api/reports" else client.post(path, params=payload)
        elapsed_ms = (time.perf_counter() - started) * 1000
        try:
            body = response.json()
        except ValueError:
            body = {}
        return elapsed_ms, response.status_code, body

    results = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(send, request) for request in requests]
        for future in as_completed(futures):
            results.append(future.result())

    latencies = [result[0] for result in results]
    errors = sum(status < 200 or status >= 300 for _, status, _ in results)
    return {
        "requests": len(results),
        "errors": errors,
        "error_rate": round(errors / len(results), 4) if results else 0,
        "latency_ms": {
            "p50": _percentile(latencies, 50) if latencies else 0,
            "p95": _percentile(latencies, 95) if latencies else 0,
            "p99": _percentile(latencies, 99) if latencies else 0,
        },
        "results": results,
    }


def run_load_test(report_count: int = 200, match_count: int = 100, workers: int = 32) -> dict[str, Any]:
    if report_count < 2 or match_count < 1 or workers < 1:
        raise ValueError("report_count must be >= 2; match_count and workers must be positive.")
    with tempfile.TemporaryDirectory(prefix="lostfound-phase2-load-") as directory:
        database = Path(directory) / "load.db"
        build_database(db_path=database, n_items=20, seed=20261028, render_images=False, verbose=False)
        app = create_app(database, rate_limiter=RateLimiter(None, disabled=True))
        with TestClient(app) as client:
            report_requests = [
                (
                    "/api/reports",
                    {
                        "kind": "lost" if index % 2 == 0 else "found",
                        "description": f"Load-test report {index} for a blue water bottle",
                        "contact_email": f"load-{index}@example.invalid",
                    },
                )
                for index in range(report_count)
            ]
            created = _measure(client, report_requests, workers)
            if created["errors"]:
                created["results"] = [
                    {"status": status, "body": body}
                    for _, status, body in created["results"]
                    if status < 200 or status >= 300
                ]
                raise RuntimeError(f"report-creation load had errors: {json.dumps(created, sort_keys=True)}")

            report_ids = [body["report"]["id"] for _, _, body in created["results"]]
            match_requests = [
                (f"/api/match/{report_ids[index % len(report_ids)]}", {"top": 5}) for index in range(match_count)
            ]
            matched = _measure(client, match_requests, workers)
            if matched["errors"]:
                matched["results"] = [
                    {"status": status, "body": body}
                    for _, status, body in matched["results"]
                    if status < 200 or status >= 300
                ]
                raise RuntimeError(f"match load had errors: {json.dumps(matched, sort_keys=True)}")
            return {
                "backend": "SQLite demo",
                "workers": workers,
                "report_creation": {key: value for key, value in created.items() if key != "results"},
                "matching": {key: value for key, value in matched.items() if key != "results"},
            }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=int, default=200)
    parser.add_argument("--matches", type=int, default=100)
    parser.add_argument("--workers", type=int, default=32)
    args = parser.parse_args()
    logging.disable(logging.INFO)
    result = run_load_test(args.reports, args.matches, args.workers)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
