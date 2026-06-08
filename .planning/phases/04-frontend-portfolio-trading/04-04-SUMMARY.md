---
phase: 04-frontend-portfolio-trading
plan: "04"
subsystem: frontend
tags: [react, swr, trading, optimistic-update, tdd]
dependency_graph:
  requires: ["04-01"]
  provides: ["TradeBar component", "POST /api/portfolio/trade integration"]
  affects: ["/api/portfolio SWR cache", "Header cash display"]
tech_stack:
  added: []
  patterns:
    - "SWR v2 optimisticData + rollbackOnError for instant UI feedback"
    - "useEffect auto-fill from selectedTicker prop"
    - "Client-side ticker/qty validation before network call"
key_files:
  created:
    - frontend/src/components/TradeBar.tsx
    - frontend/__tests__/TradeBar.test.tsx
  modified: []
decisions:
  - "SWR shared key '/api/portfolio' (no trailing slash) matches Header.tsx so trade revalidates header cash/total simultaneously"
  - "mockMutate in tests re-throws errors (not swallows) — SWR v2 applies rollback internally AND re-throws, so handleTrade catch fires on failure"
  - "jest.mock('swr') factory is empty; useSWR mock wired in beforeEach via jest.mocked() to avoid hoisting 'cannot access before initialization' error"
  - "node_modules symlinked into worktree frontend dir so jest.config.js can resolve next/jest"
metrics:
  duration: "~5 minutes"
  completed: "2026-06-07"
  tasks_completed: 1
  files_created: 2
  files_modified: 0
---

# Phase 04 Plan 04: TradeBar Component Summary

TradeBar market-order form with optimistic SWR mutate, ticker/quantity input validation, auto-fill from watchlist selection, and inline API error display.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (RED) | TradeBar failing test | 99b795a | frontend/__tests__/TradeBar.test.tsx |
| 1 (GREEN) | TradeBar implementation | b1b6fd8 | frontend/src/components/TradeBar.tsx, frontend/__tests__/TradeBar.test.tsx |

## What Was Built

`frontend/src/components/TradeBar.tsx` — A trade entry form component (FE-13) that:

- Accepts `selectedTicker: string | null` and `onTradeComplete?: () => void` props
- Auto-fills ticker input when `selectedTicker` changes (D-12) via `useEffect`
- Validates inputs client-side before any network call:
  - Ticker: `trim().toUpperCase()` + `/^[A-Z]+$/` regex (T-4-01)
  - Quantity: `isFinite(Number(qty)) && Number(qty) > 0` (T-4-03)
- Executes trades via `mutate(asyncMutator, { optimisticData, rollbackOnError: true, revalidate: true })` against `/api/portfolio` SWR key
- Sends `POST /api/portfolio/trade` with `{ ticker, quantity, side }` body
- Shows API error messages inline below inputs; clears them on each new submit attempt (D-14)
- Buy button: green `#22c55e`; Sell button: red `#ef4444` (UI-SPEC semantic colors)
- Buttons disabled during pending state

`frontend/__tests__/TradeBar.test.tsx` — 9 TDD tests covering all acceptance criteria.

## TDD Gate Compliance

- RED commit (99b795a): `test(04-04)` — failing tests written first, confirmed failing
- GREEN commit (b1b6fd8): `feat(04-04)` — implementation makes all 9 tests pass
- Full suite: 40/40 tests pass across all 9 test suites

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] jest.mock hoisting caused "Cannot access before initialization"**
- **Found during:** Task 1 GREEN phase
- **Issue:** `jest.mock('swr', factory)` is hoisted above `const mockPortfolio = ...` declarations by Babel, causing a ReferenceError when the factory referenced the not-yet-declared variable
- **Fix:** Empty jest.mock factory; wire `jest.mocked(useSWR).mockReturnValue(...)` in `beforeEach` instead — safe because hoisting is complete by then
- **Files modified:** frontend/__tests__/TradeBar.test.tsx
- **Commit:** b1b6fd8

**2. [Rule 3 - Blocking] Worktree missing node_modules**
- **Found during:** Task 1 test run setup
- **Issue:** Worktree frontend dir had no node_modules; jest could not resolve `next/jest`, `@testing-library/jest-dom`, etc.
- **Fix:** Created symlink `worktree/frontend/node_modules -> main-repo/frontend/node_modules`
- **Files modified:** (symlink only — not a committed file)
- **Commit:** n/a (filesystem-only fix)

**3. [Rule 1 - Bug] mockMutate swallowed errors, T-4-400 test never saw inline error**
- **Found during:** Task 1 GREEN phase (1 test failing after initial implementation)
- **Issue:** Original mockMutate caught the throw and swallowed it when `rollbackOnError: true`, but SWR v2 actually re-throws after applying rollback — so `handleTrade`'s catch block never fired in tests
- **Fix:** Simplified mockMutate to always propagate throws (matching real SWR v2 behavior)
- **Files modified:** frontend/__tests__/TradeBar.test.tsx
- **Commit:** b1b6fd8

## Known Stubs

None — TradeBar is fully wired to `POST /api/portfolio/trade` and `/api/portfolio` SWR.

## Threat Surface Scan

No new network endpoints or auth paths introduced. TradeBar only calls the existing `/api/portfolio/trade` endpoint. Client-side validation mitigations T-4-01 and T-4-03 are implemented as specified.

## Self-Check

Files created:
- frontend/src/components/TradeBar.tsx: FOUND
- frontend/__tests__/TradeBar.test.tsx: FOUND

Commits:
- 99b795a: FOUND (RED test commit)
- b1b6fd8: FOUND (GREEN implementation commit)

## Self-Check: PASSED
