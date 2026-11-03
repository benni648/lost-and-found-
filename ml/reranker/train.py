"""Train the learned re-ranker and print its ablation table.

    .venv/Scripts/python.exe ml/reranker/train.py --db data/lostfound.db

The split is by query id (item), never by pair, so the holdout number is not
leakage. The artifact lands in ``ml/reranker/model.json``; deleting it restores
the hand-set scorer, which is what makes the fallback testable.
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
from lostfound.training import reranker_report_markdown, train_reranker  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the pairwise learned re-ranker")
    parser.add_argument("--db", default=None)
    parser.add_argument("--holdout", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--epochs", type=int, default=240)
    parser.add_argument("--out", default=None, help="artifact path (default: ml/reranker/model.json)")
    parser.add_argument("--dry-run", action="store_true", help="measure without writing the artifact")
    parser.add_argument("--report", default="ml/reranker/report.md")
    args = parser.parse_args(argv)

    con = store.connect(args.db)
    result = train_reranker(
        Matcher.load(con),
        holdout=args.holdout,
        seed=args.seed,
        epochs=args.epochs,
        out=args.out,
        save=not args.dry_run,
    )
    base, learned = result["baseline"], result["reranked"]
    print(f"queries: {result['train_queries']} train / {result['holdout_queries']} holdout")
    print(f"{'metric':<16}{'hand-set':>10}{'learned':>10}")
    for key in ("recall@1", "recall@10", "mrr", "ndcg@10", "precision@alert", "coverage", "ece"):
        print(f"{key:<16}{base.get(key)!s:>10}{learned.get(key)!s:>10}")
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(reranker_report_markdown(result), encoding="utf-8")
    print(f"report: {report}")
    print(f"artifact: {result['path'] or '(dry run, not written)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
