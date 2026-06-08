---
phase: 02-llm-chat-integration
plan: "01"
subsystem: backend
tags:
  - refactor
  - portfolio
  - watchlist
  - tdd
  - helper-extraction
dependency_graph:
  requires:
    - "01-05 (portfolio routes)"
    - "01-03 (watchlist routes)"
  provides:
    - "execute_trade_on_conn (callable from chat.py)"
    - "apply_watchlist_change_on_conn (callable from chat.py)"
  affects:
    - "02-02 (LLM chat route — imports these helpers)"
tech_stack:
  added: []
  patterns:
    - "Connection-level helper pattern: module-level functions accept open conn, never open their own"
    - "TDD RED/GREEN cycle for each helper"
    - "HTTP route as thin wrapper: call helper, map failure dict to 400 JSONResponse"
key_files:
  created:
    - "backend/tests/test_execute_trade_on_conn.py"
    - "backend/tests/test_apply_watchlist_change_on_conn.py"
  modified:
    - "backend/app/routes/portfolio.py"
    - "backend/app/routes/watchlist.py"
decisions:
  - "Helper functions never open DB connections — caller manages lifecycle (Research Pattern 4)"
  - "Failure paths return dicts not raise — loop callers in chat.py collect outcomes cleanly"
  - "HTTP routes left as thin wrappers mapping helper outcomes to HTTP responses (status=ok for success)"
  - "apply_watchlist_change_on_conn does NOT call add_ticker/remove_ticker HTTP routes — DB-only per CONTEXT.md Pitfall 6"
  - "Existing HTTP route responses (status=ok) unchanged — no breaking change to frontend"
metrics:
  duration: "~8 minutes"
  completed: "2026-06-05"
  tasks_completed: 2
  files_modified: 4
  tests_added: 30
---

# Phase 2 Plan 1: Refactor Trade and Watchlist Logic as Connection-Level Helpers Summary

Extracted trade execution and watchlist mutation logic from HTTP route handlers into reusable module-level helper functions, enabling the upcoming LLM chat route to call them directly without circular HTTP round-trips.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Extract execute_trade_on_conn | 7cf3841 | backend/app/routes/portfolio.py, backend/tests/test_execute_trade_on_conn.py |
| 2 | Extract apply_watchlist_change_on_conn | ae18389 | backend/app/routes/watchlist.py, backend/tests/test_apply_watchlist_change_on_conn.py |

## What Was Built

**`execute_trade_on_conn(conn, price_cache, ticker, side, quantity) -> dict`** — module-level helper in `portfolio.py` that:
- Normalizes `ticker.upper()` and `side.lower()` (satisfies T-02-01 threat mitigation)
- Uses parameterized SQL throughout (satisfies T-02-02 threat mitigation)
- Returns `{"status": "failed", "ticker", "error"}` for all validation failures — never raises
- Returns `{"status": "executed", "ticker", "side", "quantity", "price", "trade_id"}` on success
- Calls `_record_snapshot` after successful trade commit
- The HTTP `execute_trade` route refactored to a thin wrapper: calls helper, maps `failed` → `JSONResponse(400)`, `executed` → `{"status": "ok", ...}`

**`apply_watchlist_change_on_conn(conn, ticker, action) -> dict`** — module-level helper in `watchlist.py` that:
- Normalizes `ticker.strip().upper()` and `action.lower()` (satisfies T-02-03 threat mitigation)
- Returns `{"status": "failed", ...}` for empty ticker or invalid action
- Returns `{"status": "added", "ticker", "action": "add"}` on INSERT OR IGNORE (idempotent)
- Returns `{"status": "removed", "ticker", "action": "remove"}` on DELETE (idempotent)
- Existing `add_ticker` and `remove_ticker` HTTP handlers unchanged

## Test Coverage

- 14 tests in `test_execute_trade_on_conn.py`: import/signature, 5 failure paths, 5 success paths
- 16 tests in `test_apply_watchlist_change_on_conn.py`: import/signature, 4 failure paths, 5 add paths, 4 remove paths
- All 14 existing portfolio and watchlist HTTP tests still pass (zero regressions)

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — this plan adds pure logic helpers with no UI-facing data or placeholder values.

## Threat Flags

None — no new network endpoints, auth paths, or trust boundaries introduced. All changes are internal Python function calls.

## Self-Check: PASSED

All created/modified files verified present. All 4 task commits verified in git log.

| Check | Result |
|-------|--------|
| backend/app/routes/portfolio.py exists | FOUND |
| backend/app/routes/watchlist.py exists | FOUND |
| backend/tests/test_execute_trade_on_conn.py exists | FOUND |
| backend/tests/test_apply_watchlist_change_on_conn.py exists | FOUND |
| Commit 80a7c74 (test RED execute_trade_on_conn) | FOUND |
| Commit 7cf3841 (feat execute_trade_on_conn) | FOUND |
| Commit 1d4fdbc (test RED apply_watchlist_change_on_conn) | FOUND |
| Commit ae18389 (feat apply_watchlist_change_on_conn) | FOUND |
