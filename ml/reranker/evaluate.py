"""Re-run the hand-set vs learned ablation against a database, without training.

    .venv/Scripts/python.exe ml/reranker/evaluate.py --db data/lostfound.db

Useful for checking a served artifact against a different corpus (the
sim-to-real check in the plan) without touching the model.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lostfound import rerank, store  # noqa: E402
from lostfound.matching import Matcher  # noqa: E402
from lostfound.training import build_shortlists, evaluate_reranker  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ablate the served re-ranker against the hand-set scorer")
    parser.add_argument("--db", default=None)
    parser.add_argument("--model", default=None, help="artifact path (default: settings.reranker_path)")
    args = parser.parse_args(argv)

    model = rerank.load(args.model)
    if model is None:
        print("no re-ranker artifact found; the hand-set scorer is being served")
        return 1

    con = store.connect(args.db)
    shortlists = build_shortlists(Matcher.load(con))
    result = evaluate_reranker(model, shortlists)
    base, learned = result["baseline"], result["reranked"]
    print(f"model: {model.version}  trained on {model.train_size} pairs  queries: {base['queries']}")
    print(f"{'metric':<16}{'hand-set':>10}{'learned':>10}")
    for key in ("recall@1", "recall@10", "mrr", "ndcg@10", "precision@alert", "coverage", "ece", "brier"):
        print(f"{key:<16}{base.get(key)!s:>10}{learned.get(key)!s:>10}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
