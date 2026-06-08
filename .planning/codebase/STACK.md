# Technology Stack

**Analysis Date:** 2026-06-04

## Languages

**Primary:**
- Python 3.12+ — backend server, market data, business logic (`backend/`)
- TypeScript — frontend UI (planned; Next.js static export in `frontend/`, not yet scaffolded)

**Secondary:**
- SQL — SQLite schema definitions (`backend/db/`, planned)

## Runtime

**Environment:**
- Python 3.12 (minimum; enforced in `backend/pyproject.toml` via `requires-python = ">=3.12"`)
- Node.js 20 (planned for frontend build stage in Dockerfile)

**Package Manager:**
- Backend: `uv` — fast Python package manager with reproducible lockfile
  - Lockfile: `backend/uv.lock` (committed)
  - Project manifest: `backend/pyproject.toml`
- Frontend: `npm` (planned)

## Frameworks

**Core:**
- FastAPI `>=0.115.0` — async HTTP server, SSE streaming, REST API (`backend/`)
- Uvicorn `>=0.32.0` with `[standard]` extras — ASGI server running FastAPI
- Next.js — frontend framework, static export mode (`output: 'export'`; planned, not yet built)

**Testing:**
- pytest `>=8.3.0` — backend test runner
- pytest-asyncio `>=0.24.0` — async test support; `asyncio_mode = "auto"` configured
- pytest-cov `>=5.0.0` — coverage reporting
- httpx `>=0.27.0` — async HTTP client for API route testing (uses `ASGITransport`)
- React Testing Library — frontend component tests (planned)
- Playwright — E2E tests in `test/` (planned)

**Build/Dev:**
- Ruff `>=0.7.0` — Python linter and formatter; `line-length = 100`, target Python 3.12
- Hatchling — build backend for the Python package (`[build-system]` in `pyproject.toml`)
- Tailwind CSS — frontend styling (planned)

## Key Dependencies

**Critical:**
- `fastapi>=0.115.0` — core web framework; all API routes and SSE endpoint (`backend/app/market/stream.py`)
- `uvicorn[standard]>=0.32.0` — production ASGI server; the `[standard]` extra adds websockets + performance deps
- `numpy>=2.0.0` — Geometric Brownian Motion math; Cholesky decomposition for correlated price simulation (`backend/app/market/simulator.py`)
- `massive>=1.0.0` — Polygon.io REST API client for real market data (`backend/app/market/massive_client.py`)
- `rich>=13.0.0` — terminal dashboard for `market_data_demo.py` demo script
- `litellm` — LLM API abstraction layer via OpenRouter (required per cerebras skill; not yet in `pyproject.toml` — must be added when LLM chat is implemented)
- `pydantic` — structured outputs and data validation (required per cerebras skill; not yet in `pyproject.toml`)

**Infrastructure:**
- SQLite — built-in Python stdlib `sqlite3`; no ORM, no extra dependency; database file at `db/finally.db` (runtime only, gitignored)
- Python `threading.Lock` — used in `PriceCache` for thread-safety (`backend/app/market/cache.py`)
- Python `asyncio` — background task loop for market data simulation and SSE streaming

## Configuration

**Environment:**
- `.env` file at project root (gitignored); read by backend at startup
- Required: `OPENROUTER_API_KEY` — OpenRouter API key for LLM chat
- Optional: `MASSIVE_API_KEY` — Polygon.io key; if absent, GBM simulator is used
- Optional: `LLM_MOCK=true` — returns deterministic mock LLM responses (for E2E tests)
- `.env.example` referenced in README but not yet present in repo

**Build:**
- `backend/pyproject.toml` — Python project definition, dependencies, pytest config, ruff config, coverage config
- `backend/uv.lock` — reproducible dependency lockfile
- `Dockerfile` — multi-stage build (Node 20 → Python 3.12); defined in PLAN.md, not yet created
- `docker-compose.yml` — optional convenience wrapper; not yet created

## Platform Requirements

**Development:**
- Python 3.12+
- `uv` installed globally
- `OPENROUTER_API_KEY` in `.env`

**Production:**
- Docker (single container on port 8000)
- Named Docker volume `finally-data` mounted to `/app/db` for SQLite persistence
- `--env-file .env` passed to `docker run`

---

*Stack analysis: 2026-06-04*
