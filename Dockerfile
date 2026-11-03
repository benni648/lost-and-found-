# syntax=docker/dockerfile:1
#
# Production image: build the single-page UI with Node, then serve it from the
# API (which mounts web/dist at / when it exists).

# ---------------------------------------------------------------- stage 1: UI
FROM node:20-alpine AS ui
WORKDIR /ui
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ------------------------------------------------------------ stage 2: runtime
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY lostfound/ ./lostfound/
# Editable install on purpose: `lostfound.settings` derives the project root
# from its own __file__, so the package must stay at /app/lostfound. A regular
# install into site-packages would make the data directory resolve inside
# site-packages.
RUN pip install -e ".[migrations]"

COPY alembic.ini ./
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/

# The API serves the built SPA from here.
COPY --from=ui /ui/dist ./web/dist

ENV ENV=prod \
    API_HOST=0.0.0.0 \
    API_PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:8000/api/health" || exit 1

CMD ["python", "-m", "lostfound.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
