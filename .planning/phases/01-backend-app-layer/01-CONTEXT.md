# Phase 1: Backend App Layer - Context

**Gathered:** 2026-06-05
**Status:** Ready for planning

<domain>
## Phase Boundary

Build the complete FastAPI application layer on top of the existing `backend/app/market/` subsystem: the app entry point (`main.py`) with lifespan management, SQLite database with lazy initialization and seed data, all REST API endpoints (health, portfolio, watchlist), and a background portfolio snapshot task. The market data SSE endpoint already exists — this phase wires it into the app and adds everything else.

</domain>

<decisions>
## Implementation Decisions

### Code Organization
- Route files in `backend/app/routes/` folder: `health.py`, `portfolio.py`, `watchlist.py` — mirrors market/ subsystem pattern
- Database layer in `backend/app/db/` package: `schema.sql`, `connection.py`, `seed.py`
- Each router uses `create_*` factory pattern, injecting shared `price_cache` and `db_path`
- Static files served via `StaticFiles` middleware pointing to `backend/static/` directory

### Database Access Pattern
- Per-request `sqlite3.connect(db_path)` with `check_same_thread=False` — simple, no pooling needed for SQLite
- `conn.row_factory = sqlite3.Row` for dict-like row access and easy dict serialization
- Schema initialization on FastAPI startup lifespan (before accepting requests)
- Enable WAL mode (`PRAGMA journal_mode=WAL`) for concurrent reads

### API Design Details
- `GET /api/portfolio` returns: `{cash, total_value, positions: [{ticker, qty, avg_cost, current_price, unrealized_pnl, pnl_pct}]}` — all in one call
- Trade validation errors: HTTP 400 with `{"error": "message"}` JSON body
- Portfolio snapshot background task: `asyncio` task in lifespan, 30-second interval
- Record portfolio snapshot immediately after each trade execution (in addition to periodic)

### Claude's Discretion
- Exact SQL schema column names and types (follow PLAN.md schema as spec)
- Error handling detail (log level, whether to surface DB errors as 500 vs suppress)
- Whether to use Pydantic models for request/response bodies (yes, use them for type safety)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `PriceCache` from `backend/app/market/cache.py` — `get(ticker)`, `get_all()`, `get_price(ticker)` methods
- `create_market_data_source(cache)` from `backend/app/market/factory.py` — returns simulator or Massive client
- `create_stream_router(price_cache)` from `backend/app/market/stream.py` — returns configured APIRouter for SSE
- `SEED_PRICES` from `backend/app/market/seed_prices.py` — the 10 default tickers for watchlist seeding
- `httpx.ASGITransport` pattern from `backend/tests/market/test_stream.py` — use for route integration tests

### Established Patterns
- `create_*` factory functions for building routers with injected dependencies
- `from __future__ import annotations` + full type annotations on all signatures
- `logger = logging.getLogger(__name__)` per-module loggers
- Private methods with `_` prefix; `asyncio.CancelledError` handled in stop()/cleanup paths
- Background async tasks: `asyncio.create_task()` + explicit cancel/await in lifespan cleanup
- Pydantic already in the dependency chain via FastAPI

### Integration Points
- `main.py` lifespan: create `PriceCache`, create market source, `await source.start(tickers)`, register routers
- SSE router already exists: `include_router(create_stream_router(price_cache))`
- DB path from environment or default: `os.getenv("DB_PATH", "db/finally.db")`
- Static files: `app.mount("/", StaticFiles(directory="static", html=True), name="static")` — must be LAST mount

</code_context>

<specifics>
## Specific Ideas

- DB schema exactly as specified in `planning/PLAN.md § 7` — all 6 tables with `user_id` columns defaulting to `"default"`
- Default seed data: `users_profile` with `id="default"`, `cash_balance=10000.0`; watchlist with 10 tickers from `SEED_PRICES`
- Portfolio total value = cash + sum(position.quantity × current_price)
- Unrealized P&L per position = (current_price - avg_cost) × quantity
- `GET /api/watchlist` should join watchlist with price cache (no DB query for prices — use in-memory cache)

</specifics>

<deferred>
## Deferred Ideas

- LLM chat endpoint (`POST /api/chat`) — Phase 2
- Frontend serving (the static/ directory will be empty in Phase 1; StaticFiles mount will be there but unused)

</deferred>
