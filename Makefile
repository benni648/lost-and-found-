# Developer entry points.
#
# On Windows the interpreter lives at .venv/Scripts/python.exe, elsewhere at
# .venv/bin/python; PY picks whichever exists. No `make` available? Every target
# is a plain shell command you can copy out.

PY ?= $(shell if [ -x .venv/Scripts/python.exe ]; then echo .venv/Scripts/python.exe; \
             elif [ -x .venv/bin/python ]; then echo .venv/bin/python; \
             else echo python3; fi)

.DEFAULT_GOAL := help

.PHONY: help install check lint format typecheck test seed campus eval serve migrate \
        web-install web-dev web-lint web-build up down

help: ## List the available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# --------------------------------------------------------------------- setup
install: ## Install Python runtime + dev + migration dependencies
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev,migrations]"

web-install: ## Install frontend dependencies
	cd web && npm ci

# ------------------------------------------------------------------- quality
lint: ## Lint and format-check the Python sources
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

format: ## Auto-fix and format the Python sources
	$(PY) -m ruff check . --fix
	$(PY) -m ruff format .

typecheck: ## Type-check the Python sources
	$(PY) -m mypy lostfound scripts

web-lint: ## Lint, format-check and type-check the frontend
	cd web && npm run lint && npm run format:check && npm run typecheck

test: ## Run the Python test suite
	$(PY) -m pytest

check: lint typecheck test web-lint ## Everything CI runs

# --------------------------------------------------------------------- data
campus: ## Write the campus map to data/campus.json
	$(PY) -m lostfound.cli campus

seed: ## Generate the demo corpus and build the database
	$(PY) -m lostfound.cli seed

eval: ## Evaluate retrieval against the ground-truth pairs
	$(PY) -m lostfound.cli eval

migrate: ## Apply database migrations
	$(PY) -m alembic upgrade head

# ------------------------------------------------------------------- serving
serve: ## Run the API on http://127.0.0.1:8000
	$(PY) -m lostfound.cli serve

web-dev: ## Run the Vite dev server on http://localhost:5173
	cd web && npm run dev

web-build: ## Build the single-page UI into web/dist
	cd web && npm run build

# ------------------------------------------------------------------ container
up: ## Start the full local stack with Docker Compose
	docker compose up --build

down: ## Stop the local stack
	docker compose down
