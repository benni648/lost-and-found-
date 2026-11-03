"""Fit the confidence calibration and write its reliability report.

    .venv/Scripts/python.exe ml/calibration/report.py --db data/lostfound.db

A calibration is applied at serve time only when it reduces holdout ECE; the
report always shows the raw-vs-calibrated comparison either way.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lostfound import store  # noqa: E402
from lostfound.matching import Matcher  # noqa: E402
from lostfound.training import calibrate_scorer, calibration_report_markdown  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fit and report confidence calibration")
    parser.add_argument("--db", default=None)
    parser.add_argument("--holdout", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--method", choices=["auto", "platt", "isotonic"], default="auto")
    parser.add_argument("--out", default=None, help="params path (default: ml/calibration/params.json)")
    parser.add_argument("--dry-run", action="store_true", help="measure without writing params")
    parser.add_argument("--force", action="store_true", help="apply even if holdout ECE does not improve")
    parser.add_argument("--report", default="ml/calibration/report.md")
    args = parser.parse_args(argv)

    con = store.connect(args.db)
    result = calibrate_scorer(
        Matcher.load(con),
        holdout=args.holdout,
        seed=args.seed,
        method=args.method,
        out=args.out,
        save=not args.dry_run,
        force=args.force,
    )
    print(f"scorer: {result['scorer']}  method: {result['params'].method}")
    print(f"ECE:   {result['raw']['ece']} -> {result['calibrated']['ece']}")
    print(f"Brier: {result['raw']['brier']} -> {result['calibrated']['brier']}")
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(calibration_report_markdown(result), encoding="utf-8")
    print(f"report: {report}")
    print(f"applied: {result['path'] or '(not applied)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
