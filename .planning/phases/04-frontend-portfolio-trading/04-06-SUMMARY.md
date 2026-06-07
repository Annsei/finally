---
phase: "04-frontend-portfolio-trading"
plan: "06"
subsystem: "frontend"
tags: ["dashboard", "layout", "integration", "index", "phase4"]
dependency_graph:
  requires: ["04-02", "04-03", "04-04", "04-05"]
  provides: ["3-column-dashboard", "index-wiring"]
  affects: ["frontend/src/pages/index.tsx"]
tech_stack:
  added: []
  patterns:
    - "SWR auto-select first watchlist ticker useEffect"
    - "chatOpen boolean state for collapsible chat column"
    - "3-column flex layout: fixed watchlist | flex-1 center | shrink-0 chat"
    - "bound mutatePortfolio passed as onTradeComplete/onNewTrade callback"
key_files:
  created: []
  modified:
    - "frontend/src/pages/index.tsx"
    - "frontend/__tests__/index.test.tsx"
decisions:
  - "Used bound mutate from /api/portfolio SWR call (not global mutate) — follows Header.tsx pattern and ensures correct cache key revalidation"
  - "usePriceStream() called exactly once at page root — components use useTicker() from Zustand only (T-4-ES anti-pattern guard)"
  - "overflow-hidden on outer wrapper + overflow-auto on center column — allows center to scroll without clipping chat column"
metrics:
  duration_minutes: 15
  completed_date: "2026-06-07"
  tasks_completed: 1
  tasks_total: 1
  files_changed: 2
---

# Phase 04 Plan 06: Dashboard Integration Summary

## One-liner

3-column terminal dashboard wired with all 6 Phase 4 panels (MainChart, PortfolioHeatmap, PnLChart, TradeBar, PositionsTable, ChatPanel) into `index.tsx`, with auto-select-first-ticker and collapsible chat.

## What Was Built

`frontend/src/pages/index.tsx` updated from Phase 3 skeleton (watchlist + placeholder comment) to the full Phase 4 dashboard:

- **3-column layout** (D-01): `WatchlistPanel` (fixed) | flex-1 center column | `shrink-0` chat column
- **Center column stack** (D-02/D-04): `MainChart` → `[PortfolioHeatmap | PnLChart]` → `TradeBar` → `PositionsTable`
- **Auto-select first ticker** (D-03): `useEffect` on `watchlistData` triggers `setSelectedTicker(tickers[0].ticker)` when no ticker selected
- **Chat open by default** (D-09): `const [chatOpen, setChatOpen] = useState(true)`; column collapses to `w-8` / expands to `w-80` via CSS transition
- **Portfolio revalidation**: `mutatePortfolio` bound from `useSWR('/api/portfolio')` passed as `onTradeComplete` to `TradeBar` and `onNewTrade` to `ChatPanel`
- **Single SSE source** (T-4-ES): `usePriceStream()` called exactly once; Phase 4 components consume `useTicker()` from Zustand

`frontend/__tests__/index.test.tsx` extended with 4 new tests (TDD RED→GREEN):
- Test 6: all 6 Phase 4 panels mount
- Test 7 (D-03): first watchlist ticker auto-selected
- Test 8 (D-09): chat panel open by default
- Test 9 (D-01): center column and chat column present

Full suite: 62/62 tests pass across 14 test suites.

## Tasks

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (RED) | Failing tests for 3-column dashboard | 9c5b467 | frontend/__tests__/index.test.tsx |
| 1 (GREEN) | Wire 3-column dashboard in index.tsx | 777dbc1 | frontend/src/pages/index.tsx |

## Deviations from Plan

None — plan executed exactly as written. The TDD cycle completed cleanly with 4 RED failures → 5 GREEN passes.

## TDD Gate Compliance

- RED gate commit: `9c5b467` — `test(04-06): add failing tests for 3-column dashboard wiring`
- GREEN gate commit: `777dbc1` — `feat(04-06): wire 3-column dashboard in index.tsx with all Phase 4 panels`
- REFACTOR: not needed — implementation matched plan specification directly

## Known Stubs

None — all 6 components render (they have their own full implementations from plans 02-05). The SWR keys (`/api/portfolio`, `/api/watchlist`) resolve to real backend endpoints.

## Threat Flags

None — this plan is composition only. No new trust boundaries introduced. `usePriceStream()` confirmed to appear exactly once in `index.tsx` (T-4-ES mitigated).

## Self-Check

### Files exist
- frontend/src/pages/index.tsx — FOUND
- frontend/__tests__/index.test.tsx — FOUND

### Commits exist
- 9c5b467 — FOUND (test RED)
- 777dbc1 — FOUND (feat GREEN)

## Self-Check: PASSED

## Checkpoint: Task 2 — Human Verification Required

Task 1 is complete. Task 2 is a `checkpoint:human-verify` gate requiring visual verification of the live dashboard.

**What to verify:**
1. Run `cd frontend && npm test -- --watchAll=false` (62 tests green) and `cd backend && uv run --extra dev pytest -v` (all green)
2. Build and serve the app; open http://localhost:8000
3. Confirm 3-column layout: watchlist left, center content, chat panel right (open by default)
4. Confirm main chart auto-renders for first watchlist ticker on load; chart switches cleanly on ticker click
5. Confirm positions table current-price column flashes green/red on price updates (after owning a position)
6. Use trade bar: buy shares; confirm cash decreases, position appears, heatmap tile renders, error appears for over-budget buy
7. Confirm P&L chart shows portfolio value over time (~30s for second snapshot)
8. With LLM_MOCK=true: send a chat message; confirm loading indicator, assistant reply, action badges; test collapse/expand toggle
