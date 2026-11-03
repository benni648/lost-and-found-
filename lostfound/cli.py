"""Command line entry points.

python -m lostfound.cli campus
python -m lostfound.cli seed
python -m lostfound.cli match LR-0007
python -m lostfound.cli demo --n 3
python -m lostfound.cli eval
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

from . import store
from .campus import get_campus
from .config import DEFAULT_ITEM_COUNT, DEFAULT_SEED, EVAL_DIR, settings
from .eval import format_report_markdown, format_results, run_eval
from .matching import Matcher

GREEN, RED, YELLOW, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"


def _c(text: str, colour: str) -> str:
    return f"{colour}{text}{RESET}"


def cmd_campus(args) -> int:
    campus = get_campus()
    print(_c("Lovely Professional University", BOLD))
    print(f"  {campus.stats()}")
    print()
    print("  OpenStreetMap locations")
    for building in campus.buildings.values():
        if building.latitude is None or building.longitude is None:
            continue
        print(f"  {building.name:<36} {building.latitude:.6f}, {building.longitude:.6f}")
    print("\n  Legacy synthetic locations remain available to the demo matching corpus.")
    path = campus.save()
    print(f"\n  written to {path}")
    return 0


def cmd_seed(args) -> int:
    from .pipeline import build_database

    print(_c("Seeding demo campus corpus", BOLD))
    build_database(
        db_path=args.db,
        n_items=args.items,
        seed=args.seed,
        render_images=not args.no_images,
    )
    print(_c("\nDone.", GREEN))
    return 0


def cmd_stats(args) -> int:
    con = store.connect(args.db)
    lost = store.list_reports(con, "lost")
    found = store.list_reports(con, "found")
    print(f"  reports   : {len(lost)} lost, {len(found)} found")
    print(f"  pairs     : {len(store.truth_pairs(con))}")
    cats: dict[str, int] = {}
    for r in lost + found:
        cats[r.category or "?"] = cats.get(r.category or "?", 0) + 1
    for cat, n in sorted(cats.items(), key=lambda kv: -kv[1]):
        print(f"    {cat:<12} {n}")
    return 0


def _print_match(result: dict, truth_target: str | None = None) -> None:
    q = result["query"]
    print(_c(f"\n{result['decision'].upper()}  [{q['id']}] {q['kind']}", BOLD))
    print(f"  {q['description']}")
    print(_c(f"  {q['location']}", DIM))
    print(f"  confidence {result['confidence']:.3f}   margin {result['margin']:.3f}   {_c(result['reason'], DIM)}")
    for i, m in enumerate(result["matches"], 1):
        mark = " <-- truth" if truth_target and m["candidate_id"] == truth_target else ""
        head = f"  {i}. [{m['candidate_id']}] p={m['prob']:.3f}  {m['description'][:78]}"
        print(_c(head, GREEN) + _c(mark, YELLOW))
        print(_c(f"     {m['location']}", DIM))
        chips = [f"{c['label']}" for c in m["reasons"][:5]]
        if chips:
            print(_c("     " + " | ".join(chips), DIM))


def cmd_match(args) -> int:
    con = store.connect(args.db)
    matcher = Matcher.load(con)
    result = matcher.match(args.report_id, top_k=args.top)
    truth = store.truth_pairs(con)
    target = truth.get(args.report_id)
    if target is None:
        inv = {v: k for k, v in truth.items()}
        target = inv.get(args.report_id)
    _print_match(result, target)
    return 0


def cmd_demo(args) -> int:
    con = store.connect(args.db)
    matcher = Matcher.load(con)
    truth = store.truth_pairs(con)
    rng = random.Random(args.seed)
    ids = [lid for lid in truth if len(matcher.by_id[lid].description) < 120]
    picks = rng.sample(sorted(ids), min(args.n, len(ids)))
    for lid in picks:
        result = matcher.match(lid, top_k=3)
        _print_match(result, truth[lid])
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    from .api import create_app

    print(_c(f"API on http://{args.host}:{args.port}  (docs at /docs)", BOLD))
    # The app installs its own structured logging and logs one line per request
    # from RequestContextMiddleware. Passing log_config=None and access_log=False
    # stops uvicorn from reconfiguring the root logger or emitting a second,
    # unformatted access line for every request.
    uvicorn.run(
        create_app(args.db),
        host=args.host,
        port=args.port,
        log_config=None,
        access_log=False,
    )
    return 0


def cmd_feedback(args) -> int:
    import csv
    import json

    con = store.connect(args.db)
    items = store.list_feedback(con)
    counts = store.feedback_counts(con)
    print(_c("Staff feedback", BOLD))
    print(f"  confirmed : {counts['confirmed']}")
    print(f"  rejected  : {counts['rejected']}")
    print(f"  total     : {len(items)}")
    if not items:
        print(_c("\n  no feedback yet - confirm or reject a match in the UI", DIM))
        return 0

    out = Path(args.export)
    out.parent.mkdir(parents=True, exist_ok=True)
    fields = ["created_at", "query_id", "candidate_id", "action", "staff", "note", "prob", "score", "features_json"]
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in items:
            writer.writerow({**row, "features_json": json.dumps(json.loads(row["features_json"]))})
    print(_c(f"\n  exported {len(items)} labels to {out}", GREEN))
    return 0


def cmd_auth_set_role(args) -> int:
    con = store.connect(args.db)
    try:
        store.set_user_role(con, args.user_id, args.role, args.campus_id)
        con.commit()
    finally:
        con.close()
    print(f"  assigned {args.role} to {args.user_id}")
    return 0


def cmd_eval(args) -> int:
    if args.seeds > 1:
        from .eval import run_multi_seed

        print(_c(f"Running evaluation over {args.seeds} independently seeded corpora...", BOLD))
        out = run_multi_seed(list(range(args.seed, args.seed + args.seeds)), args.items)
        print(_c("Mean over seeds", BOLD))
        print(format_results(out["mean"]))
        print(
            _c("  spread (max-min): ", DIM)
            + "  ".join(f"{k}={v}" for k, v in out["spread"].items() if k.startswith("recall@") or k == "mrr")
        )
        return 0

    con = store.connect(args.db)
    matcher = Matcher.load(con)
    print(_c("Running retrieval evaluation...", BOLD))
    results = run_eval(matcher, save_csv=not args.no_csv)
    print(_c("Results", BOLD))
    print(format_results(results))
    if not args.no_csv:
        print(_c("\n  appended to ml/eval/results.csv and ml/eval/metrics.jsonl", DIM))
    if args.report:
        out = EVAL_DIR / "report.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(format_report_markdown(results), encoding="utf-8")
        print(_c(f"  report written to {out}", GREEN))
    return 0


def cmd_calibrate(args) -> int:
    from .training import calibrate_scorer, calibration_report_markdown

    con = store.connect(args.db)
    matcher = Matcher.load(con)
    print(_c("Fitting confidence calibration (fit/holdout split by query)...", BOLD))
    result = calibrate_scorer(
        matcher,
        holdout=args.holdout,
        seed=args.seed,
        method=args.method,
        save=not args.dry_run,
        force=args.force,
    )
    print(f"  scorer        : {result['scorer']}")
    print(f"  method        : {result['params'].method}")
    print(f"  ECE           : {result['raw']['ece']} -> {result['calibrated']['ece']}")
    print(f"  Brier         : {result['raw']['brier']} -> {result['calibrated']['brier']}")
    print(f"  rows          : {result['fit_rows']} fit / {result['holdout_rows']} holdout")
    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(calibration_report_markdown(result), encoding="utf-8")
    print(_c(f"  report        : {out}", DIM))
    if result["path"]:
        print(_c(f"  applied       : {result['path']}", GREEN))
    elif not result["improved_ece"]:
        print(_c("  not applied   : holdout ECE did not improve (use --force to override)", YELLOW))
    elif args.dry_run:
        print(_c("  dry run       : nothing written", YELLOW))
    return 0


def cmd_train_rerank(args) -> int:
    from .training import reranker_report_markdown, train_reranker

    con = store.connect(args.db)
    matcher = Matcher.load(con)
    print(_c("Training the learned re-ranker (holdout split by query)...", BOLD))
    result = train_reranker(
        matcher,
        holdout=args.holdout,
        seed=args.seed,
        epochs=args.epochs,
        save=not args.dry_run,
    )
    base, learned = result["baseline"], result["reranked"]
    calibrated = result["reranked_calibrated"]
    print(f"  queries       : {result['train_queries']} train / {result['holdout_queries']} holdout")
    print(f"  {'metric':<16}{'hand-set':>10}{'learned':>10}{'calibrated':>12}")
    for key in ("recall@1", "recall@10", "mrr", "ndcg@10", "precision@alert", "coverage", "ece"):
        print(f"  {key:<16}{base.get(key):>10}{learned.get(key):>10}{calibrated.get(key):>12}")
    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(reranker_report_markdown(result), encoding="utf-8")
    print(_c(f"  report        : {out}", DIM))
    if result["path"]:
        print(_c(f"  artifact      : {result['path']}", GREEN))
    if result["calibration_path"]:
        print(_c(f"  calibration   : {result['calibration_path']}", GREEN))
    return 0


def cmd_notify(args) -> int:
    from .notify import dispatch, resolve_transport

    con = store.connect(args.db)
    try:
        before = store.notification_counts(con)
        print(_c("Notification outbox", BOLD))
        for status, count in before.items():
            print(f"  {status:<10} {count}")
        dispatch(con, transport=resolve_transport(), limit=args.limit)
        after = store.notification_counts(con)
        print(_c(f"  transport  {resolve_transport().name}", DIM))
        for status, count in after.items():
            print(f"  {status:<10} {count}")
        print(_c("\nDelivery is durable: a failed message stays pending for the next run.", DIM))
    finally:
        con.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lostfound", description="Campus lost & found matching")
    p.add_argument("--db", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("campus", help="show the demo campus and its distances")
    c.set_defaults(func=cmd_campus)

    s = sub.add_parser("seed", help="generate the demo corpus and build the database")
    s.add_argument("--items", type=int, default=DEFAULT_ITEM_COUNT)
    s.add_argument("--seed", type=int, default=DEFAULT_SEED)
    s.add_argument("--no-images", action="store_true")
    s.set_defaults(func=cmd_seed)

    st = sub.add_parser("stats", help="summarise the stored corpus")
    st.set_defaults(func=cmd_stats)

    m = sub.add_parser("match", help="match one report id")
    m.add_argument("report_id")
    m.add_argument("--top", type=int, default=5)
    m.set_defaults(func=cmd_match)

    d = sub.add_parser("demo", help="run a few ground-truth cases end to end")
    d.add_argument("--n", type=int, default=3)
    d.add_argument("--seed", type=int, default=7)
    d.set_defaults(func=cmd_demo)

    e = sub.add_parser("eval", help="evaluate retrieval on the ground-truth pairs")
    e.add_argument("--no-csv", action="store_true")
    e.add_argument("--seeds", type=int, default=1, help="average over N seeded corpora")
    e.add_argument("--seed", type=int, default=101, help="first seed when --seeds > 1")
    e.add_argument("--items", type=int, default=260, help="items per corpus when --seeds > 1")
    e.add_argument("--report", action="store_true", help="also write ml/eval/report.md")
    e.set_defaults(func=cmd_eval)

    cal = sub.add_parser("calibrate", help="fit a confidence calibration on a query-wise holdout")
    cal.add_argument("--holdout", type=float, default=0.25)
    cal.add_argument("--seed", type=int, default=0)
    cal.add_argument("--method", choices=["auto", "platt", "isotonic"], default="auto")
    cal.add_argument("--report", default="ml/calibration/report.md")
    cal.add_argument("--dry-run", action="store_true", help="measure without writing params.json")
    cal.add_argument("--force", action="store_true", help="apply even if holdout ECE does not improve")
    cal.set_defaults(func=cmd_calibrate)

    rr = sub.add_parser("train-rerank", help="train the learned re-ranker and print its ablation")
    rr.add_argument("--holdout", type=float, default=0.25)
    rr.add_argument("--seed", type=int, default=0)
    rr.add_argument("--epochs", type=int, default=240)
    rr.add_argument("--report", default="ml/reranker/report.md")
    rr.add_argument("--dry-run", action="store_true", help="measure without writing model.json")
    rr.set_defaults(func=cmd_train_rerank)

    fb = sub.add_parser("feedback", help="summarise and export staff confirm/reject labels")
    fb.add_argument("--export", default="ml/feedback/labels.csv")
    fb.set_defaults(func=cmd_feedback)

    auth = sub.add_parser("auth", help="manage application authorization roles")
    auth_sub = auth.add_subparsers(dest="auth_cmd", required=True)
    role = auth_sub.add_parser("set-role", help="assign a role to a Supabase user id")
    role.add_argument("user_id")
    role.add_argument("role", choices=["student", "staff", "campus_admin", "superadmin"])
    role.add_argument("--campus-id")
    role.set_defaults(func=cmd_auth_set_role)

    nt = sub.add_parser("notify", help="deliver the notification outbox")
    nt.add_argument("--limit", type=int, default=50)
    nt.set_defaults(func=cmd_notify)

    sv = sub.add_parser("serve", help="start the HTTP API")
    sv.add_argument("--host", default=settings.api_host)
    sv.add_argument("--port", type=int, default=settings.api_port)
    sv.set_defaults(func=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
