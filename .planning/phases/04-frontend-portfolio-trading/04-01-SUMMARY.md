---
phase: 04-frontend-portfolio-trading
plan: 01
subsystem: contracts
tags: [backend, frontend, types, api, tdd]
requirements: [FE-14, FE-15]

dependency_graph:
  requires: []
  provides:
    - GET /api/chat/ history endpoint (backend)
    - Phase 4 frontend TypeScript types (frontend/src/types/market.ts)
  affects:
    - frontend/src/types/market.ts (consumers in Wave 2 plans)
    - backend/tests/test_chat.py (9 tests passing)

tech_stack:
  added: []
  patterns:
    - FastAPI router GET handler inside factory function (create_chat_router)
    - DB connection try/finally pattern (from portfolio.py)
    - TDD RED/GREEN cycle for backend endpoint

key_files:
  created: []
  modified:
    - backend/app/routes/chat.py
    - backend/tests/test_chat.py
    - frontend/src/types/market.ts

decisions:
  - "Placed GET /api/chat/ handler BEFORE POST /api/chat/ inside factory to avoid route shadowing"
  - "Used ORDER BY created_at DESC LIMIT 20 then list(reversed(...)) for ascending output"
  - "Trailing slash /api/chat/ consistent with existing POST /api/chat/ convention"
  - "actions field parsed from JSON string to dict at read time (not stored pre-parsed)"

metrics:
  duration: "~8 minutes"
  completed: "2026-06-07"
  tasks_completed: 2
  files_modified: 3
  tests_added: 1
  tests_passing: 9
---

# Phase 04 Plan 01: Interface Contracts Summary

**One-liner:** GET /api/chat/ history endpoint returning last 20 messages ascending by created_at, plus seven new TypeScript interfaces for portfolio history and chat types.

## What Was Built

### Task 1: GET /api/chat/ history endpoint (TDD)

Added `get_chat_history` async handler registered with `@router.get("/")` inside the existing `create_chat_router(price_cache, db_path)` factory in `backend/app/routes/chat.py`. The handler:

- Queries `chat_messages` with `ORDER BY created_at DESC LIMIT 20`
- Reverses the result list for ascending chronological order
- Parses `actions` JSON strings to dicts (or None for user messages)
- Uses `get_conn(db_path)` with `try/finally: conn.close()` pattern — zero new imports

Test `test_get_chat_history` added to `TestChat` in `backend/tests/test_chat.py`:
- Seeds the DB via POST /api/chat/, then GETs history
- Asserts 200 status, `messages` key, list type
- Validates ascending timestamp order, key presence, and actions as dict (not string)

TDD gates:
- RED commit `873474c` — test fails with 405 (no GET handler)
- GREEN commit `08bb6da` — test passes, all 9 chat tests green

### Task 2: Phase 4 frontend TypeScript types

Appended seven new exported interfaces to `frontend/src/types/market.ts` without altering any existing export:

| Interface | Purpose |
|-----------|---------|
| `PortfolioSnapshot` | Single snapshot row from GET /api/portfolio/history |
| `PortfolioHistoryResponse` | Wrapper with `snapshots: PortfolioSnapshot[]` |
| `TradeOutcome` | Trade execution result (executed/failed) from POST trade or chat |
| `WatchlistOutcome` | Watchlist change result from POST /api/chat |
| `ChatMessage` | Individual message with parsed `actions` or null |
| `ChatHistoryResponse` | Wrapper with `messages: ChatMessage[]` from GET /api/chat/ |
| `ChatPostResponse` | Full response shape from POST /api/chat |

TypeScript compiles with zero new errors (4 pre-existing errors in `usePriceStream.test.tsx` were present before this plan and are out of scope).

## Verification

- `cd backend && uv run --extra dev pytest tests/test_chat.py -v` — 9/9 passed
- `tsc --noEmit` — zero new errors introduced; new types compile cleanly
- Acceptance criteria checklist:
  - [x] `backend/app/routes/chat.py` contains `async def get_chat_history`
  - [x] `backend/app/routes/chat.py` contains `@router.get("/")` inside create_chat_router
  - [x] `backend/app/routes/chat.py` contains `FROM chat_messages`, `ORDER BY created_at DESC`, `LIMIT 20`
  - [x] `backend/tests/test_chat.py` contains `test_get_chat_history`
  - [x] Test exits 0
  - [x] GET /api/chat/ returns 200 with `messages` array
  - [x] `frontend/src/types/market.ts` contains `export interface PortfolioHistoryResponse`
  - [x] `frontend/src/types/market.ts` contains `export interface ChatHistoryResponse`
  - [x] `frontend/src/types/market.ts` contains `export interface ChatPostResponse`
  - [x] `frontend/src/types/market.ts` contains `export interface TradeOutcome`
  - [x] Existing exports (`PortfolioResponse`, `DEFAULT_TICKERS`) preserved
  - [x] TypeScript compiles with no new errors

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — this plan adds contracts only, no UI or data-fetching stub.

## Threat Surface Scan

No new network endpoints beyond GET /api/chat/ (which was the planned addition). The query uses a literal `user_id = 'default'` with no user-supplied input interpolated into SQL — T-4-SQL mitigation confirmed present as required by the threat register.

## TDD Gate Compliance

- RED gate: `test(04-01)` commit `873474c` — test_get_chat_history fails with 405
- GREEN gate: `feat(04-01)` commit `08bb6da` — test_get_chat_history passes
- REFACTOR gate: not required (implementation was minimal and clean on first pass)

## Commits

| Hash | Type | Description |
|------|------|-------------|
| 873474c | test | add failing test for GET /api/chat/ history endpoint (RED) |
| 08bb6da | feat | implement GET /api/chat/ history endpoint (GREEN) |
| 5e6fe2e | feat | add Phase 4 frontend TypeScript types to market.ts |

## Self-Check: PASSED

- backend/app/routes/chat.py: FOUND
- backend/tests/test_chat.py: FOUND
- frontend/src/types/market.ts: FOUND
- 04-01-SUMMARY.md: FOUND
- get_chat_history handler: FOUND
- test_get_chat_history: FOUND
- ChatHistoryResponse interface: FOUND
- ChatPostResponse interface: FOUND
- TradeOutcome interface: FOUND
- PortfolioResponse (preserved): FOUND
- DEFAULT_TICKERS (preserved): FOUND
- commit 873474c: FOUND
- commit 08bb6da: FOUND
- commit 5e6fe2e: FOUND
