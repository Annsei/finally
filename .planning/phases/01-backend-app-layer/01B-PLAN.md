---
plan: 01B
title: FastAPI App Entry Point & Health Endpoint
wave: 1
depends_on: [01A]
phase: 1
requirements_addressed: [BACK-01, BACK-03, BACK-11]
files_modified:
  - backend/app/main.py
  - backend/app/__init__.py
  - backend/pyproject.toml
autonomous: true
---

# Plan 01B: FastAPI App Entry Point & Health Endpoint

## Objective

Create `backend/app/main.py` — the FastAPI application with lifespan management, router registration, static file serving, and health endpoint. This wires `PriceCache`, market data source, DB init, and all routers into a single running application.

## Tasks

<task id="01B-1">
<title>Add missing Python dependencies to pyproject.toml</title>

<read_first>
- backend/pyproject.toml (current dependencies)
- planning/PLAN.md §3 (tech stack decisions)
- .planning/phases/01-backend-app-layer/01-CONTEXT.md
</read_first>

<action>
Add to `[project].dependencies` in `backend/pyproject.toml`:
- `"litellm>=1.0.0"` — for Phase 2 LLM integration (add now so Docker build installs it)
- `"aiofiles>=23.0.0"` — for StaticFiles async file serving with FastAPI

Run `uv add litellm aiofiles` from `backend/` to add and lock the dependencies.

Note: `fastapi`, `uvicorn[standard]` are already present.
</action>

<acceptance_criteria>
- `backend/pyproject.toml` contains `litellm` in `[project].dependencies`
- `backend/pyproject.toml` contains `aiofiles` in `[project].dependencies`
- `uv sync` from `backend/` exits 0 (lockfile updates cleanly)
</acceptance_criteria>
</task>

<task id="01B-2">
<title>Create backend/app/main.py with FastAPI lifespan and routers</title>

<read_first>
- backend/app/market/__init__.py (exports: PriceCache, create_market_data_source, create_stream_router)
- backend/app/market/stream.py (create_stream_router pattern — factory returning APIRouter)
- backend/app/db/connection.py (init_db function created in 01A-1)
- backend/app/market/seed_prices.py (SEED_PRICES — initial tickers to start market data)
- planning/PLAN.md §3, §6, §8 (architecture, SSE, API endpoints spec)
- .planning/phases/01-backend-app-layer/01-CONTEXT.md
</read_first>

<action>
Create `backend/app/main.py`:

1. Import: `from __future__ import annotations`, contextlib, fastapi, fastapi.staticfiles, logging, os, pathlib, app.db.connection (init_db), app.market (PriceCache, create_market_data_source, create_stream_router), app.market.seed_prices (SEED_PRICES)

2. `lifespan` async context manager (`@asynccontextmanager`):
   - Create `price_cache = PriceCache()`
   - Call `init_db()` — initializes SQLite schema and seed data
   - Create `source = create_market_data_source(price_cache)`
   - `await source.start(list(SEED_PRICES.keys()))` — start with 10 default tickers
   - Store both on `app.state`: `app.state.price_cache = price_cache`, `app.state.market_source = source`
   - `yield`
   - `await source.stop()`
   - logger.info lifecycle events

3. Create `app = FastAPI(title="FinAlly", lifespan=lifespan)`

4. Include routers (after creating them):
   - `app.include_router(create_stream_router(price_cache))` — SSE at /api/stream/prices
   - Health router (from routes/health.py — created in 01B-3)
   - Portfolio router (wave 2 — placeholder comment)
   - Watchlist router (wave 2 — placeholder comment)

5. Mount static files LAST:
   ```python
   static_dir = Path(__file__).parent.parent / "static"
   if static_dir.exists():
       app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
   ```

6. Module-level: `logger = logging.getLogger(__name__)`

Note: the lifespan must create price_cache before routers — routers take it as a dependency. Use a module-level `_price_cache: PriceCache | None = None` and set it in lifespan, OR use `app.state`. Use `app.state` approach (simpler, no globals).

Update routers registered in main.py to read price_cache from `request.app.state.price_cache` instead of a closure — OR keep the factory pattern and set it before yield. The factory pattern (passing price_cache to create_stream_router) is the established pattern; keep it.

**Important:** The lifespan runs BEFORE the first request, but routers are included at import time (before lifespan runs). For routers that need price_cache, use the factory pattern: create price_cache inside lifespan, pass to create_*_router factories, include the returned routers. To do this, include routers inside lifespan before yield using `app.include_router(...)`.
</action>

<acceptance_criteria>
- `backend/app/main.py` exists and contains `app = FastAPI(`
- Contains `@asynccontextmanager` lifespan function
- Contains `app.state.price_cache` assignment
- Contains `create_stream_router` import and call
- Contains `init_db()` call inside lifespan
- Contains `StaticFiles` mount (conditional on static dir existing)
- `from app.main import app` in Python 3.12 exits without ImportError
</acceptance_criteria>
</task>

<task id="01B-3">
<title>Create routes/health.py and register in main.py</title>

<read_first>
- backend/app/market/stream.py (APIRouter factory pattern)
- backend/app/main.py (just created — where to add include_router call)
- planning/PLAN.md §8 (GET /api/health endpoint spec)
</read_first>

<action>
Create `backend/app/routes/__init__.py` (empty, package marker).

Create `backend/app/routes/health.py`:
```
router = APIRouter(prefix="/api", tags=["system"])

@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
```

Add `from app.routes.health import router as health_router` and `app.include_router(health_router)` in `backend/app/main.py` — include this outside lifespan since it has no dependencies.
</action>

<acceptance_criteria>
- `backend/app/routes/health.py` exists with `@router.get("/health")`
- `GET /api/health` returns `{"status": "ok"}` with HTTP 200
- `curl -s http://localhost:8000/api/health` returns `{"status":"ok"}` when server is running
</acceptance_criteria>
</task>

<task id="01B-4">
<title>Add uvicorn entry point and verify app imports cleanly</title>

<read_first>
- backend/pyproject.toml (scripts section)
- backend/app/main.py (just created)
</read_first>

<action>
Add to `backend/pyproject.toml` under `[project.scripts]`:
```
finally-server = "app.main:app"
```

Create `backend/run.py` (optional dev runner):
```python
import uvicorn
uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
```

Verify the app imports cleanly by running:
```bash
cd backend && uv run python -c "from app.main import app; print('OK')"
```
</action>

<acceptance_criteria>
- `cd backend && uv run python -c "from app.main import app; print('OK')"` prints `OK`
- No ImportError from any of the db or market imports
- `cd backend && uv run uvicorn app.main:app --port 8000` starts without error
</acceptance_criteria>
</task>

## Verification

- `GET /api/health` returns `{"status": "ok"}` with HTTP 200
- Server starts with `uv run uvicorn app.main:app` without errors
- SQLite `db/finally.db` is created on server start if it doesn't exist
- `GET /api/stream/prices` returns SSE events (from existing market subsystem)

## Must Haves

- [ ] `app = FastAPI(...)` with proper lifespan (start/stop market source)
- [ ] `init_db()` called during lifespan startup
- [ ] SSE stream router registered and functional
- [ ] `GET /api/health` returns 200 `{"status": "ok"}`
- [ ] StaticFiles mounted LAST (no interference with API routes)
- [ ] Import of `app.main:app` is clean (no startup side effects at import time)
