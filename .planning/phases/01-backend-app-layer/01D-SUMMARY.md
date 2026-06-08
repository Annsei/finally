---
plan: 01D
phase: 1
subsystem: backend
tags: [watchlist, api, rest, sqlite, price-cache]
dependency_graph:
  requires: [01A, 01B, 01C]
  provides: [BACK-07, BACK-08, BACK-09]
  affects: [frontend-watchlist-panel, chat-watchlist-actions]
tech_stack:
  added: []
  patterns: [factory-router, insert-or-ignore-idempotent, price-cache-enrichment]
key_files:
  created:
    - backend/app/routes/watchlist.py
  modified:
    - backend/app/main.py
decisions:
  - Watchlist GET always reads live prices from PriceCache (not stored in DB), returning null for tickers not yet cached
  - INSERT OR IGNORE used for idempotent add; DELETE without 404 check for idempotent remove
  - Market data source started with DB watchlist tickers on startup, falling back to SEED_PRICES if watchlist is empty
  - Ticker validation: uppercase normalization + max 10 chars + non-empty check (400 on violation)
metrics:
  duration: ~8 minutes
  completed: 2026-06-05T07:41:28Z
  tasks_completed: 2
  files_created: 1
  files_modified: 1
---

# Phase 1 Plan D: Watchlist API Summary

## One-liner

Watchlist CRUD API (GET/POST/DELETE /api/watchlist) with live PriceCache enrichment and DB-driven market source startup.

## What Was Built

### Task 01D-1: routes/watchlist.py

Created `backend/app/routes/watchlist.py` using the same `create_watchlist_router(price_cache, db_path) -> APIRouter` factory pattern established by `portfolio.py`.

Three endpoints under `prefix="/api/watchlist"`:

- **GET /**: Queries `watchlist` table ordered by `added_at ASC`, enriches each row with `price_cache.get(ticker)` — returns `price`, `change_percent`, and `direction` as `null` when ticker not yet in cache.
- **POST /**: Accepts `AddTickerRequest(ticker: str)`, normalizes to uppercase, validates non-empty and max 10 chars (400 on failure), then `INSERT OR IGNORE` for idempotent add. Returns `{"status": "ok", "ticker": "..."}`.
- **DELETE /{ticker}**: Normalizes ticker to uppercase, runs `DELETE FROM watchlist WHERE user_id='default' AND ticker=?`, returns 200 regardless of whether the row existed (idempotent).

All error paths return HTTP 400 with `{"error": "message"}` via `JSONResponse`, consistent with `portfolio.py`.

### Task 01D-2: main.py updates

Two changes to `backend/app/main.py` inside the `lifespan` context manager:

1. **DB-driven market source start**: Replaced the hardcoded `list(SEED_PRICES.keys())` with a DB query (`SELECT ticker FROM watchlist WHERE user_id='default'`), so if the user has a customized watchlist and restarts, the simulator runs exactly their tickers. Falls back to `SEED_PRICES.keys()` if the watchlist is empty.

2. **Watchlist router registration**: Added `create_watchlist_router(price_cache, db_path)` and `app.include_router(watchlist_router)` alongside the existing portfolio router registration.

## Deviations from Plan

None — plan executed exactly as written.

## Commits

| Task  | Hash    | Message |
|-------|---------|---------|
| 01D-1 | 58920b8 | feat(01D-1): create watchlist API router with GET, POST, DELETE endpoints |
| 01D-2 | 15086cc | feat(01D-2): register watchlist router and use DB tickers for market source start |

## Self-Check: PASSED

- `backend/app/routes/watchlist.py` exists and contains `def create_watchlist_router(`
- `backend/app/main.py` contains `app.include_router(watchlist_router)`
- Commit 58920b8 verified in git log
- Commit 15086cc verified in git log
- Both files pass Python AST syntax check
