# Campus Lost & Found — multimodal matching

Report a lost or found item with a description and/or a photo. The system ranks
the most likely counterparts using **visual, textual, attribute, temporal and
location** evidence — and, critically, it can say *"no confident match"*
instead of guessing.

This repository contains an LPU map, a synthetic corpus of 449 reports,
feature-based matching with abstention, an evaluation harness, an HTTP API and
a web UI: campus map with report pins, a report form with a live attribute
preview, and a match view with confidence and reason chips.

---

## Quickstart

```bash
py -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt     # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # macOS/Linux

.venv/Scripts/python.exe -m lostfound.cli campus    # LPU mapped places
.venv/Scripts/python.exe -m lostfound.cli seed      # generate corpus, build the DB (~20 s)
.venv/Scripts/python.exe -m lostfound.cli demo      # 3 ground-truth cases end to end
.venv/Scripts/python.exe -m lostfound.cli eval      # retrieval metrics on the seeded DB
.venv/Scripts/python.exe -m lostfound.cli eval --seeds 4   # mean over 4 corpora (~15 s)
.venv/Scripts/python.exe -m lostfound.cli feedback  # staff labels -> ml/feedback/labels.csv
.venv/Scripts/python.exe -m lostfound.cli notify    # deliver the notification outbox
.venv/Scripts/python.exe -m lostfound.cli serve     # API at :8000, docs at /docs
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m scripts.phase2_load_test
```

`data/lostfound.db` is created by `seed`; `data/campus.json` by `campus`.
The Phase 2 load-test command benchmarks 200 report creations and 100 matches
against a temporary local SQLite database using 32 concurrent workers. Its
latencies are a local baseline, not a production-capacity measurement.

### Real and synthetic training data

Downloaded public datasets are kept outside the application database under
`datasets/`. Build a bounded, reproducible manifest without extracting the
multi-gigabyte archives:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_real_datasets.py
```

The manifest records ABO product images, Stanford Online Products identity
classes, Open Images bounding boxes, and the synthetic report corpus. Public
datasets provide image/category supervision; they do not provide verified
lost/found ownership pairs. The re-ranker must therefore continue to use
synthetic ground truth and staff-confirmed feedback until real pair labels are
collected. Review each dataset's license and attribution requirements before
redistributing derived data.

Evaluate the active image encoder against real product identity pairs:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_real_images.py --pairs 250
```

Same-product images are treated as positives and different-product images as
negatives. The result is written to `ml/eval/real-image-baseline.json`; this
benchmark does not train or replace the serving model. The committed run for
`hist-grid-v1` puts the same product ahead of a different one on **54.4%** of
250 pairs — the honest ceiling of a colour-and-silhouette descriptor.

The encoder registry is deliberately closed to classical encoders: no deep
model is loaded or trained (see `embedders.py`). If a new classical descriptor
is added, re-embed stored photos additively before switching:

```powershell
.\.venv\Scripts\python.exe scripts\reembed_images.py --model hist-grid-v1
```

The old image vectors remain in the database until an explicit cleanup, which
makes the encoder change reversible.

### Local Phase 2 stack

With Docker Compose installed, `docker compose up --build` starts the API,
web UI, PostgreSQL + pgvector, Redis, and the asynchronous matching worker.
The API applies Alembic migrations before serving. The Compose database
credentials are disposable local-development defaults only; do not reuse them
for a hosted database. This stack keeps photos on the local filesystem.
To test a private S3-compatible provider, configure `OBJECT_STORAGE_BACKEND=s3`,
`S3_BUCKET`, and the provider's endpoint, region, and credentials in the ignored
`.env` before starting the stack. Blank credential values use the AWS default
credential chain. Keep the bucket private.

### PostgreSQL runtime

The app uses SQLite by default. To run against PostgreSQL + pgvector, install
the optional database dependencies and configure `DATABASE_URL` in the
untracked `.env`:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[postgres,migrations]"
$env:DATABASE_URL = "postgresql://lostfound:your-password@localhost:5432/lostfound"
```

The engine resolves ordinary PostgreSQL URLs to the synchronous `psycopg`
driver. Without `DATABASE_URL`, it falls back to the configured demo SQLite
database. The application opens PostgreSQL connections when this variable is
set and checks that Alembic revision `0009` is applied before serving requests.

To migrate data, first point `DATABASE_URL` at the PostgreSQL database, then
run the schema and backfill commands from the repository root:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe scripts\backfill_sqlite_to_pg.py --source data\lostfound.db
```

The script uses `DATABASE_URL` as its target, checks for the pgvector extension,
and upserts rows by their existing primary keys. Re-running it updates those
rows instead of creating duplicates. The local Docker Compose PostgreSQL +
pgvector runtime and Alembic migration through revision `0009` have been
validated. The SQLite-to-PostgreSQL corpus backfill and production-scale
retrieval recall/performance still require separate validation.

### Redis matching worker

The demo remains synchronous by default. To precompute match results with the
Redis Streams worker, configure `REDIS_URL` and `ASYNC_MATCHING=true`, then run
the API and worker in separate terminals:

```powershell
# Terminal 1
.\.venv\Scripts\python.exe -m lostfound.cli serve

# Terminal 2
.\.venv\Scripts\python.exe -m lostfound.worker
```

New, non-moderated reports enqueue a `rematch_report` job after being saved.
The worker maintains an in-process matcher snapshot, acknowledges completed
jobs, retries failed jobs, and moves exhausted/invalid jobs to
`lostfound:jobs:dead`. The report response includes `matching_status`; the
client can poll `GET /api/reports/{id}/status`. The existing match endpoint
remains available as a synchronous fallback. In async mode, text/image vectors
are generated by the worker, and per-campus database generations invalidate
stale API and worker matcher snapshots. Multiple workers are supported by the
queue. A local Docker Compose Redis + single-worker report-to-match run has been
validated; multi-worker load and recovery testing against Redis remain
outstanding.

### Private photo storage

Development uses the local filesystem under `data/media` (or `MEDIA_DIR`) and
serves photos only through signed URLs that expire after five minutes. Set
`PHOTO_SIGNING_SECRET` to a long random value when running multiple local API
workers so they can verify each other's URLs. Existing synthetic photo paths
continue to work through this signed endpoint.

For S3-compatible private buckets (AWS S3, Cloudflare R2, MinIO), install the
optional client and configure the bucket in the untracked `.env`:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[object-storage]"
```

Set `OBJECT_STORAGE_BACKEND=s3`, `S3_BUCKET`, and `S3_ENDPOINT_URL` (when
required by the provider); set `S3_REGION` and credentials as appropriate.
Credentials may also come from the standard AWS credential provider chain.
Keep the bucket private—uploads do not set public ACLs, and new-photo URLs are
short-lived S3 presigned GET URLs. Older local `data/media` files remain served
via signed local URLs; a bulk migration of these legacy files into a bucket is
available as an explicit dry-run-first command. With the target backend
configured, inspect what would move and then apply:

```powershell
.\.venv\Scripts\python.exe scripts\migrate_local_photos.py --database data\lostfound.db
.\.venv\Scripts\python.exe scripts\migrate_local_photos.py --database data\lostfound.db --apply
```

The command retains local source files, updates database paths only after each
upload succeeds, and is safe to rerun. Missing or unsafe source paths fail
before any upload starts.

### The web UI

The UI is a single-page React app. `cli serve` serves the built bundle at
`http://127.0.0.1:8000/` if `web/dist` exists, so the one-command demo is:

```bash
cd web && npm install && npm run build && cd ..
.venv/Scripts/python.exe -m lostfound.cli seed
.venv/Scripts/python.exe -m lostfound.cli serve      # open http://127.0.0.1:8000/
```

For frontend work, run the API and Vite side by side (Vite proxies `/api` and
`/media`, no CORS involved):

```bash
.venv/Scripts/python.exe -m lostfound.cli serve      # terminal 1, port 8000
cd web && npm run dev                               # terminal 2 -> http://localhost:5173
```

`npm run build` type-checks first (`tsc --noEmit`), so it fails on type errors.

---

## LPU campus map

The web map uses OpenStreetMap tiles centered on Lovely Professional University
in Phagwara. Three named buildings are currently geocoded from OSM: Lovely
Institute of Management, Lovely Institute of Technology, and Lovely Institute
of Pharmacy. Tile and map data are attributed to OpenStreetMap contributors.
Reports from the synthetic corpus use legacy demo locations and intentionally
do not appear as pins on the real map. New reports can select one of the three
verified LPU locations; expand map coverage only as more locations are verified.

The legacy 16-place campus graph is retained solely for reproducing the seeded
matching corpus and its evaluation results. Its generated layout is not a real
representation of LPU and its distances are not used to place map pins.

## The corpus

`seed` simulates three weeks of real lost & found traffic (`lostfound/corpus.py`):

* 260 physical items → 220 lost reports, 229 found reports, **189 true pairs**
* 19 lost reports that were never recovered, 37 found items nobody claimed
* lost reports carry an **interval** ("last saw it at 11:00"), not a timestamp
* reporters state only *some* attributes: terse / detailed / vague registers
* 10% of reports contain a human error (wrong shade, wrong floor)
* 123 synthetic item photos, rendered deterministically (`lostfound/imagery.py`)

Ground truth lives in its own table and is never read by the matcher.

---

## How matching works

1. **Ingest** (`pipeline.py`) — text → canonical attributes; a photo contributes
   only the colour *when the reporter did not state one*.
2. **Measure** (`features.py`) — 14 pieces of evidence per pair: category,
   serial, text cosine, image cosine, model number, colour (CIE76 ΔE), brand,
   time gap, walk distance, **GPS distance**, marks, material, damage, rarity.
   GPS distance (`geo.py`, haversine) applies when both reports are pinned;
   otherwise the campus walk graph is the location evidence.
3. **Score** — a **weight-normalised mean over available evidence**. `None`
   means "not stated" and is masked out, so a one-line report is not punished
   for the twelve fields it never mentioned.
4. **Suppress** — a stated model number, serial, category or an impossible
   event order is an *identity assertion*, not soft evidence: mismatches
   multiply the score down (`features.py`).
5. **Decide** (`matching.py`) — `match` / `review` / `no_match`, using both an
   absolute threshold and the **margin** to the runner-up. Two near-tied
   candidates go to staff instead of auto-alerting.

Every result carries human-readable reason chips (`explain()`), e.g.
`serial / tag number matches · colour matches (black) · same building · found 3.1 h after loss`.

## The web UI

Three surfaces, one page:

* **Campus map** — OpenStreetMap tiles bounded to the LPU campus, with markers
  only for reports attached to verified geocoded locations. Select a marker to
  open its reports.
* **Feed** — cursor-paginated reports with a **Load more reports** control;
  filter the loaded results by kind, search descriptions, and click through to
  a case. The API defaults to 25 reports per page and caps each page at 100.
* **Report an item** — kind toggle, description, location picker (currently the
  three mapped LPU buildings), optional photo, and for lost items a "last had it" window.
  While you type, `POST /api/extract` shows exactly which attributes were read
  and how complete the record is. Submitting files the report (JSON, or
  multipart when a photo is attached) and immediately opens its matches.
  Both kinds of report can optionally be pinned to the browser's current
  location — for a found item that is where it was picked up, for a lost item
  it is where the owner last had it. Permission is requested only after the
  reporter selects **Use my current location** / **Add where I last had it**.
  Coordinates and reported accuracy are saved with the report, shown as a map
  pin, and become GPS distance evidence in matching. Browser geolocation
  requires HTTPS or localhost.
* **Match view** — the decision banner (`match` / `review` / `no_match`), a
  confidence meter with the configured plausible floor and alert threshold marked,
  the margin to the runner-up, and the shortlist with per-candidate reason chips,
  photo thumbnails, and an explicit identity-conflict line when a flag fired.

Cases are deep-linkable: `/?report=LR-0045` opens that case's match view, which
is what a staff desk would paste into a message.

Staff sign in before **Confirm match** / **Not this item** controls are enabled.
Confirming marks both reports `matched` (the feed shows a badge, the header
counter updates); rejecting re-opens them. Either way the verdict is stored as
a training label attributed to the authenticated account.

## Staff feedback

A match is a lead, not a handover, so the desk has the final word:

```
POST /api/feedback   {query_id, candidate_id, action: confirm|reject, note?}
GET  /api/feedback?query_id=LR-0008    labels for one case
GET  /api/stats                        confirmed / rejected / matched counters
```

Each decision is stored in the `feedback` table with the **feature vector and
probability at decision time** (`features_json`, see `matching.snapshot`). That
snapshot matters: if the corpus, weights or embedders change later, the label
still records what the model actually saw, so the learned re-ranker is not
trained on hindsight. One row per pair (`ON CONFLICT` update), so a staff member
changing their mind replaces the label instead of creating a contradiction.

`cli feedback` summarises the counts and exports `ml/feedback/labels.csv` with
`created_at, query_id, candidate_id, action, staff, note, prob, score,
features_json` — the training set for stage 2.

## Authentication and staff roles

The API uses Supabase Auth for email/password signup, login, refresh and logout.
Set `SUPABASE_URL` and `SUPABASE_ANON_KEY` in the untracked local `.env` file;
the application has no working auth endpoints until both are configured. Do
not use or expose a Supabase service-role key. The API returns Supabase access
and refresh tokens to its caller, which must keep them out of URLs and logs.

New users always receive the `student` role. The API looks up roles in its
SQLite `users` table on every authenticated request, not in client-controlled
metadata or role claims. An operator can promote a registered account using
its Supabase user ID (returned by signup or `/api/auth/me`):

```powershell
.venv\Scripts\python.exe -m lostfound.cli --db data\lostfound.db auth set-role <user-id> staff
```

`/api/feedback` and `/api/stats` require a staff, campus-admin or superadmin
account. Configure `CORS_ORIGINS` with the exact web-app origins before
deployment; the defaults are for local development and wildcards are rejected.
Production also requires HTTPS origins and `REDIS_URL` for shared rate limits.

To verify a real Redis-compatible service, point `REDIS_TEST_URL` at a test
instance (prefer a dedicated Redis database) and run:

```powershell
$env:REDIS_TEST_URL = "redis://localhost:6379/1"
.\.venv\Scripts\python.exe -m pytest -m redis_live -q
Remove-Item Env:REDIS_TEST_URL
```

The test uses a unique rate-limit key that expires automatically; the standard
test suite does not connect to Redis.

### Connect and live-test Supabase

1. Create a Supabase project. In **Project Settings → API**, copy the Project
   URL and the publishable key (or legacy anon key) into the local, ignored
   `.env` file as `SUPABASE_URL` and `SUPABASE_ANON_KEY`. Never use the
   `service_role` or secret key.
2. In **Authentication → Providers**, enable Email/password sign-in. Create a
   dedicated test user and confirm its email. The test only logs in, refreshes,
   verifies and logs out that account; it does not create or delete users.
3. In the PowerShell terminal used to run tests, set the test account values
   without committing them:

   ```powershell
   $env:SUPABASE_TEST_EMAIL = "your-confirmed-test-account@example.com"
   $env:SUPABASE_TEST_PASSWORD = "your-test-account-password"
   .venv\Scripts\python.exe -m pytest -m supabase_live -q
   ```

   The test obtains `SUPABASE_URL` and `SUPABASE_ANON_KEY` from `.env` or the
   process environment. If test-account variables are absent, it skips safely.
   The normal `pytest` suite does not contact Supabase. To run it immediately
   after populating `.env`, open a fresh terminal so the shell has the test
   account variables set.

Anonymous reports require a contact email or E.164 phone number. The contact is
kept in the private `report_owners` table and omitted from public report
responses. A report receives a one-time ownership token; only its SHA-256 hash
is stored, so retain the token shown after submission.
`POST /api/reports/{id}/claim` verifies it, but the full handover workflow
belongs to Phase 4.

## Learned scoring and confidence calibration (Phase 3)

Two optional artifacts upgrade the matcher. Neither is committed; without them
the hand-set weights and the raw sigmoid serve exactly as before.

```powershell
.venv/Scripts/python.exe -m lostfound.cli train-rerank    # model + ablation report
.venv/Scripts/python.exe -m lostfound.cli calibrate       # params + reliability report
.venv/Scripts/python.exe -m lostfound.cli eval --report   # ml/eval/report.md with CIs
```

* **Re-ranker** (`lostfound/rerank.py`) — a pairwise logistic (RankNet) scorer
  over the same evidence features. The train/holdout split is by query (item),
  and `design()` is the only place a pair becomes a model vector, shared by
  training and serving so the two cannot drift apart.
* **Calibration** (`lostfound/calibration.py`) — a monotone map from the served
  scorer's raw output to an observed frequency, reporting ECE and Brier.
* **Operating point** — a calibrated probability is on a different scale from
  the hand-set sigmoid, so the alert threshold is re-derived from a measured
  precision budget (`ALERT_PRECISION_TARGET`, default 0.90) and travels with the
  calibration version. `GET /api/config/operating-point` and
  `GET /api/model/info` report the effective thresholds, model versions and the
  last evaluation row.

Holdout on the seeded corpus (47 of 189 queries, split by item):

| Metric | Hand-set | Learned + calibrated |
|---|---|---|
| Recall@1 | 0.7447 | **0.8511** |
| Recall@10 | 0.9787 | **1.0** |
| MRR | 0.8275 | **0.9167** |
| nDCG@10 | 0.8633 | **0.938** |
| Precision@alert | 0.871 | **0.925** |
| Coverage (alert rate) | 0.6596 | 0.8511 |
| ECE | 0.3478 | **0.0274** |

The ECE column is the honest part: a ranker emits a score, not a probability, so
its raw confidence is badly miscalibrated (ECE 0.90) until the logit-scale Platt
fit turns it into one (0.027). Coverage is not comparable across the two columns
because the alert threshold is re-derived on the calibrated scale; what is
comparable is precision *at* the alert point — 0.925 at 0.85 coverage versus
0.871 at 0.66 — which is the number the desk acts on. `cli calibrate` on the
hand-set scorer alone lowers ECE from 0.35 to 0.017 with an isotonic fit.

When a re-ranker is served, run `cli train-rerank` and stop there: it fits the
logit-scale calibration and derives the operating point in one step. `cli
calibrate` refits on the probability scale and overwrites that derived
threshold, which can leave the scorer with no alerts at all.

## Current numbers

`eval --seeds 4` — mean over **4 independently generated corpora**, ~188
ground-truth queries each, because one corpus is one draw and not a
measurement:

| Metric | Mean | Spread (max−min) |
|---|---|---|
| Recall@1 | 0.804 | 0.096 |
| Recall@5 | 0.984 | 0.022 |
| Recall@10 | 0.996 | 0.011 |
| MRR | 0.884 | 0.050 |
| nDCG@10 | 0.912 | — |
| Precision @ alert | 0.876 | — |
| Coverage | 0.645 | — |
| Abstain rate | 0.355 | — |
| Lookalike beats truth | 0.098 | — |

Measured with the retrained re-ranker and its logit-scale calibration served.
Both artifacts are generated and gitignored: on a fresh checkout the hand-set
scorer serves instead and these figures revert to the hand-set column of the
re-ranker ablation table above.

Single-corpus runs are appended to `ml/eval/results.csv` as the ablation log;
the full metrics row (calibration error, bootstrap CIs, per-category, alert
curve) goes to `ml/eval/metrics.jsonl`, and `eval --report` renders
`ml/eval/report.md`.

Read this as a **baseline, not a claim**: synthetic corpus, rule-based
extractor, offline embedders. It exists so every later idea (learned
re-ranker, VLM attributes, calibration) has something honest to beat. Recall@1
of 0.67 with Recall@10 of 0.97 says the retrieval stage is doing its job and
the *ordering* is where the next gain is.

---

## Layout

```
lostfound/
  campus.py      buildings, floors, walking graph, Dijkstra
  timeutil.py    timezone-safe timestamp parsing at the API boundary
  taxonomy.py    categories, colours, brands, damages, marks, locations
  schema.py      canonical attribute schema (shared by vision + text)
  textnorm.py    free text -> attributes
  imagery.py     deterministic synthetic item photos
  corpus.py      simulated three weeks of reports + ground truth
  embedders.py   classical text/image encoders (no deep models)
  encoders.py    versioned encoder registry + stale-vector detection
  rerank.py      learned pairwise re-ranker (optional artifact)
  calibration.py Platt/isotonic calibration, ECE, Brier, operating point
                 (`train-rerank` fits it; `calibrate` is for the hand-set scorer)
  training.py    item-wise split, ablation, threshold derivation, reports
  features.py    pair measurement, scoring, explanations
  matching.py    ranking + match/review/no_match decision
  eval.py        recall@k, MRR, nDCG, precision@alert, distractor rate
  ocr.py         optional Tesseract reader: printed serials, PII redacted
  recovery.py    claims, ownership challenges, handover codes
  notify.py      transactional outbox (log or SMTP transport)
  store.py       SQLite: reports, vectors, matches, feedback, claims, outbox
  pipeline.py    corpus -> enriched reports -> embeddings -> DB
  api.py         FastAPI: campus, reports, extract, match, claims, stats, UI
  cli.py         campus / seed / stats / match / demo / eval / feedback / notify / serve
web/             React + Vite single-page UI (map, feed, form, match, claims)
data/            campus.json, lostfound.db, media/ (generated)
ml/eval/         results.csv, metrics.jsonl, report.md
ml/reranker/     train.py, evaluate.py (+ generated model.json, ignored)
ml/calibration/  report.py (+ generated params.json, ignored)
ml/feedback/     labels.csv (exported staff decisions)
tests/           190 tests
```

## Honest limitations

* **Synthetic photos.** `data/media/*.png` are rendered flat-design items, not
  photographs. They are flagged `is_synthetic_art` in the DB. Dropping real
  photos into `data/media` and pointing `reports.image_path` at them requires
  no code change.
* **Classical encoders by design.** Text is hashed character n-grams; images are
  an HSV histogram plus a spatial colour/edge grid. The deep-model adapters
  (DINOv2, bge-m3) and the `vision` extra were removed, so the project loads
  and trains no neural network. The image descriptor is the weaker half: on 250
  real product pairs it separates identities 54.4% of the time.
* **The learned re-ranker is a pairwise logistic model, not LambdaMART.**
  `lostfound/rerank.py` fits a RankNet-style scorer over the same evidence
  vector in plain numpy (LightGBM is not installed here), trained on
  ground-truth pairs plus staff labels and held out **by item**. Artifacts are
  generated, never committed: `cli train-rerank` writes `ml/reranker/model.json`
  and the calibration fitted for it. Delete the artifact to fall back to the
  hand-set weights.
* **Authentication requires a configured Supabase project.** Login, identity
  lookup, refresh and logout have passed the opt-in live test against the
  configured project; that integration check is not part of the default test
  suite. Staff roles are provisioned locally with the CLI; the SPA keeps access
  and refresh tokens in memory, refreshes after an API 401, and requires sign-in
  again after a page reload.
* **Abuse prevention is an interim layer.** Redis is used when configured;
  development falls back to a process-local limiter. Spam checks are lightweight
  heuristics rather than CAPTCHA or a trained classifier.
* **Calibration is fitted, not automatically shipped.** `lostfound/calibration.py`
  implements Platt and isotonic fits with ECE and Brier; `cli calibrate` writes
  `ml/calibration/params.json` only when holdout ECE improves. A fresh checkout
  still serves the raw sigmoid.
* **OCR is optional, and degrades to nothing.** `lostfound/ocr.py` reads printed
  serials and asset tags off a photo and merges them into `visible_text`, so the
  `serial` evidence feature can use them. It runs only when both `pytesseract`
  and the native `tesseract` binary are installed; without them ingest is
  unchanged and no text is invented. Anything read is PII-redacted (e-mail,
  phone, 12–16 digit identity numbers) before storage, and only serial-like
  tokens (mixed letters and digits) are kept.
* **Notifications are durable but locally delivered.** Every message is written
  to the `notifications` outbox in the same transaction as the state change, with
  a unique `dedupe_key` so a retry cannot double-send. With no `SMTP_HOST` set,
  delivery is the structured log — a real path for a demo, and `cli notify` (or
  the next API write) flushes whatever is pending. Set `SMTP_HOST`, `SMTP_PORT`,
  `SMTP_USERNAME`, `SMTP_PASSWORD` and `SMTP_FROM` to send real mail; there is no
  SMS/WhatsApp channel and no per-user notification preference yet.
* **Recovery is a real loop, not a QR handover.** A claim is verified against
  challenge questions drawn from the claimant's own lost report (serial, marks,
  brand/model, colour — at least two must be answered correctly, and the prompts
  never contain their answers). Answers are graded and discarded, only the
  questions asked are stored, the handover code is stored as a SHA-256 hash, and
  wrong codes are capped. Staff close the counter in the Desk tab, which marks
  both reports `recovered` and writes an audit entry. There is no QR scan, no
  photo-ID capture and no offline desk workflow.
* **Up to four photos per report.** `POST /api/reports/upload` accepts `photos`
  (a list) or the original single `photo`, re-encodes each to JPEG (max 1024 px,
  EXIF stripped) and stores them under `data/media/`. The report's image vector
  is a max-pool of the per-photo embeddings, so a detail visible in any one photo
  survives instead of being averaged away. A photo contributes a colour attribute
  only when the text did not state one — it never overrides what the reporter
  wrote. Additional photo keys travel in the report payload, not a separate
  table; a dedicated `report_photos` table is the next schema step.

## Next milestones

1. Stronger classical image descriptors — colour-layout moments and a shape
   histogram — ablated against the current HSV/grid vector on real product pairs.
2. Per-category recalibration plus a distractor-aware training split; evaluate a
   LambdaMART swap behind the current re-ranker interface.
3. `report_photos` table (one row per photo, with per-photo embeddings) instead
   of carrying extra keys in the report payload.
4. Notification channels beyond e-mail (SMS/WhatsApp) with per-user preferences,
   and a staff duty roster for the desk queue.
