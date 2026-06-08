---
phase: 1
plan: 01C
subsystem: backend-portfolio-api
tags: [fastapi, sqlite, portfolio, trading, background-task]
dependency_graph:
  requires: [01A, 01B]
  provides: [portfolio-api, trade-execution, portfolio-history, snapshot-task]
  affects: [01D, 01E, frontend-portfolio]
tech_stack:
  added: []
  patterns: [factory-router, jsonresponse-errors, weighted-avg-cost-upsert, asyncio-background-task]
key_files:
  created:
    - backend/app/routes/portfolio.py
  modified:
    - backend/app/main.py
decisions:
  - Use JSONResponse directly for 400 errors to produce {"error":"..."} body (not HTTPException {"detail":...})
  - Close DB connection before returning early error JSONResponse inside trade handler
  - _record_snapshot commits its own transaction; trade handler commits before calling it
  - Background snapshot loop re-raises CancelledError, logs all other exceptions and continues
  - db_path sourced from os.getenv("DB_PATH","db/finally.db") in lifespan; passed through to router factory
metrics:
  duration: ~8 minutes
  completed: 2026-06-05T07:37:48Z
  tasks_completed: 5
  files_changed: 2
---

# Phase 1 Plan 01C: Portfolio API Summary

## One-liner

Three portfolio REST endpoints (GET /portfolio, POST /portfolio/trade, GET /portfolio/history) plus a 30-second background snapshot task wired into the FastAPI lifespan.

## What Was Built

### backend/app/routes/portfolio.py (created)

Factory function `create_portfolio_router(price_cache, db_path)` returns an `APIRouter` at prefix `/api/portfolio` with three endpoints:

- `GET /` — fetches cash balance and all positions from SQLite, enriches each position with live price from `PriceCache`, computes `unrealized_pnl` and `pnl_pct`, returns `{cash, total_value, positions:[...]}`.
- `POST /trade` — validates `TradeRequest` (ticker, quantity, side), executes market order atomically in SQLite (weighted avg cost upsert for buys, quantity reduce/delete for sells), records a portfolio snapshot immediately after each trade.
- `GET /history` — returns up to 500 portfolio snapshots ordered ascending by `recorded_at`.

Helper `_record_snapshot(conn, price_cache)` computes total value from live prices and inserts a `portfolio_snapshots` row. Used both by the trade endpoint and the background loop.

### backend/app/main.py (modified)

- Added `_snapshot_loop(price_cache, db_path, interval=30)` async coroutine that calls `_record_snapshot` every 30 seconds with full error recovery (logs exceptions, re-raises `CancelledError`).
- Lifespan creates the snapshot task via `asyncio.create_task`, cancels it cleanly on shutdown.
- Registers `create_portfolio_router` inside lifespan with `price_cache` and `db_path` from env.

## Verification Results

All smoke tests passed:
- `GET /api/portfolio` on fresh DB returns `{cash: 10000.0, total_value: 10000.0, positions: []}`
- `POST /api/portfolio/trade` buy deducts cash, creates position with correct avg_cost
- Insufficient cash buy returns HTTP 400 `{"error": "Insufficient cash"}`
- Unknown ticker returns HTTP 400 `{"error": "Ticker not found in price cache"}`
- Oversell returns HTTP 400 `{"error": "Insufficient shares to sell"}`
- `GET /api/portfolio/history` returns snapshots with `total_value` and `recorded_at` keys
- 73/73 existing backend tests still pass

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 01C-1,2,3 | e817d97 | feat(01C-1): create routes/portfolio.py with GET /api/portfolio |
| 01C-4,5 | 757bf23 | feat(01C-4): add background portfolio snapshot loop to main.py |
| fix | cab745a | fix(01C-2): use JSONResponse for trade errors so body is {"error":"..."} |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Error response format did not match plan spec**
- **Found during:** Task 01C-2 smoke test
- **Issue:** `HTTPException(status_code=400, detail={"error":"..."})` produces `{"detail":{"error":"..."}}` — FastAPI wraps the detail. Plan specifies `{"error":"message"}` at the top level.
- **Fix:** Replaced all `raise HTTPException` in trade handler with `return JSONResponse(status_code=400, content={"error":"..."})`. Connections are closed explicitly before early returns.
- **Files modified:** backend/app/routes/portfolio.py
- **Commit:** cab745a

## Known Stubs

None — all endpoints return live data from SQLite and PriceCache.

## Threat Flags

None — no new network endpoints beyond the plan spec; all inputs validated before DB access.

## Self-Check: PASSED

- [x] backend/app/routes/portfolio.py exists
- [x] backend/app/main.py contains asyncio.create_task for snapshot loop
- [x] Commits e817d97, 757bf23, cab745a exist in git log
- [x] All smoke tests pass with correct response shapes
