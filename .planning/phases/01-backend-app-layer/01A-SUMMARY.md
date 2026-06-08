---
phase: "01"
plan: "01A"
subsystem: backend-db
tags: [sqlite, database, schema, seed]
dependency_graph:
  requires: []
  provides: [db-init, db-connection, db-schema, db-seed]
  affects: [backend-routes, portfolio-api, watchlist-api]
tech_stack:
  added: [sqlite3-wal-mode]
  patterns: [per-request-connection, idempotent-init, insert-or-ignore]
key_files:
  created:
    - backend/app/db/__init__.py
    - backend/app/db/connection.py
    - backend/app/db/schema.sql
    - backend/app/db/seed.py
  modified: []
decisions:
  - "Per-request sqlite3.connect() with check_same_thread=False — no pooling needed for SQLite single-file DB"
  - "WAL mode enabled via PRAGMA for concurrent read throughput alongside SSE streaming"
  - "Local import of seed_db inside init_db to avoid circular import at module level"
  - "datetime.now(timezone.utc) used in seed.py (aware datetime) rather than deprecated datetime.utcnow()"
metrics:
  duration_minutes: 5
  completed_date: "2026-06-05"
  tasks_completed: 3
  files_created: 4
  files_modified: 0
---

# Phase 1 Plan A: Database Foundation Summary

## One-liner

SQLite database package with WAL-mode connection utility, 6-table schema, and idempotent seed loader for default user and 10-ticker watchlist.

## What Was Built

Created the `backend/app/db/` package — the persistence foundation for all API routes in subsequent plans.

**`connection.py`** provides:
- `DB_PATH` — reads from `DB_PATH` env var, falls back to `"db/finally.db"`
- `get_conn(db_path)` — opens SQLite connection with `row_factory=sqlite3.Row`, `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`
- `init_db(db_path)` — reads `schema.sql`, runs `executescript`, seeds if `users_profile` is empty; fully idempotent

**`schema.sql`** defines 6 tables with `CREATE TABLE IF NOT EXISTS`:
- `users_profile` — cash balance for the default user
- `watchlist` — UNIQUE(user_id, ticker)
- `positions` — UNIQUE(user_id, ticker), avg_cost tracking
- `trades` — append-only order log
- `portfolio_snapshots` — total_value over time for P&L chart
- `chat_messages` — LLM conversation history with actions JSON column

**`seed.py`** inserts:
- Default user: `id="default"`, `cash_balance=10000.0`
- 10 watchlist rows from `SEED_PRICES.keys()` (AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX)
- All via `INSERT OR IGNORE` for idempotency

## Task Commits

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 01A-1 | Create db/ package with connection utilities | 904fddb | backend/app/db/__init__.py, backend/app/db/connection.py |
| 01A-2 | Write schema.sql with all 6 tables | c1a489a | backend/app/db/schema.sql |
| 01A-3 | Write seed.py with default user and watchlist | 2dd9a37 | backend/app/db/seed.py |

## Verification Results

All plan verification criteria passed:
- `python -c "from app.db import init_db; init_db('/tmp/finally_test_01A.db')"` exits 0
- `/tmp/finally_test_01A.db` contains exactly 6 tables after `init_db`
- `users_profile` row: `id='default'`, `cash_balance=10000.0`
- 10 rows in `watchlist` for `user_id='default'`
- Second call to `init_db` on the same DB produces no duplicates (idempotency confirmed)

## Deviations from Plan

### Auto-applied Improvements

**1. [Rule 2 - Missing Critical] Used timezone-aware datetime in seed.py**
- **Found during:** Task 01A-3
- **Issue:** Plan spec referenced `datetime.utcnow()` which is deprecated in Python 3.12+
- **Fix:** Used `datetime.now(timezone.utc)` instead — produces the same ISO string, avoids deprecation warning
- **Files modified:** backend/app/db/seed.py

None beyond the above minor improvement.

## Known Stubs

None — this plan creates pure data-layer infrastructure with no UI rendering paths.

## Threat Flags

None — no new network endpoints introduced. Database file is local SQLite, not network-accessible.

## Self-Check: PASSED

- [x] backend/app/db/__init__.py exists
- [x] backend/app/db/connection.py exists
- [x] backend/app/db/schema.sql exists
- [x] backend/app/db/seed.py exists
- [x] Commit 904fddb exists (01A-1)
- [x] Commit c1a489a exists (01A-2)
- [x] Commit 2dd9a37 exists (01A-3)
- [x] All 6 tables created and verified at runtime
- [x] Seed data verified: default user + 10 watchlist tickers
