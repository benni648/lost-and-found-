# Contributing

## Setup

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev,migrations]"   # Windows
# .venv/bin/python -m pip install -e ".[dev,migrations]"         # macOS / Linux

cd web && npm ci && cd ..
```

Dependencies live in **`pyproject.toml`** — that is the single source of truth.
`requirements.txt` just points at the `dev` extra so the documented quickstart
keeps working. `make install` does both halves.

Optional extra: `rerank` (LightGBM / scikit-learn). There is deliberately no
`vision` extra: the project is classical ML end to end and loads no deep model,
so `torch`/`transformers`/`sentence-transformers` are not dependencies.

## Everyday commands

`make help` lists everything. The essentials:

| Task | Command |
|---|---|
| Lint + format-check Python | `make lint` |
| Auto-fix + format Python | `make format` |
| Type-check Python | `make typecheck` |
| Python tests | `make test` |
| Lint + type-check frontend | `make web-lint` |
| Everything CI runs | `make check` |
| Seed the demo corpus | `make seed` |
| Serve the API | `make serve` |
| Vite dev server | `make web-dev` |
| Full local stack | `make up` |

`make` is not installed everywhere (it is absent on a stock Windows box). Every
target is a plain shell command you can copy out of the `Makefile`.

## Configuration

`lostfound/settings.py` is the **only** module that reads `os.environ` or the
`.env` file. Everything else imports resolved values from `lostfound.config`,
which re-exports them.

- Add a new setting as a field on `Settings` in `lostfound/settings.py`.
- Document it in `.env.example`.
- If existing code wants a module-level constant for it, re-export it from
  `lostfound/config.py` so call sites do not have to change.
- Supabase Auth uses `SUPABASE_URL` and `SUPABASE_ANON_KEY`. Never add a
  service-role key to the application or browser. Signup only creates
  `student` accounts; assign privileged roles from the operator's CLI with
  `python -m lostfound.cli --db data/lostfound.db auth set-role <user-id> staff`.

A test asserts that `config` never drifts from `settings`, so a missed re-export
fails CI rather than silently using a stale value.

## Database migrations

Alembic is scaffolded in `migrations/`. Revision `0001` mirrors the schema the
runtime currently creates in `lostfound/store.py`.

```bash
.venv/Scripts/python.exe -m alembic upgrade head      # apply
.venv/Scripts/python.exe -m alembic downgrade base    # roll back
.venv/Scripts/python.exe -m alembic current           # where am I?
```

- The database URL comes from `DATABASE_URL`, falling back to the SQLite demo
  database, so migrations and the app can never point at different databases.
- **Migrations are frozen snapshots.** Never edit a released revision to match a
  change in application code — add a new one.
- `migrations/versions/` is excluded from ruff on purpose: those files are
  generated from `migrations/script.py.mako`, which ships Alembic's legacy
  annotations.

> **Phase 2 note.** Until the repository layer replaces `lostfound/store.py`,
> `store.connect()` still creates its own tables and Alembic is not yet the sole
> schema authority. Run migrations against a fresh database, or
> `alembic stamp head` an existing one.

## Code conventions

- Python: `ruff` (lint + format, line length 120) and `mypy` must be clean.
  `make check` before every push.
- Frontend: `eslint` (including `jsx-a11y`) and `prettier` must be clean.
- Never suppress a check to make it pass. If a rule genuinely does not apply,
  disable it narrowly with a comment explaining why — see the
  `react-hooks/set-state-in-effect` entry in `web/eslint.config.js` for the
  expected shape of that explanation.
- Prefer editing an existing module over adding a new one.

## Commits and branches

- Branch names: `feat/…`, `fix/…`, `docs/…`, `chore/…`.
- Commit messages: imperative subject under ~72 characters, then *why* in the
  body. Explain the reasoning, not the diff.
- Keep a pull request to one concern. CI must be green before merge.

## Pull-request checklist

- [ ] `make check` passes locally.
- [ ] New behaviour has a test; bug fixes have a regression test.
- [ ] New configuration is on `Settings` and documented in `.env.example`.
- [ ] Schema change is a new Alembic revision, with a working `downgrade`.
- [ ] No secrets, tokens, or real personal data in the diff.
- [ ] Docs updated when behaviour a user sees has changed.

## Where things live

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the module map, the target
architecture, and the decision log. [`docs/DEVELOPMENT_PLAN.md`](docs/DEVELOPMENT_PLAN.md)
is the phase-by-phase roadmap; it is the reference for *why* the structure looks
the way it does.
