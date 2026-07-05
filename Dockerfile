# ---------------------------------------------------------------------------
# Stage 1: Frontend — Next.js static export (PLAN.md §11)
# ---------------------------------------------------------------------------
FROM node:20-bookworm-slim AS frontend-build

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend ./
RUN npm run build


# ---------------------------------------------------------------------------
# Stage 2: Backend runtime — FastAPI served from a uv-managed venv
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# uv binary (backend is a uv project per PLAN.md §3/§11).
# Pinned version — floating :latest makes builds non-reproducible and exposes
# the image to upstream tag compromise.
COPY --from=ghcr.io/astral-sh/uv:0.9.28 /uv /uvx /usr/local/bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/app/db/finally.db \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/app/backend/.venv/bin:${PATH}"

WORKDIR /app/backend

# Non-root user. /app/db must be owned by `app` BEFORE the volume is first
# mounted so the named volume (finally-data:/app/db) inherits writable
# ownership for the SQLite file.
RUN useradd --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/db \
    && chown app:app /app/db

# Dependency layer — cached unless the lockfile changes
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Application code + static frontend export
COPY backend ./
COPY --from=frontend-build /app/frontend/out ./static
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

USER app

EXPOSE 8000

# curl is not installed in python:slim — probe /api/health with stdlib urllib.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).read()" || exit 1

CMD ["/app/backend/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
