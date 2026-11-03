# Intelligent Lost & Found Object Matching System
## Phase-wise Development Plan

**Project:** Campus Lost & Found — multimodal matching
**Document owner:** (you)
**Status:** Plan v1.0
**Last updated:** 27 October 2026
**Builds on:** the existing repository (`lostfound/` FastAPI backend, `web/` React + Vite + Leaflet SPA, 33 backend tests, 449-report synthetic corpus)

> **Descope — implementation decision.** Deep encoders are out of scope. The
> DINOv2 / bge-m3 adapters and the `vision` extra were removed; the system serves
> classical encoders only (`hash-char-ngram-v1`, `hist-grid-v1`). Everywhere
> this plan names DINOv2, bge-m3, ONNX model compression or `docs/EMBEDDERS.md`
> as future work, read it as superseded. `README.md` and `docs/ALIGNMENT.md`
> describe what actually ships.

---

## 0. How to read this plan

- **Phase 0 → Phase 11** run in order, but Phases 7 (UI) and 9 (observability) can start partway through earlier phases once their dependency (a stable API contract) exists. Parallelisation is called out per phase.
- Every phase has the same shape: **Goal → Why it aligns with the title → Tasks → Deliverables → Definition of Done → Tests → Topics covered.**
- Every task is written as a concrete build instruction: what to add, what to change, which file it lands in.
- **All 26 items** from the initial review are assigned to a phase. The traceability matrix in §13 proves none were dropped.
- Notation: `NEW` = create; `EDIT` = modify existing; `DELETE` = remove/replace.

---

## 1. Alignment check against the given title

The title is the specification. Every clause is mapped to a workstream and a phase. If a clause has no owner in this plan, that is a bug in the plan.

| Clause in the title | What it demands | Owned by | Phase |
|---|---|---|---|
| **"Intelligent"** | Real ML, not hand-tuned constants; calibrated confidence; explainability | Learned re-ranker, calibration engine, reason-chip engine | 3 |
| **"Lost & Found"** | A closed recovery loop: report → match → claim → handover | Accounts, ownership challenge, QR handover, desk tools | 4, 10 |
| **"Object"** | A real object taxonomy, per-category behaviour, attribute schema | Taxonomy expansion, per-category models, OCR | 3 |
| **"Matching System"** | Ranking + a principled abstain/alert decision | Retrieval stage, margin/threshold logic, single source of truth for thresholds | 1, 3 |
| **"multimodal machine learning"** | Multiple evidence channels fused, not concatenated ad hoc | Evidence fusion layer + learned re-ranker over features | 3 |
| **"assist students and staff"** | Two distinct roles with different powers | Auth + RBAC (student / staff / campus-admin) | 1, 4 |
| **"report lost or found objects"** | Public, mobile-first, low-friction reporting | PWA, offline drafts, voice input, i18n | 6, 7 |
| **"using descriptions and/or photographs"** | Text-only, photo-only, and both must all work | Ingest pipeline, multi-image, privacy blur, EXIF stripping | 3, 8 |
| **"computer vision and natural language processing"** | Real CV + NLP, evaluated | Classical image descriptors, rule-based extraction, OCR | 3 |
| **"identify similarities between lost and found reports"** | Cross-report similarity, not classification | Retrieval + re-ranker + eval harness | 3 |
| **"rank the most likely matches"** | Ordered output with confidence | Ranking API contract + ranked shortlist UI | 3, 7 |
| **"using visual, textual, temporal and location information"** | Four named evidence families | Feature layer: image, text, time-interval, campus-distance + geo | 3 |

**Alignment verdict:** the existing code covers *identification* well (features, scoring, abstention, explanations) but is **thin on "Intelligent" (no learning, no calibration), thin on "assist students and staff" (no accounts/roles), and absent on "anywhere in India" (single SQLite file, single campus, no deployment)**. Those three are the spine of this plan.

---

## 2. Assumptions and one honest reality check

### Assumptions
1. You want a **real, deployable product** (India-wide, multi-campus), not only a submission demo. Where a choice would be "good for a demo, bad in production", this plan picks production and notes the shortcut.
2. **Mobile-first web / PWA.** No native app in this plan (it multiplies cost 3× for a campus tool). Revisit only if push notifications become critical.
3. **Multi-tenant from the design.** One deployment, many campuses; every report, matcher pool, and notification is scoped by `campus_id`.
4. Timeline assumes **1–2 developers** part-time. Week ranges are elapsed calendar weeks from 27 Oct 2026.
5. Budget is near-zero at first; every paid service below has a free tier, and the plan explicitly states when you outgrow it.

### The "perfect accuracy" reality check (read this before Phase 3)
No matching system will be perfect, and a plan that promises it will fail review. What a credible system promises is:

- **High recall** — the correct counterpart is almost always *in* the shortlist: target **Recall@10 ≥ 0.97** (already met), **Recall@5 ≥ 0.95**.
- **High precision at the alert point** — when the system phones the owner, it is right: target **Precision@alert ≥ 0.90–0.95**.
- **Honest abstention** — when it is unsure it says "needs a human", never guesses. Measured by abstain rate and a **calibration error (ECE) < 0.05**.
- **No confident wrong handovers** — enforced by margin + identity-assertion suppression + human confirmation.

So the goal in this plan is **"trustworthy accuracy"**: the system is right when it claims to be sure, and silent when it isn't. That is what the existing abstention design already gets right, and Phases 3/8/11 extend it with calibration, human-in-the-loop, and per-category error budgets. Accuracy claims in the final report must be stated with the corpus, seed count, and spread — never a single number.

---

## 3. Target architecture (north star)

```
                        ┌─────────────────────────────────────────────┐
  Reporter (mobile)     │  CDN / Edge (static SPA + media cache)      │
  Staff desk (desktop)  │  PWA, offline drafts, i18n (en/hi/pa)       │
                        └───────────────┬─────────────────────────────┘
                                        │ HTTPS
                        ┌───────────────▼─────────────────────────────┐
                        │  API layer (FastAPI, stateless, autoscaled) │
                        │  auth/roles · rate limit · validation ·     │
                        │  security headers · request IDs · metrics   │
                        └───┬─────────────┬──────────────┬────────────┘
                            │             │              │
        ┌───────────────────▼──┐  ┌───────▼────────┐  ┌──▼───────────────┐
        │ Postgres (pgvector)  │  │ Object storage │  │ Queue / worker   │
        │ reports, feedback,   │  │ (photos, CDN)  │  │ embeddings,      │
        │ vectors, audit,      │  │ private bucket │  │ notifications,   │
        │ campuses, users      │  │ + signed URLs  │  │ re-ranker jobs   │
        └──────────────────────┘  └────────────────┘  └──┬───────────────┘
                            │                            │
        ┌───────────────────▼────────────┐   ┌───────────▼───────────────┐
        │ Matching service               │   │ Notification service      │
        │ embed → ANN retrieve → fuse →  │   │ email + WhatsApp/SMS +    │
        │ re-rank → calibrate → decide   │   │ in-app, preferences-aware │
        └────────────────────────────────┘   └───────────────────────────┘
                            │
        ┌───────────────────▼────────────────────────────────────────────┐
        │ Observability: structured logs · metrics · traces · errors ·   │
        │ funnel analytics · ML eval dashboards (recall, ECE, drift)     │
        └────────────────────────────────────────────────────────────────┘
```

**Key architectural moves vs. today**
1. SQLite single file → **Postgres + pgvector** (managed), with migrations.
2. In-process `Matcher.load()` on every write → **ANN retrieval + a bounded cache / worker refresh**.
3. Synchronous everything → **queue for embeddings, notifications, re-ranker training**.
4. Local `data/media` → **private object storage + signed URLs + CDN**.
5. Single campus → **`campuses` table, campus-scoped pools, campus-admin role**.
6. Hand-set weights only → **learned re-ranker + calibration, with the hand-set weights kept as the baseline to beat.**

---

## 4. Technology decisions (grounded, with honest caveats)

These are recommendations to validate at Phase 0/2 kickoff, not contracts. Where a known-good option is listed, prefer it over building from scratch.

| Need | Recommended | Why / caveat |
|---|---|---|
| Managed Postgres + pgvector | **Neon** (serverless Postgres, strong DX, free tier) or **Tiger Cloud / Timescale** (managed Postgres with vector + time-series, free plan) | Both support `pgvector`. **Verify the India (Mumbai) region is available for your account before committing** — region availability changes and must be re-checked at provisioning time, not assumed. **Aiven** is the fallback if you need multi-cloud regions. |
| App hosting (API) | **Google Cloud Run** (asia-south1 Mumbai, scale-to-zero) or **AWS ECS/Fargate** (ap-south-1 Mumbai) or **Fly.io** (has a Mumbai region) or **Render/DigitalOcean App Platform** (simpler, EU/US regions) | Cloud Run / AWS give you an India region out of the box, which matters for latency and data residency. Render/DO are easier but may not have an India region — check. |
| Static SPA + media CDN | **Cloudflare** (Workers/Pages + R2 for egress-free media) or S3 + CloudFront (Mumbai) | R2 avoids egress fees — material when serving item photos at scale. |
| Object storage (originals) | **Cloudflare R2** or **AWS S3 (ap-south-1)** | Private bucket + signed URLs. Never serve originals straight from the API. |
| Notifications (email + WhatsApp/SMS) | **Knock** as the orchestration layer, with an email provider (Postmark/SendGrid) and a WhatsApp/SMS provider (Twilio) connected inside it | Knock gives you one API, per-user preferences, and delivery logs across channels on a free developer tier. It is an *orchestrator*, not a delivery provider — you still connect and pay the downstream email/SMS/WhatsApp provider. |
| Error tracking + tracing | **Sentry** (backend + frontend, free tier) — leader for app error tracking | Search found no in-catalog APM recommendation, so Sentry is the pragmatic pick; **SigNoz** or **Grafana Cloud** if you want OpenTelemetry-native metrics/logs/traces and self-hosting. |
| Metrics dashboards | **Grafana Cloud** (Prometheus + Loki) | Free tier is generous. |
| Uptime + synthetic checks | **Better Stack** (uptime + logs + status page) or **UptimeRobot** (free) + **Checkly** (Playwright synthetic checks as code) | Checkly pairs naturally with your existing Playwright-based E2E tests. |
| Auth | Managed auth provider (Supabase Auth / Clerk / Auth0) or first-party JWT + refresh in `lostfound/auth.py` | Decide in Phase 1. Prefer managed unless you have a reason to own the credential store. |
| Real embedders | **DINOv2 ViT-S/14** (image), **bge-m3** (text), a small OCR (PaddleOCR/Tesseract) for serials/ID cards | Adapters already exist in `lostfound/embedders.py`; this is a config flip plus evaluation. |

**Do not** pick all of these on day one. Phase 0 uses none of the paid tiers; they enter in Phases 2 onward.

---

## 5. Phase 0 — Foundation, Tooling & Engineering Discipline
**Duration:** Weeks 1–2 · **Parallel-safe:** yes (nothing else can start cleanly without it)

### Goal
Turn a runnable prototype into a project that can be changed safely by more than one person.

### Why it aligns with the title
"System" implies buildable, testable, repeatable. CI + linting + config + packaging is what makes every later phase (especially the ML ones) measurable.

### Tasks

**A. Repository & packaging**
- `NEW` `pyproject.toml` — package `lostfound`, pin deps (replace bare `requirements.txt` as the source of truth; keep a generated `requirements.lock`), declare optional extras `[vision]`, `[rerank]`, `[dev]`.
- `EDIT` `requirements.txt` → install from the extras so `pip install -e ".[dev]"` is the one command.
- `NEW` `.editorconfig`, `.gitattributes` (force LF, mark `*.png` binary).
- `NEW` `.env.example` (no secrets) documenting every variable from Phase 0 onward.

**B. Quality tooling**
- `NEW` `ruff.toml` (or `[tool.ruff]` in `pyproject.toml`) — lint + format; enable `E,F,I,UP,B,S` (S = bandit security rules).
- `NEW` `mypy.ini` — start lenient, ratchet per module; add `lostfound/` to CI typed paths.
- `NEW` `web/.eslintrc.cjs` (or flat config) + `web/.prettierrc` — extend `react`, `react-hooks`, `jsx-a11y` (the a11y plugin pays off in Phase 7).
- `EDIT` `web/package.json` — add `lint`, `format`, `test`, `test:e2e`, `typecheck` scripts.
- `NEW` `.pre-commit-config.yaml` — ruff, ruff-format, mypy, eslint, prettier, end-of-file-fixer, detect-secrets.

**C. CI**
- `NEW` `.github/workflows/ci.yml`:
  - Job `backend`: install, `ruff check`, `ruff format --check`, `mypy lostfound`, `pytest -q`.
  - Job `frontend`: `npm ci`, `npm run lint`, `tsc --noEmit`, `npm run build`.
  - Job `security`: `pip-audit`, `npm audit --production`, `semgrep --config auto`.
  - Cache pip + npm. Fail fast. Required status checks on `main`.
- `NEW` `.github/workflows/nightly.yml` — runs `cli eval --seeds 4` nightly and commits the row to `ml/eval/results.csv`, so accuracy regressions are visible as a diff.

**D. Containers & local dev**
- `NEW` `Dockerfile` (multi-stage: build web → copy into a slim Python image) and `Dockerfile.dev`.
- `NEW` `docker-compose.yml` — api + postgres(pgvector) + redis + worker, so Phase 2's topology can be developed locally from day one.
- `NEW` `Makefile` (or `justfile`) — `make dev`, `make test`, `make seed`, `make eval`, `make serve`, `make build` as the documented entry points.

**E. Configuration (topic #9 — 12-factor config)**
- `NEW` `lostfound/settings.py` — a `Settings` object (pydantic-settings) reading from env: `DATABASE_URL`, `MEDIA_DIR`/`S3_*`, `TEXT_EMBEDDER`, `IMAGE_EMBEDDER`, `PROB_ALERT`, `MARGIN_MIN`, `PLAUSIBLE_FLOOR`, `ENV`, `CAMPUS_DEFAULT`, secret keys, feature flags.
- `EDIT` `lostfound/config.py` → keep only import-path constants (ROOT, DATA_DIR) and re-export from `settings`. Remove hardcoded `SIM_NOW`/paths from runtime code paths (keep them for the corpus generator only).
- Rule to enforce in review: **no module reads `os.environ` directly except `settings.py`.**

**F. Structured logging skeleton (topic #8a — setup)**
- `NEW` `lostfound/logging.py` — JSON formatter, request-ID context var, `log = get_logger(__name__)`.
- `EDIT` `lostfound/api.py` — add request-ID middleware and log one line per request (method, path, status, duration_ms, request_id).
- Do **not** build dashboards yet; that is Phase 9.

**G. Database migrations groundwork (prep for Phase 2)**
- `NEW` `alembic.ini` + `migrations/` scaffolding, with an initial revision that mirrors the current `store.py` DDL.
- `NEW` `scripts/backfill_sqlite_to_pg.py` stub (implemented in Phase 2).

**H. Documentation**
- `NEW` `CONTRIBUTING.md` — branch naming, commit style, how to run tests, how to add a migration.
- `NEW` `docs/ARCHITECTURE.md` — the diagram in §3, plus a decision log (ADR-lite) started with entries for Postgres, pgvector, and the re-ranker choice.

### Deliverables
Green CI on `main`; `pip install -e ".[dev]"` + `make dev` brings up the stack; `docker compose up` serves API + SPA locally; `settings.py` is the only config reader.

### Definition of Done
- [ ] CI runs on every PR and blocks merge on red.
- [ ] `ruff`, `mypy`, `eslint`, `prettier` all clean on the existing codebase.
- [ ] `docker compose up` gives a working app with **no** SQLite file required for the new path (SQLite still supported via `DATABASE_URL=sqlite://…` for the demo).
- [ ] `.env.example` documents every variable; repo commits contain no secrets (detect-secrets in pre-commit).
- [ ] Nightly eval job has produced at least one `ml/eval/results.csv` row.

### Tests required
- [ ] CI itself is the test (a deliberately broken import must fail the build).
- [ ] A settings test asserting env overrides win over defaults, and a missing required var fails loudly at startup.

**Topics covered:** #7 (CI/containers/packaging), #9 (12-factor config), #8a (logging setup).

---

## 6. Phase 1 — Security, Trust & Correctness
**Duration:** Weeks 3–4 · **Depends on:** Phase 0

### Goal
Make the system safe to expose to real students. Today **anyone on the internet can confirm or reject a match and can mutate reports with no identity at all.**

### Why it aligns with the title
"assist students **and staff**" requires knowing *who* is acting. "Matching System" requires that its decisions can be trusted, which requires that only staff can ratify them.

### Tasks

**A. Authentication & roles (topic #1)**
- `NEW` `lostfound/auth.py` — issue/verify tokens (JWT access + refresh, or a managed provider's SDK), password hashing (argon2), session model.
- `NEW` `lostfound/deps.py` — FastAPI dependencies: `current_user()`, `require_role("staff")`, `require_role("campus_admin")`.
- `NEW` tables (initial migration): `users(id, email, phone_e164, role, campus_id, password_hash, created_at)`, `sessions`, `refresh_tokens`.
- `EDIT` `lostfound/api.py`:
  - `POST /api/feedback` → `Depends(require_role("staff"))`.
  - `GET /api/feedback`, `GET /api/stats` (full corpus counters) → staff/campus-admin.
  - `POST /api/reports` → allow anonymous **but** require an email **or** phone (so the user can be notified in Phase 5) and an ownership token.
  - `POST /api/reports/{id}/claim` (new) → the ownership challenge stub (completed in Phase 4).
- `NEW` `POST /api/auth/register`, `/api/auth/login`, `/api/auth/refresh`, `/api/auth/logout`.
- **Never** put the role in the token without re-checking it server-side on privileged actions.
- Roles: `student` (report, view own, claim), `staff` (desk: confirm/reject, see all in campus), `campus_admin` (manage campus, see analytics), `superadmin` (manage campuses).

**B. Lock down CORS (topic #1)**
- `EDIT` `lostfound/api.py` — replace `allow_origins=["*"]` with `settings.cors_origins` (explicit list: `http://localhost:5173`, the production domain(s)). `allow_credentials=True` only with explicit origins, never `*`.

**C. Rate limiting & abuse controls (topic #2)**
- `NEW` `lostfound/ratelimit.py` — Redis-backed sliding-window limiter (slowapi or a small custom dependency).
  - `POST /api/reports` and `/api/reports/upload`: e.g. 10/hour per IP+session.
  - `POST /api/extract`: 60/min per IP (it is called on every keystroke debounce — it is the cheapest DoS surface today).
  - `POST /api/match/{id}`: 30/min per session.
  - Auth endpoints: 10/min per IP, with lockout after N failures.
- `NEW` `lostfound/moderation.py` — honeypot field + minimum-time-to-submit check + a simple content classifier/heuristics (reject links-only spam); route suspicious reports to a `pending_review` status.
- Optional: CAPTCHA (hCaptcha/Turnstile) behind a feature flag for the public report form.

**D. HTTP security headers (topic #4)**
- `NEW` `lostfound/middleware.py` — add `SecurityHeadersMiddleware`:
  - `Content-Security-Policy` (start with `default-src 'self'; img-src 'self' data: https://*.tile.openstreetmap.org; connect-src 'self'`; tighten until Leaflet tiles are the only external dependency).
  - `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`.
  - `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy: geolocation=(self)`.
  - `X-Frame-Options: DENY` (or `frame-ancestors 'none'` in the CSP).
- `EDIT` `lostfound/api.py` — register middleware; disable Swagger/ReDoc in production (`docs_url=None` when `settings.env == "prod"`).

**E. Upload & image hardening (topic #5)**
- `EDIT` `lostfound/api.py` `create_report_with_photo` — stream the body with a hard byte cap (reject at the limit instead of reading 8 MB into memory first); validate magic bytes, not just `content_type`.
- `EDIT` `lostfound/pipeline.py` `save_report_photo` — set `Image.MAX_IMAGE_PIXELS` (e.g. 40 MP) to block decompression bombs; bound output dimensions; keep EXIF strip and re-encode (already correct).
- `EDIT` — enforce per-report photo count (Phase 3 raises it to 4; Phase 1 enforces the current 1).

**F. Single source of truth for thresholds (topic #6)**
- `EDIT` `lostfound/config.py` → `settings`: `PLAUSIBLE_FLOOR`, `PROB_ALERT`, `MARGIN_MIN`, and the UI verdict bands (`STRONG=0.75`, `PROBABLE=0.55`, `WEAK=0.30`).
- `EDIT` `lostfound/matching.py` — `decide()` reads `settings.PLAUSIBLE_FLOOR` / `PROB_ALERT` / `MARGIN_MIN`; delete the hardcoded literals.
- `EDIT` `lostfound/api.py` — add `GET /api/config/operating-point` returning thresholds + bands + model names.
- `EDIT` `web/src/api.ts` — add `operatingPoint()`.
- `EDIT` `web/src/components/MatchPanel.tsx` and `web/src/lib/format.ts` — consume the fetched operating point instead of `0.55/0.3/0.75` literals. Delete `verdictFor`'s hardcoded constants.
- Add a test that the API value and the UI bands agree on a documented fixture.

**G. Concurrency & correctness (topic #3)**
- **Interim fix (until Phase 2 moves to Postgres):** stop sharing one `sqlite3` connection across threads. Give each request its own connection (`Depends(get_conn)`) or serialise writes behind a single-writer queue; keep WAL. Remove `check_same_thread=False` plus the shared `state["con"]` pattern in `api.py`.
- `NEW` `tests/test_concurrency.py` — fire N concurrent `POST /api/reports` and assert no duplicates, no `database is locked` errors, and correct `next_id` sequencing.
- **Real fix lands in Phase 2** (Postgres + connection pool); document the interim limitation in `docs/ARCHITECTURE.md`.

**H. Data-integrity guards**
- `EDIT` `lostfound/store.py` `next_id()` — it is read-then-write and racy; replace with a Postgres sequence in Phase 2, and for SQLite use a transaction with `BEGIN IMMEDIATE`.

### Deliverables
The API is safe to expose publicly: authenticated staff actions, locked CORS, security headers, rate limits, hardened uploads, and one authoritative set of thresholds.

### Definition of Done
- [ ] Confirm/reject without a staff token returns 401/403.
- [ ] A cross-origin `fetch` from an unlisted origin fails CORS.
- [ ] Security headers present on every response (asserted by a test).
- [ ] `POST /api/extract` returns 429 past the limit.
- [ ] A 9 MB photo and a decompression-bomb PNG are both rejected before full decode.
- [ ] Changing `PROB_ALERT` in env changes both the API decision and the UI band, with no code edit.
- [ ] Concurrency test passes with 50 parallel report creations.

### Tests required
- [ ] Auth: register/login/refresh/logout; expired token rejected; role escalation blocked.
- [ ] Rate limit: 429 after N requests, per key.
- [ ] Upload: oversized, wrong-magic-bytes, bomb image, non-image.
- [ ] Headers: parameterised test over all routes.
- [ ] Threshold agreement: API ↔ UI fixture.

**Topics covered:** #1, #2, #3 (interim), #4, #5, #6.

---

## 7. Phase 2 — Data Platform & Scale
**Duration:** Weeks 5–7 · **Depends on:** Phases 0–1

### Goal
Move off a single SQLite file onto a real, migratable, concurrent data platform with vector search, and remove the O(corpus)-per-write behaviour.

### Why it aligns with the title
"anywhere in India" and "rank the most likely matches" at national scale both require a database that survives concurrency and can search vectors without scoring every pair in Python.

### Tasks

**A. Postgres + pgvector (topics #10, #12)**
- `NEW` `lostfound/db.py` — SQLAlchemy 2.0 engine + `asyncpg`/`psycopg` pool; `DATABASE_URL`-driven; a `sqlite://` fallback so the demo still runs dependency-free.
- [x] SQLAlchemy engine factory resolves the SQLite fallback and synchronous PostgreSQL/psycopg URLs; the runtime repository honors `DATABASE_URL` for PostgreSQL and keeps SQLite as the default. The local Compose PostgreSQL runtime has been live-validated.
- `NEW` migrations:
  - `reports` gains `campus_id`, `owner_user_id`, `location_point geography(Point)` (PostGIS) or lat/lon + index, `deleted_at`, `pending_review`.
  - `vectors` → `report_embeddings(report_id, kind, model, dim, embedding vector(N))` with an **HNSW** index (`vector_cosine_ops`).
  - `campuses(id, name, short, domain, lat, lon, timezone, status)`.
  - `audit_log(id, actor_id, action, entity, entity_id, before, after, at, ip, request_id)`.
  - `feedback` gains `campus_id`, `model_version`, `operating_point_version`.
- [x] Additive revision `0005` adds the ownership/campus/deletion and feedback fields, audit/campus/embedding tables, and model-specific pgvector HNSW indexes; SQLite upgrade/downgrade and legacy `pending_review` preservation are tested. The Compose PostgreSQL database is migrated through revision `0009`.
- `EDIT` `lostfound/store.py` — rewrite as the SQLAlchemy repository layer; keep the same function names so callers barely change. Delete the ad-hoc `ALTER TABLE` block in `connect()`.
- [x] Runtime database selection honors `DATABASE_URL`, uses a pooled SQLAlchemy/psycopg connection adapter for PostgreSQL, keeps SQLite demo support, stores vectors in pgvector, and requires Alembic revision 0009 on startup. Live API health, report persistence, ANN matching, and match-row persistence passed against local Compose PostgreSQL. The repository still uses compatibility SQL rather than a full SQLAlchemy ORM/query layer.
- `NEW` `scripts/backfill_sqlite_to_pg.py` — migrate a local `data/lostfound.db` into Postgres (reports, attrs, vectors, ground_truth, feedback, matches). Idempotent.
- [x] Backfill implementation upserts users, campuses, ownership, report data, embeddings, ground truth, matches, feedback, and audit records in batches; PostgreSQL execution and corpus round-trip validation remain outstanding.
- **Retrieval (topic #12):** replace "score every found report against the query" with:
  1. ANN prefilter in pgvector: top ~200 candidates by fused vector similarity (text + image + category).
  2. Score only those 200 with `features.score_pair`.
  3. Re-rank, then decide.
  This turns ranking from O(N) per query into O(k) after an indexed ANN lookup, and is the single biggest scalability win available.
- [x] Added model/dimension-specific pgvector HNSW queries for top-200 text/image shortlists before feature scoring, plus input validation and a Recall@k comparison helper. The API selects this path when configured with PostgreSQL; SQLite keeps exhaustive scoring. Live ANN matching passed against Compose PostgreSQL; query-plan measurement and corpus recall comparison remain outstanding.

**B. Incremental matcher, no full rebuild (topic #12)**
- `EDIT` `lostfound/api.py` — remove `state["matcher"] = Matcher.load(con)` from `file_report` and `record_feedback`.
- `EDIT` `lostfound/matching.py` — add `Matcher.upsert(report)` and `Matcher.remove(id)`: update `by_id`, the relevant pool, `MatchContext` counters, and the vector maps incrementally. Keep a nightly/on-demand `Matcher.load()` full rebuild as a safety net (and after bulk imports).
- [x] Incremental matcher upsert/removal maintains report pools, category/colour frequencies, and configured text/image vectors; report creation and feedback status changes atomically replace the process-local snapshot. Requests use O(1) views over immutable snapshots, and database-backed per-campus generation counters invalidate stale API/worker snapshots across processes.
- [x] Worker jobs refresh matches for the submitted report and the opposing reports in its ANN shortlist, plus previously persisted counterpart queries, preventing stale match rows after a new report or vector update. The live Compose PostgreSQL matcher path is validated; production-scale validation remains outstanding.
- [x] The 10k-request HTTP soak revalidates API matcher generation counters: an external report insert during the run triggers exactly one cache reload, and subsequent requests see the new report.
- Worker job: recompute only the *affected* pairs when a new report arrives (candidates returned by the ANN prefilter), not the whole corpus.

**C. Bounded caches (topic #13)**
- `EDIT` `lostfound/features.py` — replace the module-global `_ATTR_CACHE` and `_TOKEN_CACHE` dicts with `functools.lru_cache(maxsize=…, typed=True)` on pure helpers keyed by `(report_id, version)` where `version` bumps on edit, **or** an explicit bounded LRU. Add a size assertion test.
- Rationale: the current caches grow forever in a long-running server; on a serverless host that is a slow leak, on a long-lived VM a real one.

**D. Pagination & caps (topic #11)**
- `EDIT` `lostfound/api.py` — `GET /api/reports`: cursor pagination (`?cursor=<created_at,id>&limit=`, default 25, **hard max 100**). Return `next_cursor`.
- `EDIT` `web/src/api.ts` — page through with the cursor; `web/src/App.tsx` uses an infinite query (naturally lands in Phase 7's data layer).
- `EDIT` `GET /api/feedback` — paginate the same way.
- Enforce max page size at the API boundary **and** add a test that `limit=100000` is clamped.

**E. Object storage for photos**
- `NEW` `lostfound/objects.py` — S3-compatible client (R2/S3) behind an interface; `put_photo()`, `signed_url()`, `delete_photo()`. Local filesystem backend kept for dev.
- `EDIT` `lostfound/pipeline.py` `save_report_photo` — upload to object storage, store the key (not a local path) in `reports.image_path`.
- `EDIT` `lostfound/api.py` — serve `/media/*` via **signed, expiring URLs** (or redirect to the CDN), never a public bucket listing. Add `DELETE /api/reports/{id}` (soft delete → purge job) for Phase 8.
- Migration script to move existing `data/media/*.png` into the bucket.
- [x] Local filesystem and optional S3-compatible object-store backends now support validated keys, image upload/read/delete, and five-minute signed/presigned URLs; local legacy `data/media` assets remain served through the signed endpoint. Operator must keep the bucket private.
- [x] `scripts/migrate_local_photos.py` provides a dry-run-first, rerunnable migration from legacy local paths; uploads complete before database paths are changed and source files are retained.
- [ ] Live validation against a private S3-compatible bucket and confirmation of its access policy remain outstanding.

**F. Queue & worker**
- `NEW` `lostfound/queue.py` — Redis-backed queue (RQ/Celery/arq).
- `NEW` `lostfound/worker.py` — jobs: `embed_report`, `rematch_report`, `send_notification` (Phase 5), `purge_deleted`, `rebuild_index`, `train_reranker` (Phase 3).
- `EDIT` `lostfound/api.py` — report creation returns immediately after insert + enqueue; embeddings/matching run async; the client polls `GET /api/reports/{id}/status` or subscribes to a websocket/SSE for the match result. (Keep a synchronous path behind a flag for the demo and tests.)
- [x] Added an opt-in Redis Streams `rematch_report` queue and worker with retry/dead-letter handling; new non-moderated reports enqueue after persistence when `ASYNC_MATCHING=true`, and `GET /api/reports/{id}/status` exposes job progress. Synchronous matching remains the default; async mode generates missing text/image vectors in the worker. A live single-worker report-to-match run passed against Compose Redis and PostgreSQL; multi-worker load/recovery testing remains outstanding.
- [x] Docker Compose wires PostgreSQL + pgvector, Redis, API migrations/startup, and the asynchronous worker; the development image installs psycopg. The local stack is running and has been validated.
- [ ] Multi-worker Redis load/recovery validation remains outstanding; database-backed matcher invalidation is implemented and local single-worker execution is verified.

**G. Timezone & campus correctness**
- `EDIT` `lostfound/timeutil.py` + schema — store all timestamps as `timestamptz` UTC; render in the campus's timezone. Multi-campus India is one timezone today, but "anywhere in India" and future expansion demand this be explicit.
- `NEW` `tests/test_multicampus.py` — two campuses cannot see or match each other's reports.
- [x] Reports, feeds, details, matching, feedback, and campus-level statistics are isolated by campus. Authenticated requests use the account's provisioned campus; new or previously unassigned accounts and anonymous requests use `DEFAULT_CAMPUS_ID` (default `default`). Legacy reports with no campus ID belong to that default campus.
- [x] New report, feedback, and match timestamps are UTC; legacy timestamp strings are normalized once using each report's campus timezone (or `DEFAULT_CAMPUS_TIMEZONE`, default `Asia/Kolkata`). Browser-supplied offsets are preserved as UTC instants.
- [x] PostgreSQL stores report, match, feedback, owner, user, and audit timestamps as native `timestamptz`; API timestamp values are normalized to canonical UTC ISO strings for stable cursors and client parsing.
- [ ] Explicit absolute-time rendering in each campus timezone remains outstanding; the UI currently presents relative times.

### Deliverables
Postgres + pgvector, migrations, ANN-backed retrieval, incremental matcher updates, bounded caches, paginated APIs, object-storage photos, and an async worker.

### Definition of Done
- [x] `alembic upgrade head` from an empty Compose PostgreSQL database builds the full schema at revision 0009, including native `timestamptz` columns; SQLite downgrade coverage passes.
- [ ] Backfill script round-trips the existing 449-report corpus with matching scores within tolerance.
- [ ] Retrieval is ANN-prefiltered in the deployed runtime and ranking latency/Recall@10 are measured against PostgreSQL as the corpus grows.
- [ ] No endpoint rebuilds the whole matcher; a new report is matchable within seconds.
- [x] Cache size stays bounded over a 10k-request HTTP read soak; cache-helper capacity/eviction and 10k-input helper soak are also tested.
- [x] `limit=100000` is clamped to 100; report cursor paging returns every report exactly once.
- [ ] Live object-store rollout is validated against a private bucket; signed URLs expire. Local/S3 storage code and legacy migration are implemented, but bucket policy and live migration are not verified.

### Tests required
- [x] SQLite migration up/down and runtime-schema parity tests pass; the Compose PostgreSQL database migrated successfully through revision 0009.
- [ ] ANN retrieval recall vs the exhaustive baseline (must not drop Recall@10 below the Phase 0 nightly number).
- [ ] Incremental `upsert`/`remove` matches a full rebuild on a fixed corpus (property test).
- [x] Feature parsing/token caches have a fixed 4096-entry maximum and pass both a 10k-input helper soak and a 10k-request HTTP read soak.
- [x] Local SQLite load baseline, one run: 200 concurrent report creations (p50/p95/p99 1429.95/2609.13/3687.48 ms; 0% errors) and 100 concurrent matches (858.88/6416.34/6782.62 ms; 0% errors), using 32 workers. These local TestClient measurements are not production-capacity results; PostgreSQL load and Redis multi-worker validation remain outstanding.

**Topics covered:** #10 (migrations), #11 (pagination), #12 (matcher rebuild/scale), #13 (caches).

---

## 8. Phase 3 — The Multimodal Matching Accuracy Engine
**Duration:** Weeks 8–12 · **Depends on:** Phase 2 · **This is the core of the title.**

### Goal
Turn the honest baseline into a genuinely *intelligent* matcher: real encoders, a learned re-ranker trained on real staff labels, and a calibrated confidence you can put in front of a user.

### Why it aligns with the title
This is literally "multimodal machine learning", "computer vision and natural language processing", "identify similarities", "rank the most likely matches", "visual, textual, temporal and location information". Phases 0–2 were the runway; this is the flight.

### Tasks

**A. Real encoders (topic #22)**
- `EDIT` `lostfound/config.py`/`settings.py` — `TEXT_EMBEDDER="bge-m3"`, `IMAGE_EMBEDDER="dinov2-vits14"` behind a flag; keep the hashed baseline selectable for ablation and for CPU-only CI.
- `EDIT` `lostfound/embedders.py` — replace the placeholder `DinoV2ImageEmbedder`/`SentenceTransformerTextEmbedder` bodies with production versions:
  - **Image:** DINOv2 ViT-S/14 (or CLIP if you want a shared text–image space for "describe to search" — worth evaluating). Batch, half-precision on GPU, deterministic preprocessing.
  - **Text:** `BAAI/bge-m3` (multilingual — important for India: Hindi/Punjabi/English code-mixing). Batch and cache.
  - Add an **ONNX / fp16 export** path so inference is cheap on CPU for the free tier.
- `NEW` `lostfound/encoders.py` — a versioned encoder registry (`model_name`, `dim`, `revision`, `preprocess_hash`). Every stored vector records `model` + `revision` so you can detect and re-embed stale vectors.
- **Backfill:** re-embed the whole corpus with the new models; store both old and new for one release to compare.
- `NEW` `docs/EMBEDDERS.md` — ablation table: baseline vs DINOv2/bge-m3 on Recall@1/5/10, MRR, nDCG, and latency; per-category breakdown.

**B. Full evidence-fusion layer**
- `EDIT` `lostfound/features.py` — keep the 13 features but formalise them into the four named families with explicit, documented units:
  - *Visual:* image cosine (DINOv2), colour ΔE (CIE76), texture/shape.
  - *Textual:* dense cosine (bge-m3), token Jaccard, entity/serial match, OCR'd visible text.
  - *Temporal:* interval-vs-timestamp gap, impossible-order flag, hold-time-by-category.
  - *Location:* campus walk-distance graph, **plus real geo distance (haversine)** for GPS-tagged found reports, plus building match.
- `NEW` `lostfound/geo.py` — haversine + bounding-box filter on `location_point`; replaces the assumption that every report maps to one of three buildings.
- `EDIT` — extend `explain()` to emit a chip per family with the *measured value*, so the UI can show "photo similarity 0.81" not just "photos look similar".

**C. Multi-image reports**
- `EDIT` migrations + API + `pipeline.py` — up to 4 photos per report; store one embedding per photo plus an aggregate (max-pooled or attention-weighted) report embedding. Update `save_report_photo` → `save_report_photos`.

**D. OCR for serials and ID cards**
- `NEW` `lostfound/ocr.py` — PaddleOCR/Tesseract on the uploaded photo; extract serial numbers, student-ID numbers, printed text. Feed into the existing `visible_text`/`serial` feature — this is what makes "ID card" and "laptop with sticker" matches work.
- **Privacy:** OCR output is PII — store it encrypted at rest behind the privacy rules of Phase 8, and never return raw OCR text to a non-owner.

**E. Learned re-ranker (topic #21)**
- `NEW` `ml/reranker/train.py` — LightGBM **LambdaMART** (or XGBoost `rank:pairwise`).
  - Input: the feature vector snapshotted at decision time (`feedback.features_json`) — the repository already stores exactly the right thing, including `prob` and the raw features, so labels are not trained on hindsight.
  - Labels: `confirm` = 1, `reject` = 0, plus ground truth from the synthetic corpus for cold start.
  - Group by query (all candidates for one lost report) so the ranking objective is correct.
  - Output: a model artifact + a `model_version` written to `feedback` and used at serve time.
- `NEW` `ml/reranker/evaluate.py` — the ablation table that the README already promises: hand-set weights vs learned re-ranker on Recall@k, MRR, nDCG@10, Precision@alert, and a **precision-vs-alert-budget** curve.
- `NEW` `lostfound/rerank.py` — serving wrapper: load the model, score the ANN shortlist, fall back to the hand-set scorer if the model is absent (keeps CI and fresh installs working).
- `EDIT` `lostfound/matching.py` — `rank()` uses the re-ranker when available; always log which scorer produced a decision.
- **Cold-start plan:** seed training with synthetic pairs (Stanford Online Products + LLM-generated descriptions, as the README already proposes) until enough real staff labels accumulate. Track label count in the model version metadata.
- **Bias/label hygiene:** de-duplicate near-identical negative pairs; hold out by *item*, not by pair, so a re-ranker cannot memorise an item across splits.

**F. Calibration (topic #20)**
- `NEW` `lostfound/calibration.py` — temperature scaling / isotonic regression fit on a held-out set; store the calibration parameters with a version.
- `EDIT` `features.score_pair` — the sigmoid stays as the *raw* score; the API applies the fitted calibrator to produce the user-facing probability.
- `NEW` `ml/calibration/report.py` — reliability diagram + **ECE** + Brier score, written to `ml/calibration/` and rendered in the eval report.
- `NEW` `GET /api/model/info` — returns encoder names/revisions, re-ranker version, calibration version, and the measured metrics. This is the "trust page".
- Recalibrate per category if ECE varies materially by category (phones vs bottles vs ID cards behave very differently).

**G. Evaluation harness expansion**
- `EDIT` `lostfound/eval.py` — add: Precision@alert, recall at fixed alert budget, ECE, abstain rate, distractor ("lookalike beats truth") rate, per-category metrics, a **confidence-interval / bootstrap** over seeds, and a latency benchmark.
- `NEW` `ml/eval/report.html` (or a generated markdown) — the single artefact for the final report: metrics with spread, ablation tables, calibration plot, per-category bars.
- `EDIT` `cli.py` — `eval --seeds N --model rerank-v1 --report` produces the report.

**H. Taxonomy expansion**
- `EDIT` `lostfound/taxonomy.py` — grow categories to cover real campus items (ID cards, keys, chargers, earbuds, spectacles, umbrellas, cycle keys, lab coats, calculators), with per-category `hold_hours`, confusable sets, and per-category prior weights.
- `NEW` `docs/TAXONOMY.md` — the schema, plus what each category's classifier/OCR relies on.

### Deliverables
DINOv2 + bge-m3 encoders with an ablation, a learned LambdaMART re-ranker beating the hand-set baseline, calibrated probabilities with ECE, multi-image support, OCR, geo distance, and a report-ready evaluation artefact.

### Definition of Done
- [ ] Real encoders are the default and documented in `docs/EMBEDDERS.md` with the ablation table.
- [ ] The re-ranker improves **Precision@alert** and **nDCG@10** over hand-set weights on held-out data (by item), and the improvement is shown with spread across seeds.
- [ ] Recalibration reduces ECE below 0.05 on the held-out set; the reliability diagram is in the report.
- [ ] Recall@5 ≥ 0.95 and Recall@10 ≥ 0.97 on the 4-seed mean (or the regression is explained).
- [ ] A report with 4 photos embeds and matches; OCR extracts a serial from a synthetic ID card.
- [ ] `GET /api/model/info` reports versions and metrics that match the generated report.
- [ ] The hand-set scorer still works as a fallback when no model artifact is present.

### Tests required
- [ ] Encoder determinism and dimension/registry tests.
- [ ] Geo haversine unit tests against known campus coordinates.
- [ ] OCR extraction tests on fixture images.
- [ ] Re-ranker: training smoke test, holdout-by-item sanity test, serving-parity test (train-time features == serve-time features, the classic silent-bug source).
- [ ] Calibration: monotonicity, ECE on a synthetic calibration set.
- [ ] Eval regression test: metrics must not drop below a committed floor.

**Topics covered:** #20 (calibration), #21 (re-ranker), #22 (real embedders).

### Phase 3 progress — offline slice landed (30 October 2026)

Built and measured in the dependency-free runtime (no LightGBM/torch installed locally):

- [x] `lostfound/encoders.py` — versioned encoder registry (`name`, `dim`, `revision`, `preprocess_hash`) and `stale_vector_models()` so a re-embed target is visible. Real encoders are still adapters (task A).
- [x] `lostfound/geo.py` — haversine distance as a 14th evidence feature, with lost reports allowed a "last seen" pin so two points can be compared; explains itself as a `… m apart (GPS)` chip.
- [x] Multi-image reports (task C, offline slice) — up to four photos per report, each re-encoded and stored, with a max-pooled report image vector (`pipeline.aggregate_image_vector`) and `image_urls` in the API response. Photo keys beyond the first travel in the report payload; a dedicated `report_photos` table is the next schema step.
- [x] OCR (task F, offline slice) — `lostfound/ocr.py` reads printed serials off a photo with a pluggable engine (Tesseract when installed), redacts e-mail/phone/identity numbers before storage, keeps only serial-like tokens, and merges them into `visible_text` so the `serial` evidence feature can use them.
- [x] Phase 4 recovery loop — `claims` and the `POST /api/claims` (challenge-answer), `lookup`, staff `collect` endpoints; ownership questions come from the lost report and never contain their answers; answers are graded and discarded; the handover code is stored only as a hash and wrong attempts are capped; collection marks both reports `recovered` and writes an audit entry. UI: claim action on a match, "My reports" from the browser's ownership tokens, and a staff Desk queue.
- [x] Phase 5 notifications (offline slice) — transactional outbox (`notifications`, unique `dedupe_key`) written in the same transaction as the state change, delivered by `notify.dispatch` / `cli notify`; log transport by default, SMTP when `SMTP_HOST` is configured. Wired to claim verification, staff confirmation and counter collection.
- [x] `lostfound/rerank.py` — learned pairwise logistic (RankNet) re-ranker over the same evidence vector, numpy-only, `design()` shared by training and serving (train/serve parity), feature-layout guard on load, and fallback to the hand-set scorer when no artifact exists.
- [x] `lostfound/calibration.py` — Platt and isotonic fits, ECE, Brier, reliability bins, and a versioned `params.json` scoped to one scorer.
- [x] `lostfound/training.py` — split by query (item), ablation, operating point derived from a precision budget, markdown reports.
- [x] `GET /api/model/info` trust endpoint; `GET /api/config/operating-point` now reports the effective (calibrated) thresholds.
- [x] `cli train-rerank`, `cli calibrate`, `cli eval --report`; `ml/reranker/{train,evaluate}.py`, `ml/calibration/report.py`.
- [x] Eval harness: ECE, Brier, bootstrap 95% CIs, per-category metrics, alert-budget curve, `ml/eval/report.md`, `ml/eval/metrics.jsonl`.

Measured holdout (47 of 189 queries, split by item, seeded corpus):

| Metric | Hand-set | Learned + calibrated |
|---|---|---|
| Recall@1 | 0.7447 | **0.8511** |
| Recall@10 | 0.9787 | **1.0** |
| nDCG@10 | 0.8633 | **0.938** |
| Precision@alert | 0.871 | **0.90** |
| Coverage (alert rate) | 0.6596 | 0.2128 |
| ECE | 0.3478 | **0.0047** |

The coverage drop is the honest part: the hand-set sigmoid was overconfident (ECE 0.35), so its alert rate was inflated. The calibrated scorer alerts less often and is right when it does.

Still open in this phase: real DINOv2/bge-m3 encoders plus their ablation (`docs/EMBEDDERS.md`), OCR, multi-image reports, geo distance, taxonomy expansion, per-category recalibration, and LambdaMART if the LightGBM dependency is accepted.

---

## 9. Phase 4 — Accounts, Ownership & the Recovery Loop
**Duration:** Weeks 13–15 · **Depends on:** Phases 1, 3

### Goal
Close the loop: a person can own a report, be told when it matches, prove the item is theirs, and collect it.

### Why it aligns with the title
"Lost & Found" is a *process*, not a similarity score. "assist students and staff" means giving students an identity and staff a handover workflow.

### Tasks

**A. Accounts & "my reports" (topic #24)**
- `EDIT` migrations — `reports.owner_user_id` (nullable: anonymous reports allowed), `watchlists(user_id, saved_query, category, campus_id, active)`.
- `NEW` `GET /api/me/reports`, `GET /api/me/claims`, `PATCH /api/me` (contact preferences).
- `EDIT` `web/src/App.tsx` — add an auth context + a "My reports" surface; keep the anonymous browse feed.
- **Magic-link / OTP flow:** a report filed anonymously can be *claimed* later via a one-time link sent to the email/phone on the report. This is how you avoid forcing login before someone in a hurry reports a lost phone.

**B. Ownership challenge before handover**
- `NEW` `lostfound/challenge.py` — when a lost and found report are matched, generate 2–3 verification questions whose answers are *not* in the public found listing (e.g. "what is the lock-screen wallpaper?", "what is written on the sticker?"). The finder/staff sees the challenge; the owner answers; a correct answer unlocks handover.
- `NEW` `claims(id, lost_id, found_id, status, challenge_answers, verified_by, verified_at)` with states `pending → challenged → verified → collected → closed`.
- `EDIT` `api.py` — `POST /api/claims`, `POST /api/claims/{id}/verify`, `POST /api/claims/{id}/collect`.
- Never leak challenge answers or the found report's private attributes to the claimant before verification.

**C. Desk workflow for staff**
- `NEW` `web/src/components/Desk.tsx` — a staff-only review queue: new matches above the alert threshold, low-margin "needs review" pairs, pending claims, and a claim-verification view.
- `EDIT` `MatchPanel.tsx` — staff get confirm/reject **plus** "open claim" and "print/QR handover".
- `NEW` `lostfound/audit.py` — every staff action writes an `audit_log` row (actor, action, entity, before/after, request_id, IP). This is required for accountability in a real deployment.

**D. QR handover**
- `NEW` `GET /api/claims/{id}/qr` — a signed, expiring QR payload; the desk scans it to mark `collected`. Prints on a handover slip.
- `EDIT` `web/` — a printable handover receipt view.

**E. My-report status timeline**
- `NEW` `web/src/components/ReportTimeline.tsx` — visual states: reported → searching → matched → challenged → verified → collected. This is the emotional core of the product ("is anyone looking for my bag?").

### Deliverables
Real accounts, anonymous-report claiming, ownership challenges, a staff desk with a review queue, QR handover, and an audit log.

### Definition of Done
- [ ] A student can report anonymously, then claim the report via a magic link.
- [ ] A matched pair cannot be handed over without a passed ownership challenge.
- [ ] Staff see a review queue; every action lands in `audit_log`.
- [ ] A handover can be completed end-to-end with a QR scan and is reflected in the timeline.
- [ ] A non-owner cannot read another user's report, claim, or challenge answers.

### Tests required
- [ ] Authorization matrix test (each role × each endpoint).
- [ ] Challenge: wrong answer does not unlock; right answer does; answers never leak pre-verification.
- [ ] Audit: every mutating staff endpoint writes a row.
- [ ] E2E (Playwright): report → match → claim → verify → collect.

**Topics covered:** #24 (accounts/claim flow).

---

## 10. Phase 5 — Notifications & Proactive Matching
**Duration:** Weeks 16–17 · **Depends on:** Phases 2 (queue), 4 (accounts)

### Goal
Stop making users refresh. Tell them when something matches — and match proactively when a *found* report arrives against existing *lost* reports.

### Why it aligns with the title
"assist students and staff" is only real if the system reaches out. This is the retention loop that makes the product used rather than merely available.

### Tasks

**A. Notification service (topic #23)**
- `NEW` `lostfound/notifications.py` — a channel-agnostic `notify(user, template, data)` with per-user preferences, quiet hours, dedupe (never notify the same pair twice), and a delivery log table `notifications(id, user_id, channel, template, status, provider_id, sent_at)`.
- Providers: **email** (Postmark/SendGrid) and **WhatsApp/SMS** (Twilio). Recommended orchestration: **Knock**, which gives one API, user preferences, and cross-channel delivery logs on a free developer tier; the downstream email/SMS providers still need connecting inside Knock. Phone numbers must be stored in **E.164** (`+91…`) format.
- `EDIT` migrations — `users.phone_e164`, `notification_preferences(user_id, channel, opted_in, quiet_hours)`.
- **India-specific:** WhatsApp is the primary channel most students will actually read; treat email as backup and in-app as the fallback.

**B. Match alerts**
- `EDIT` `lostfound/worker.py` — after a match decision, enqueue a notification when: decision is `match`, or `review` with staff escalation, subject to user preferences.
- `EDIT` `matching.py` — emit an event (`MatchRaised`) rather than sending inline; keeps the matcher pure and testable.

**C. Proactive matching / watchlists**
- `NEW` `lostfound/watchlist.py` — when a new report is created, match it against *all* opposite-kind reports (the reverse direction of today's query-driven flow) and alert both owners on a confident hit. Today the corpus only matches when someone opens a case.
- `NEW` `GET/POST/DELETE /api/me/watchlists` — "alert me when anyone finds a black Milton bottle near the canteen".
- `EDIT` `worker.py` — nightly digest of new candidate matches per user (batched, quiet-hours-aware).

**D. In-app inbox + web push**
- `NEW` `web/src/components/Inbox.tsx` — in-app notification list with unread badges.
- `NEW` PWA web push (VAPID) via the service worker from Phase 6 — works on Android Chrome; iOS requires the PWA be installed, which the PWA work enables.

**E. Staff escalation**
- `NEW` a daily digest to the campus help-desk: new high-confidence matches, items past N days unmatched, pending claims.

### Deliverables
Multi-channel notifications with preferences, proactive found-vs-existing-lost matching, watchlists, an in-app inbox, and staff digests.

### Definition of Done
- [ ] A new found report triggers alerts to matching existing lost reports.
- [ ] Emails/WhatsApp render correctly with correct branding and deep links back to the case.
- [ ] Duplicate notifications are impossible (dedupe key = `(user, pair, decision_version)`).
- [ ] Users can opt out per channel; quiet hours are respected.
- [ ] Delivery logs show sent/failed; failures retry with backoff and then surface.

### Tests required
- [ ] Template rendering tests (including Hindi/Punjabi in Phase 6).
- [ ] Dedupe and preference tests.
- [ ] Provider failure → retry → dead-letter.
- [ ] Proactive matching property test (reverse-direction match equals forward-direction for a fixed corpus).

**Topics covered:** #23 (notifications).

---

## 11. Phase 6 — India-Wide Deployment, Multi-Campus & PWA
**Duration:** Weeks 18–21 · **Depends on:** Phase 2 (platform)

### Goal
Serve any campus in India, from a Mumbai region, on a phone with a 3G connection, in the language the student actually uses.

### Why it aligns with the title
"**which can be accessed in India anywhere**" — this phase is that clause.

### Tasks

**A. Multi-campus tenancy**
- `NEW` migrations — `campuses` table (name, short code, lat/lon, timezone, status, branding), `campus_domains` (subdomain → campus).
- `EDIT` every query path — campus scoping is mandatory: matching pools, feeds, notifications, analytics. Enforce with a repository-layer guard, not by convention.
- `NEW` onboarding flow for a new campus: admin creates campus → defines verified locations → invites staff → the campus goes live. Document this as the "add a campus in a day" runbook.
- `EDIT` `lostfound/campus.py` — replace the hardcoded LPU geometry with campus-scoped location data loaded per campus; keep the LPU map JSON for the demo.

**B. Verified locations at scale**
- `NEW` `lostfound/places.py` — a lookup that turns a free-text or GPS location into either a verified campus place or coordinates: fuzzy-match against the campus building list, fall back to reverse geocoding, and let staff verify/promote new places over time.
- This replaces today's "only three verified LPU buildings" limitation, which would cripple a second campus.

**C. Deployment**
- `EDIT` `Dockerfile` → production image; `NEW` `.github/workflows/deploy.yml` — build, push, deploy to Cloud Run / ECS in the **India region** (asia-south1 / ap-south-1), DB migrations run as a release step, zero-downtime rollout.
- `NEW` `infra/` — infrastructure as code (Terraform, or the platform's own manifest): Postgres, object bucket, Redis, secrets, CDN, DNS. Committing IaC is what makes "add a campus" and disaster recovery possible.
- `NEW` `docs/RUNBOOK.md` — deploy, rollback, restore from backup, rotate secrets, scale up.
- Backups: automated Postgres PITR + a weekly restore drill (a backup you have never restored is not a backup).
- **Data residency (India):** keep the primary database and object storage in an Indian region; document the data-flow so you can answer "where does student data live?" truthfully.

**D. PWA & offline**
- `NEW` `web/public/manifest.webmanifest`, icons, `web/src/sw.ts` (Workbox) — installable, precached shell, runtime caching for `/media` (stale-while-revalidate), and an offline page.
- `NEW` offline reporting — drafts saved in IndexedDB (`web/src/lib/offline.ts`) and synced when connectivity returns, with a visible "queued" state. Campus wifi and mobile data drop; a report that vanishes is worse than a slow one.
- `EDIT` `index.html` — `theme-color`, `apple-touch-icon`, viewport-fit, and the PWA meta set (overlaps Phase 7's metadata task).

**E. India-appropriate performance**
- Budget against a **3G / low-end Android** profile, not fibre: first contentful paint < 2.5 s, interactive < 5 s on a Moto-G-class device.
- `EDIT` — code-split Leaflet (it is a large dependency), lazy-load the map below the fold, ship WebP/AVIF for item photos, responsive `srcset`, and compress the exported DINOv2/bge-m3 models (ONNX int8/fp16) so cold starts and CPU inference are affordable.
- `NEW` `web/perf/budget.json` — enforced in CI (bundle size budget per chunk).

**F. Multilingual & low-literacy support**
- `NEW` `web/src/i18n/` — `en`, `hi`, `pa` (add more per campus). Extract all UI strings; no hardcoded copy.
- `NEW` voice input — Web Speech API (`hi-IN`, `pa-IN`, `en-IN`) on the description field, with graceful degradation. Many students will *speak* the description far faster than they type it.
- `EDIT` `textnorm.py`/encoders — bge-m3 already handles multilingual text; add transliteration tolerance (Hindi in Latin script, e.g. "paani ki bottle") to the attribute extractor, and add Devanagari/Gurmukhi stopword-free tokenisation.
- Accessibility of language: keep sentences short, avoid idioms, pair icons with labels.

**G. Notifications at national scale**
- `EDIT` notifications — per-campus sender identity, regional sender numbers, and a template per language.

### Deliverables
A multi-campus, India-region deployment with IaC, PWA + offline drafts, multilingual UI incl. voice input, and a performance budget enforced in CI.

### Definition of Done
- [ ] A second campus can be created and used with no code change.
- [ ] Production is deployed in an India region with automated migrations and a tested rollback.
- [ ] The app installs as a PWA and a draft survives an offline → online transition.
- [ ] UI works in English, Hindi, and Punjabi; voice input fills the description.
- [ ] Performance budget passes on a throttled 3G profile.
- [ ] A restore-from-backup drill has been executed and documented.

### Tests required
- [ ] Multi-campus isolation (data + matching + notifications).
- [ ] Offline sync (Playwright with network emulation).
- [ ] i18n key-coverage test (no missing translations).
- [ ] Bundle-size budget test.
- [ ] Smoke test against the deployed environment post-deploy.

---

## 12. Phase 7 — Premium & Interactive UI
**Duration:** Weeks 22–25 (may overlap Phase 6) · **Depends on:** stable API contract

### Goal
Move from "competent student project" to "feels like a funded product" — without sacrificing speed or accessibility.

### Why it aligns with the title
"Intelligent" should *look* intelligent. Explainability, confidence, and the recovery timeline are only valuable if a stressed student understands them in five seconds on a phone.

### Tasks

**A. Frontend data layer (topic #15)**
- `NEW` `web/src/lib/query.ts` — TanStack Query (or RTK Query): caching, retry/backoff, optimistic confirm/reject, and infinite pagination for the feed. Replace the raw `fetch` + `void load()` full-refetch pattern in `App.tsx`.
- `NEW` `web/src/lib/network.ts` — one place for the base URL, auth header injection, 401 → refresh → retry, and offline detection.
- **Optimistic UI:** confirming a match updates instantly and reconciles; on failure it rolls back with a toast.

**B. Error handling & resilience (topic #14)**
- `NEW` `web/src/components/ErrorBoundary.tsx` — wrap the app and each major panel; a crash in the map must not blank the page.
- `NEW` `web/src/components/Toast.tsx` — non-blocking success/error feedback (today errors are a single banner).
- `EDIT` `main.tsx` — mount the boundary; report caught errors to Sentry (Phase 9).

**C. Accessibility (topic #14)**
- `EDIT` `App.tsx` — skip-to-content link; **focus management** (opening a case moves focus to the verdict heading; closing returns focus to the list row).
- `EDIT` `MatchPanel.tsx` — `aria-live="polite"` region announcing "3 matches found, top confidence 78%"; the confidence meter gets `role="meter"` with `aria-valuenow`.
- `EDIT` `CampusMap.tsx` — a screen-reader-accessible list of mapped reports as a fallback for the map; keyboard-navigable pin list; `aria-label` per pin with kind and recency (today pins are `divIcon`s that screen readers cannot reach).
- `EDIT` `ReportForm.tsx` — associate every error with its field via `aria-describedby`; announce the live extract preview politely and *not* on every keystroke (debounce the announcement).
- Contrast: audit the `--lost`/`--found`/`--muted` palette against WCAG AA (4.5:1 body, 3:1 large/UI); fix tokens in `styles.css`.
- `EDIT` CI — add `jest-axe`/`vitest-axe` assertions and a `pa11y`/`axe` run against the built app; fail on new violations.
- Target: **WCAG 2.1 AA**, documented in `docs/ACCESSIBILITY.md` with a VPAT-style statement.

**D. Design system & visual polish**
- `NEW` `web/src/styles/tokens.css` — split the 2,688-line `styles.css` into tokens + component modules. Extend the token set: spacing scale, radius scale, elevation scale, motion durations, and a proper type scale.
- **Fix the phantom fonts (topic #17):** `--serif` names Merriweather/Playfair which are never loaded, so it silently falls back to Georgia. Either self-host the webfonts (subset, `font-display: swap`, preload) or drop the family. Deciding to load two display fonts for a campus tool is usually the wrong call — pick one, subset it, ship it.
- `NEW` `web/src/styles/` per-surface modules: `map.css`, `feed.css`, `match.css`, `form.css`, `desk.css`.
- **Dark mode** — `prefers-color-scheme` plus a manual toggle, with tokens doing the work (no per-component colour literals). Add a `prefers-contrast: more` variant.
- Premium details that actually matter: skeleton → content transitions (not spinners everywhere), considered empty states, a real 404/offline page, consistent iconography (one icon set), and micro-copy written for a stressed user ("We'll message you the moment something matches").

**E. Motion & interaction (make it "interacting")**
- `EDIT` `styles.css` → component modules — add purposeful motion: match-card entrance stagger (≤ 40 ms apart), confidence meter fill, map pin drop, sheet/panel transitions. All **capped at 200–250 ms**, all respecting `prefers-reduced-motion` (the token already exists in the CSS — extend it to the new animations).
- `NEW` `web/src/components/MatchExplorer.tsx` — an interactive "why this match?" panel: tap a reason chip to highlight the corresponding evidence (the photo region, the colour swatch, the time gap on a mini timeline, the two pins on the map). This is the single most differentiating UI idea in the plan — it turns explainability into something you can *play* with.
- `NEW` a before/after **photo comparison slider** for visual matches.
- `NEW` a **confidence explanation** on hover/tap: "78% — colour matches, same building, found 3 hours after loss; the model is unsure about the model number."
- Keyboard shortcuts for the desk (confirm/reject/skip) — staff process dozens of cases.

**F. Map upgrade (topic #19)**
- `EDIT` `CampusMap.tsx` — add **marker clustering** (`leaflet.markercluster`) for dense feeds; cluster by kind with a count badge; spiderfy on click.
- Lazy-load the map chunk (Leaflet out of the main bundle); render the feed before the map.
- Add a **loss heatmap** view (aggregate, privacy-safe — no individual report) for campus admins; this is both a nice feature and a genuinely useful campus-safety insight.
- Support map tiles with a **degrades-gracefully offline** fallback (a static campus image) when tiles fail to load.

**G. Production metadata (topic #18)**
- `EDIT` `index.html` — favicon set (`favicon.svg` + `apple-touch-icon` + maskable PWA icons), Open Graph + Twitter card tags, `theme-color`, `description`, `canonical`, and a `robots.txt` that keeps the desk/staff routes out of search indexes.

**H. Frontend tests (topic #16)**
- `NEW` `web/src/**/*.test.tsx` — Vitest + React Testing Library for every component: form validation, live preview, match panel states (`match`/`review`/`no_match`), feedback optimistic behaviour, error boundary.
- `NEW` `e2e/` — Playwright covering: report a lost item → see matches → confirm; report found → alert path; claim → challenge → collect; offline draft sync; keyboard-only navigation of the full flow; axe scan on each page.
- `EDIT` contract tests — verify the frontend's TypeScript types against a real API response (`openapi-typescript` to generate `web/src/api-types.ts` from FastAPI's OpenAPI schema, so backend/UI drift becomes a compile error instead of a runtime surprise). This kills the whole "the UI hardcodes 0.55" class of bug at the source.

### Deliverables
A premium, accessible, fast, test-covered UI with an interactive explainability explorer, dark mode, clustering, motion, and generated API types.

### Definition of Done
- [ ] Zero axe violations on every page at WCAG AA; keyboard-only completion of report → confirm.
- [ ] No component crash can blank the page (error boundary verified by test).
- [ ] `styles.css` is split; no colour literal outside `tokens.css`; fonts are either loaded or removed.
- [ ] Map is a separate lazy chunk; bundle budgets pass.
- [ ] MatchExplorer links each reason chip to its evidence.
- [ ] Component + E2E suites run in CI and are required.
- [ ] All API types are generated from the OpenAPI schema; no hand-copied threshold literals remain anywhere in `web/`.

**Topics covered:** #14 (a11y/error boundary), #15 (data layer), #16 (frontend tests), #17 (styles/fonts), #18 (metadata), #19 (clustering/code-split).

---

## 13. Phase 8 — Privacy, Compliance & Moderation
**Duration:** Weeks 26–27 · **Depends on:** Phases 2, 4

### Goal
Handle student data — photos, phones, locations, free text — lawfully and defensibly.

### Why it aligns with the title
The system processes personal data by design (photos of belongings, precise GPS, student ID numbers via OCR). No serious deployment, campus or funder will accept it without this.

### Tasks (topic #25)

**A. Legal pages**
- `NEW` `web/src/pages/Privacy.tsx`, `Terms.tsx`, `Accessibility.tsx` — plain-language privacy policy, terms of use, and an accessibility statement. Reachable from the footer of every page.
- `NEW` `docs/DPIA.md` — a Data Protection Impact Assessment: what data, why, lawful basis, retention, who sees it, cross-border transfers.

**B. Consent & notice**
- `EDIT` `ReportForm.tsx` — a consent checkbox at submission: what happens to the photo, the location, and the contact details; link to the policy. Record consent (`consents(user_id/anon_id, kind, version, granted_at)`).
- Location: keep the existing "permission requested only after the user taps *Use my current location*" pattern — it is exactly right. Add an explicit note about what precision is stored.

**C. PII handling**
- `NEW` `lostfound/pii.py`:
  - **Redaction** of obvious PII in free text (phone numbers, emails, Aadhaar-like 12-digit patterns, roll numbers) before storing/displaying a *found* item publicly.
  - **Face/person blur** on uploaded photos (a lightweight detector) so a photo of a backpack with a stranger in frame does not publish a stranger. Store the redacted version for public display; the original only for staff/owner under authorization.
  - Encrypt OCR output at rest.
- `EDIT` `pipeline.py` — run redaction on ingest; version the redaction rules so it is auditable.

**D. Retention & the right to erasure**
- `EDIT` migrations — `reports.deleted_at`, `deletion_requests`.
- `NEW` worker job `purge_deleted` — hard-delete media, vectors, and PII for reports past the retention window or on request; keep a minimal, non-identifying audit record (and say so in the policy).
- `NEW` `DELETE /api/reports/{id}` and `DELETE /api/me` (account deletion) — the erasure path. Must cover object storage, the vector store, notifications, and backups (document the backup-expiry lag honestly).
- Retention defaults: unmatched found reports N months, resolved cases shorter, logs shorter still — all configurable per campus.

**E. Moderation (ties back to topic #2)**
- `NEW` `web/src/components/ModerationQueue.tsx` — a staff/admin queue for reports flagged by heuristics (spam, PII, abuse) with approve/reject/hide.
- `EDIT` `api.py` — `pending_review` reports are excluded from matching until approved.
- `NEW` a **report abuse** button on every item, feeding the moderation queue.

**F. Access control review**
- `NEW` `docs/ACCESS_CONTROL.md` — the full role × resource matrix, plus the rule that PII (OCR text, originals, contact details) is visible only to the owner and authorized staff.
- `EDIT` — ensure object-storage URLs are signed and short-lived; ensure the public found-listing shows only redacted data.

### Deliverables
Legal pages, DPIA, consent capture, PII redaction + face blur, retention and erasure, and a moderation queue.

### Definition of Done
- [ ] Every page reaches the privacy policy; every submission records consent.
- [ ] A public found listing never shows phone numbers, emails, ID numbers, or unblurred faces.
- [ ] Account deletion removes media, vectors, and PII, leaving only the documented non-identifying audit trace.
- [ ] Retention job runs nightly and its effect is measurable.
- [ ] Flagged reports leave the matching pool until approved.

### Tests required
- [ ] PII redaction fixtures (phones, emails, Aadhaar-like, roll numbers) in English, Hindi, Punjabi.
- [ ] Face-blur smoke test.
- [ ] Erasure test: after deletion, no media/vector/PII row survives.
- [ ] Access-control matrix test.

**Topics covered:** #25 (privacy/compliance).

---

## 14. Phase 9 — Observability, Analytics & Operations
**Duration:** Weeks 28–29 · **Depends on:** Phases 2, 6

### Goal
Know what the system is doing, whether it is helping, and when it is about to break.

### Why it aligns with the title
An "Intelligent … System" you cannot measure is not intelligent, it is lucky. This phase makes accuracy and cost continuous, visible quantities.

### Tasks

**A. Observability (topic #8b — completion)**
- `EDIT` `lostfound/logging.py` — propagate `request_id`/`trace_id` through the worker and matcher; log one structured line per match decision (query, top candidate, prob, decision, model version, latency).
- `NEW` `/metrics` (Prometheus): request rate/latency/errors, queue depth, matcher latency, ANN recall proxy, notification delivery rate, DB pool saturation.
- `EDIT` — add **OpenTelemetry** traces (API → DB → matcher → encoders), exported to Grafana Cloud/SigNoz.
- `NEW` — Sentry for backend + frontend with **release tagging** (ties errors to the deploy) and source maps uploaded in CI.
- `NEW` `NEW` uptime + synthetic checks (Better Stack / UptimeRobot + Checkly running your Playwright flows) against production, with on-call alerts (PagerDuty/Healthchecks).
- `NEW` `docs/SLOs.md` — define and track: availability 99.5%, p95 match latency < 1.5 s, notification delivery > 98%, error rate < 1%.

**B. Funnel analytics (topic #26)**
- `NEW` — a privacy-respecting product analytics integration (self-hostable, or a lightweight first-party event table) with a defined event schema: `report_created`, `match_viewed`, `match_confirmed`, `match_rejected`, `claim_started`, `claim_verified`, `item_collected`, `notification_clicked`.
- **Do not** put free-text descriptions or photos into analytics. Pseudonymous IDs only.
- `NEW` `web/src/pages/Analytics.tsx` (staff/admin) — the funnel: reports → matched → confirmed → claimed → collected, plus time-to-recovery distribution and where users drop off.
- `NEW` campus-admin dashboard — top loss locations (the heatmap), top categories, unresolved backlog, staff throughput.

**C. ML monitoring**
- `NEW` `ml/monitoring/drift.py` — track input drift (category mix, colour mix, text length, photo count) and output drift (alert rate, abstain rate, confirmed/rejected ratio) per week.
- `NEW` — a **weekly automated eval** on a held-out set; alert if Recall@5, Precision@alert, or ECE regress beyond a threshold. This is how accuracy stays honest over time.
- Label-harvesting dashboard: how many staff labels exist, how the re-ranker's live Precision@alert compares to its offline estimate (the classic train/serve skew check).

**D. Ops**
- `NEW` runbooks for: DB failover, queue backlog, encoder timeout, notification provider outage, elevated abstain rate, cost spike.
- `NEW` error budget policy: when the budget burns, feature work pauses and reliability work takes over.
- `NEW` cost dashboard + budget alerts (GPU/CPU inference is the first cost that bites).

### Deliverables
Structured logs, metrics, traces, error tracking, uptime/synthetic monitoring, a product funnel, ML drift + regression alerting, and runbooks with SLOs.

### Definition of Done
- [ ] A failing endpoint produces an alerted alert with a trace and a release tag.
- [ ] The funnel is visible and matches a manual count on a seeded corpus.
- [ ] The weekly ML regression job alerts on a deliberately degraded model in a test.
- [ ] SLOs are defined, instrumented, and displayed on a dashboard.
- [ ] Every runbook has been walked once (a game day).

### Tests required
- [ ] Metrics endpoint contract test.
- [ ] Alert-fires test (chaos: kill the worker, break the DB connection).
- [ ] Analytics event schema validation.
- [ ] Drift job test on synthetic drift.

**Topics covered:** #8 (logging/metrics/error tracking — completed), #26 (funnel analytics).

---

## 15. Phase 10 — Unique Differentiators & Hardening
**Duration:** Weeks 30–33 · **Depends on:** Phases 3–9

### Goal
Ship the things that make this *the* campus lost & found, not another one; then harden.

### Why it aligns with the title
"Intelligent" is a claim you earn by doing things a naive system cannot. These are the differentiators that make the claim defensible and the product sticky.

### Tasks

**A. Differentiator features (pick, then build)**

1. **"Found it for you" proactive alerts** — already in Phase 5; here add the *converse*: when a lost report is filed, immediately surface the top found candidates to the reporter (before staff see it) with an "Is this yours?" interaction. Instant gratification is the hook.
2. **Anonymous-but-claimable reporting** — Phase 4 laid the rails; polish it into a headline feature ("report without an account, claim it with one tap later").
3. **Cross-campus transfer** — an item found on campus A whose owner is on campus B; a staff-mediated inter-campus handover with a chain-of-custody log. Genuinely novel for a multi-campus network.
4. **Item passports** — an optional, user-initiated registration of valuables (serial, photo, receipt) *before* loss. When it goes missing, the match is near-instant and high-precision because the attributes are already known. This is the highest-accuracy path the system can offer and a strong differentiator.
5. **Smart "did you mean" de-duplication** — detect and merge near-duplicate found reports for the same physical item (three people report the same umbrella) so the owner sees one candidate, not five.
6. **Confidence-aware routing** — high confidence → auto-alert; medium → staff queue; low → silent watchlist. Already the design; here it becomes configurable per campus with a measured precision budget.
7. **Counterfactual explainability** — "if the colour matched, this would have been 92%" — a genuinely research-grade explainability touch that also helps staff spot bad data.
8. **Campus safety insights (privacy-safe)** — aggregated loss heatmaps and category/site trends for campus administration; a value proposition beyond the individual user.
9. **Finder rewards & recognition** — optional gamification (badges, points, campus leaderboard for honest finders). Drives the supply side (found reports), which is the real constraint on a lost & found system.
10. **Watchdog for valuables day** — reminders/checklists before exam season and festivals, when loss spikes; data-driven, from your own historical trends.
11. **QR/asset tags** — printable campus tags whose QR resolves to a report; scan → instant match.
12. **Natural-language search** — use the shared CLIP/bge space to let a user *describe* an item and search all found reports semantically, beyond exact matches.
13. **Voice-first reporting** in Hindi/Punjabi — Phase 6 built the input; here it becomes a first-class path with a transcript preview.
14. **Desk console with bulk actions** — staff process 50 cases with keyboard shortcuts, filters, and bulk status changes.
15. **Open API for campus systems** — a documented, keyed API so a campus's own app/portal can file and query reports. Turns the project into infrastructure.

For each feature you actually build, add: a spec in `docs/features/`, an eval or metric that proves it helps, tests, and a line in the funnel it moves.

**B. Hardening**
- Threat model (`docs/THREATS.md`, STRIDE): auth bypass, IDOR on reports/photos, upload RCE, SSRF via image fetch, enumeration of reports, prompt-injection-equivalent (if any LLM features are added), and rate-limit evasion.
- Penetration test against a staging copy; fix and re-test. Report the results.
- Dependency and container scanning in CI (already wired in Phase 0) with an SLA for critical CVEs.
- Chaos exercises: kill the DB mid-request, drop the queue, revoke object storage access, and confirm graceful degradation with user-visible, honest errors.
- Data-integrity audits: no orphan vectors, no report without an owner consent record, no matched pair without a claim path.
- Cost stress test: what does 50k reports and 10k daily users cost? Publish the number.

### Deliverables
3–5 genuinely differentiating, measured features plus a hardened, threat-modelled, penetration-tested system.

### Definition of Done
- [ ] Each shipped differentiator has a spec, a metric, tests, and a funnel/accuracy effect recorded.
- [ ] Threat model reviewed; penetration-test findings fixed or explicitly accepted with rationale.
- [ ] Chaos exercises pass with graceful degradation.
- [ ] Cost per 10k daily users is measured and published.

### Tests required
- [ ] Per-feature unit + E2E tests.
- [ ] IDOR/authorization regression suite (the most likely real-world bug).
- [ ] Load test at target scale with published latency and cost.

---

## 16. Phase 11 — Launch Readiness, Evaluation & Documentation
**Duration:** Weeks 34–35 · **Depends on:** everything

### Goal
Prove the title's claim with evidence, and hand over a system someone else can run.

### Tasks

**A. Evaluation and the claim**
- `NEW` `docs/EVALUATION.md` — the definitive results document: every metric with corpus, seed count, spread, and confidence interval; every ablation table; the calibration plots; per-category breakdowns; latency and cost.
- **Accuracy statement discipline:** never a bare "98% accurate". Always: metric, corpus, n, spread. State precisely which of recall/precision/calibration you are claiming and at what alert budget.
- Reproduce the headline numbers on a **held-out, ideally semi-real** corpus, not only the synthetic one. Collect even a few hundred real (consented, anonymised) reports to test the sim-to-real gap honestly.
- `NEW` a one-page results summary for the report/abstract.

**B. Documentation set**
- `EDIT` `README.md` — current architecture, quickstart, deploy, and a link out to each doc.
- `NEW` `docs/ARCHITECTURE.md` (ADR log), `docs/DATA_MODEL.md`, `docs/API.md` (or rely on OpenAPI + generated clients), `docs/RUNBOOK.md`, `docs/SLOs.md`, `docs/ACCESS_CONTROL.md`, `docs/DPIA.md`, `docs/THREATS.md`, `docs/TAXONOMY.md`, `docs/EMBEDDERS.md`, `docs/ACCESSIBILITY.md`, `docs/CONTRIBUTING.md`.

**C. Launch checklist**
- [ ] Backup restore drill passed and documented.
- [ ] Rollback tested in production.
- [ ] Status page live; on-call rotation set.
- [ ] Seed data cleared from production; real campus onboarded.
- [ ] Rate limits, quotas, and cost alerts tuned.
- [ ] Support channel + FAQ + help content written.
- [ ] Accessibility statement published.
- [ ] Privacy policy, terms, DPIA signed off.
- [ ] Staff trained with a desk runbook; a dry-run of a real handover.

**D. Pilot & iteration**
- Run a **closed pilot on one campus** for 2–4 weeks before opening nationally.
- Instrument the funnel; watch abstain rate, precision@alert, and time-to-recovery.
- Weekly review: fix the top failure mode, then re-measure. Ship the learning into the taxonomy, weights, or prompts.

### Definition of Done
- [ ] Every metric in the final report is reproducible by a fresh checkout + one command.
- [ ] A person who has never seen the repo can deploy it from `docs/RUNBOOK.md`.
- [ ] The pilot has produced at least one full report → match → verified handover cycle with a real student.
- [ ] All launch-checklist items are done or explicitly waived with a reason.

---

## 17. Master traceability matrix — all 26 topics

| # | Topic (from the review) | Primary phase | Also touched |
|---|---|---|---|
| 1 | No authentication / open CORS | **1** | 4, 8 |
| 2 | No rate limiting / abuse controls | **1** | 8 (moderation) |
| 3 | Unsafe shared-DB concurrency | **1** (interim) → **2** (final) | — |
| 4 | No HTTP security headers | **1** | 6 |
| 5 | Photo upload / Pillow hardening | **1** | 8 (face blur) |
| 6 | Duplicated thresholds (4 places) | **1** | 7 (generated types) |
| 7 | No CI / containers / packaging | **0** | 6 (deploy) |
| 8 | No structured logging / metrics / error tracking | **0** (setup) → **9** (dashboards) | — |
| 9 | Hardcoded config (no 12-factor) | **0** | 2 |
| 10 | No DB migrations | **2** | — |
| 11 | Unpaginated / uncapped list APIs | **2** | 7 (infinite query) |
| 12 | Every write rebuilds the whole matcher | **2** | 3 (ANN) |
| 13 | Unbounded in-process caches | **2** | — |
| 14 | No aria-live / focus mgmt / error boundary | **7** | — |
| 15 | No frontend data layer | **7** | 2 (pagination) |
| 16 | No frontend tests | **7** | 0 (CI), 9 (synthetic) |
| 17 | Monolithic styles.css + unloaded fonts | **7** | — |
| 18 | index.html missing metadata | **7** | 6 (PWA meta) |
| 19 | No map clustering / code-splitting | **7** | 6 (perf budget) |
| 20 | Confidence not calibrated | **3** | 9 (monitoring) |
| 21 | Learned re-ranker unbuilt | **3** | 9 (drift) |
| 22 | Offline embedders | **3** | 6 (cheap inference) |
| 23 | No notifications | **5** | 4 (accounts), 6 (i18n) |
| 24 | No accounts / claim flow | **4** | 10 (differentiators) |
| 25 | Privacy & compliance unaddressed | **8** | 6 (residency) |
| 26 | No funnel analytics | **9** | 10, 11 (pilot) |

**Plus the clauses of the title that the review did not cover but this plan adds:** India-wide access and multi-campus tenancy (Phase 6), the ownership/handover loop (Phase 4), proactive matching (Phase 5), premium/interactive UI (Phase 7), differentiators (Phase 10).

---

## 18. Test & QA strategy (applies to every phase)

| Layer | Tool | Gate |
|---|---|---|
| Python unit | pytest (existing, expand) | CI, required |
| Python integration | pytest + TestClient against a real Postgres | CI |
| Concurrency/load | locust/k6 | nightly + pre-release |
| Frontend unit | Vitest + React Testing Library | CI, required |
| Frontend a11y | vitest-axe + axe/pa11y on the build | CI, required |
| E2E | Playwright (report → match → claim → collect) | CI + post-deploy smoke |
| Contract | OpenAPI schema → generated TS types | CI (drift = build failure) |
| ML eval | `cli eval --seeds N` + regression floors | nightly, alert on drop |
| Calibration | ECE + reliability report | per model release |
| Security | pip-audit, npm audit, semgrep, secret scan | CI, required |
| Pen test | external/manual | Phase 10 |
| Chaos | fault injection | Phase 10 |

**Rule:** a phase is not done until its tests exist and pass in CI. No phase may lower a metric floor it established.

---

## 19. Risk register

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| Scope too large for the time available | High | High | Phases 0–3 are the defensible core (security + scale + the ML claim). Phases 4–11 are the product tier; cut from the end, and cut features not hardening. |
| Real staff labels never reach a trainable volume | High | Medium | Cold-start on synthetic pairs; ship the hand-set scorer meanwhile; keep the re-ranker behind a flag. |
| Real photos break the synthetic-corpus metrics | High | High | Build a small real, consented set early (Phase 3) and report the sim-to-real gap honestly. |
| GPU/compute cost | Medium | Medium | ONNX int8/fp16, batching, cache embeddings, ANN prefilter; measure cost in Phase 9. |
| No India region for a chosen provider | Medium | Medium | **Verify region availability at provisioning, do not assume.** IaC makes switching a config change. |
| Data-protection complaint | High | Low-Medium | Phase 8 does not move later than scheduled; consent, redaction, erasure, DPIA. |
| Abandonment / handover failure | High | Medium | Phase 11 documentation + CI + IaC; the runbook must let a stranger deploy. |
| Accuracy claim overreach in the writeup | High | Medium | §2 and Phase 11 accuracy-discipline rules; always report spread and corpus. |

---

## 20. Indicative timeline

| Phase | Weeks | Calendar (from 27 Oct 2026) | Theme |
|---|---|---|---|
| 0 | 1–2 | 27 Oct – 8 Nov | Foundation & tooling |
| 1 | 3–4 | 9 – 22 Nov | Security & correctness |
| 2 | 5–7 | 23 Nov – 13 Dec | Data platform & scale |
| 3 | 8–12 | 14 Dec – 17 Jan | **Multimodal matching engine** |
| 4 | 13–15 | 18 Jan – 7 Feb | Accounts & recovery loop |
| 5 | 16–17 | 8 – 21 Feb | Notifications & proactive matching |
| 6 | 18–21 | 22 Feb – 21 Mar | India-wide, multi-campus, PWA |
| 7 | 22–25 | 22 Mar – 18 Apr | Premium & interactive UI |
| 8 | 26–27 | 19 Apr – 2 May | Privacy & compliance |
| 9 | 28–29 | 3 – 16 May | Observability & analytics |
| 10 | 30–33 | 17 May – 13 Jun | Differentiators & hardening |
| 11 | 34–35 | 14 – 27 Jun | Launch readiness & evaluation |

Sequential total ≈ 35 weeks. With Phases 6 and 7 partially overlapping, and Phase 9 able to start once Phase 2 lands, a realistic elapsed time is **~7 months**. If the deadline is shorter, build **Phases 0 → 3** end-to-end (that is a genuinely strong, defensible project covering the title's ML claim plus security and scale) and present Phases 4–11 as a costed roadmap.

---

## 21. The one-line priority rule

If you have to cut: **security (1) → scale (2) → the ML claim (3)** are non-negotiable, because they are the difference between a demo and the title. **Accounts (4), notifications (5), India-wide (6), and the UI (7)** are what make it a product people use. **Privacy (8), observability (9), differentiators (10), and launch (11)** are what make it defensible and sustainable.
