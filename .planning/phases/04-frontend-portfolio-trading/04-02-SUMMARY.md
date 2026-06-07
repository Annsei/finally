---
phase: 04-frontend-portfolio-trading
plan: 02
subsystem: frontend-charts
tags: [frontend, lightweight-charts, tdd, sse, swr]
requirements: [FE-09, FE-11]

dependency_graph:
  requires:
    - frontend/src/stores/priceStore.ts (useTicker selector)
    - frontend/src/lib/fetcher.ts (SWR fetcher)
    - frontend/src/types/market.ts (PortfolioHistoryResponse — added in Plan 01)
    - lightweight-charts v5 (already installed)
    - swr v2 (already installed)
  provides:
    - frontend/src/components/MainChart.tsx (selected-ticker live line chart)
    - frontend/src/components/PnLChart.tsx (portfolio value area chart with 30s poll)
  affects:
    - frontend/__tests__/MainChart.test.tsx (3 tests)
    - frontend/__tests__/PnLChart.test.tsx (3 tests)

tech_stack:
  added: []
  patterns:
    - Lightweight Charts v5 createChart/addSeries(LineSeries) instance lifecycle (from SparklineChart.tsx)
    - Lightweight Charts v5 addSeries(AreaSeries) for filled area chart
    - Monotonic tickCountRef for SSE-driven time axis (avoids real timestamp parsing)
    - Array index+1 as time value for historical snapshot charts (Pitfall 4 avoidance)
    - Separate useEffect([ticker]) to reset series on ticker change (Pitfall 1 avoidance)
    - useSWR with refreshInterval: 30_000 for 30s polling
    - jest.mocked(useSWR) after import to avoid jest.mock hoisting issue

key_files:
  created:
    - frontend/src/components/MainChart.tsx
    - frontend/src/components/PnLChart.tsx
    - frontend/__tests__/MainChart.test.tsx
    - frontend/__tests__/PnLChart.test.tsx
  modified: []

decisions:
  - "Used jest.mocked(useSWR) after import rather than variable-in-factory to avoid jest.mock hoisting ReferenceError"
  - "MainChart uses autoSize: true (not explicit width/height props) for full-width resize via ResizeObserver"
  - "PnLChart renders empty state 'No portfolio history yet.' above the chart div (not inside container)"
  - "Ticker title header in MainChart styled with accent color #ecad0a per UI-SPEC"

metrics:
  duration: "~4 minutes"
  completed: "2026-06-07"
  tasks_completed: 2
  files_modified: 4
  tests_added: 2
  tests_passing: 6
---

# Phase 04 Plan 02: MainChart and PnLChart Summary

**One-liner:** Lightweight Charts v5 LineSeries live ticker chart (FE-09) and AreaSeries portfolio value chart with 30s SWR poll (FE-11), both following SparklineChart instance lifecycle with full TDD coverage.

## What Was Built

### Task 1: MainChart component (FE-09) + test (TDD)

Added `frontend/src/components/MainChart.tsx` — a default-exported React component taking `{ ticker: string }`. The component:

- Creates a Lightweight Charts v5 instance with `autoSize: true`, grid lines at `#30363d`, text color `#8b949e`, and visible price/time scales
- Calls `chart.addSeries(LineSeries, { color: '#209dd7', lineWidth: 2 })` for a terminal-blue line
- Uses `useTicker(ticker)` from Zustand to receive SSE price updates, appending each as `series.update({ time: tickCountRef.current, value: price })`
- Has a dedicated `useEffect([ticker])` that calls `seriesRef.current?.setData([])` and resets `tickCountRef.current = 0` on ticker switch (prevents discontinuous jump — Pitfall 1)
- Renders a ticker title header with accent color `#ecad0a` above the chart container
- Cleans up with `chart.remove()` and nulls refs on unmount

`frontend/__tests__/MainChart.test.tsx` provides 3 tests:
1. `createChart` called once; `addSeries` called with `LineSeries`
2. Store price update triggers `series.update` with `{ time: 1, value: 190.5 }`
3. Re-render with different ticker calls `series.setData([])`

TDD gates:
- RED: test suite fails with "Cannot find module" (component did not exist)
- GREEN: commit `78f365a` — all 3 tests pass

### Task 2: PnLChart component (FE-11) + test (TDD)

Added `frontend/src/components/PnLChart.tsx` — a default-exported React component with no props. The component:

- Uses `useSWR<PortfolioHistoryResponse>('/api/portfolio/history', fetcher, { refreshInterval: 30_000 })` — exact key string, no trailing slash
- Creates a Lightweight Charts v5 instance with `autoSize: true`, same grid/text color theme
- Calls `chart.addSeries(AreaSeries, { lineColor: '#209dd7', topColor: 'rgba(34, 197, 94, 0.4)', bottomColor: 'rgba(34, 197, 94, 0.0)', lineWidth: 2 })`
- Maps snapshots to `{ time: (i + 1) as UTCTimestamp, value: s.total_value }` using array index (avoids timestamp gap Pitfall 4)
- Guards setData call: `if (!data?.snapshots?.length || !seriesRef.current) return`
- Renders the empty-state string `No portfolio history yet.` when no snapshots
- Cleans up with `chart.remove()` on unmount

`frontend/__tests__/PnLChart.test.tsx` provides 3 tests:
1. `createChart` called once; `addSeries` called with `AreaSeries` sentinel
2. SWR returning 3 snapshots → `series.setData` called with `[{time:1,value:10000}, {time:2,value:10500}, {time:3,value:10250}]`
3. `undefined` SWR data → component does not throw; `series.setData` not called

TDD gates:
- RED: test suite fails with "Cannot find module" (component did not exist)
- GREEN: commit `3507fee` — all 3 tests pass

## Verification

- `npm test -- --testPathPattern=MainChart --watchAll=false` — 3/3 passed
- `npm test -- --testPathPattern=PnLChart --watchAll=false` — 3/3 passed
- Both test suites run together: 6/6 passed

Acceptance criteria checklist:
- [x] `frontend/src/components/MainChart.tsx` contains `createChart` and `addSeries(LineSeries`
- [x] `frontend/src/components/MainChart.tsx` contains `useTicker(`
- [x] `frontend/src/components/MainChart.tsx` contains `setData([])` inside a `[ticker]` effect
- [x] `frontend/src/components/MainChart.tsx` contains `chart.remove()` in cleanup
- [x] `frontend/__tests__/MainChart.test.tsx` asserts createChart called once and setData([]) called on ticker change
- [x] MainChart tests exit 0
- [x] `frontend/src/components/PnLChart.tsx` contains `addSeries(AreaSeries`
- [x] `frontend/src/components/PnLChart.tsx` contains `'/api/portfolio/history'` and `refreshInterval: 30_000`
- [x] `frontend/src/components/PnLChart.tsx` contains `setData(` and uses `(i + 1)` index time
- [x] `frontend/src/components/PnLChart.tsx` contains the string `No portfolio history yet.`
- [x] `frontend/__tests__/PnLChart.test.tsx` asserts createChart called and setData called when data arrives
- [x] PnLChart tests exit 0

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed jest.mock hoisting ReferenceError in PnLChart test**
- **Found during:** Task 2 — first test run after writing PnLChart.test.tsx
- **Issue:** `const mockUseSWR = jest.fn()` declared before `jest.mock('swr', ...)` but Jest hoists `jest.mock` calls above variable declarations. The factory referenced `mockUseSWR` before it was initialized: `ReferenceError: Cannot access 'mockUseSWR' before initialization`
- **Fix:** Removed the pre-declared `mockUseSWR` variable from the factory. Instead used `jest.mock('swr', () => ({ __esModule: true, default: jest.fn() }))` and then `const mockUseSWR = jest.mocked(useSWR)` after the import, which resolves to the same mock function at runtime
- **Files modified:** `frontend/__tests__/PnLChart.test.tsx`
- **Commit:** included in `3507fee`

## Known Stubs

None — both components wire to real data sources (Zustand price store and SWR /api/portfolio/history). No hardcoded values flow to UI rendering.

## Threat Surface Scan

No new network endpoints introduced. Both components consume existing app-owned data:
- `MainChart`: reads from Zustand store (SSE-sourced numeric prices) — T-4-CHART accepted per threat register
- `PnLChart`: polls `/api/portfolio/history` (app's own API, numeric total_value) — T-4-CHART accepted per threat register

No new trust boundaries. T-4-SC confirmed: zero new npm packages installed.

## TDD Gate Compliance

| Task | RED commit | GREEN commit | Compliance |
|------|-----------|-------------|-----------|
| Task 1 (MainChart) | test suite fails "Cannot find module" before `78f365a` | `78f365a` — 3 tests pass | PASS |
| Task 2 (PnLChart) | test suite fails "Cannot find module" before `3507fee` | `3507fee` — 3 tests pass | PASS |

## Commits

| Hash | Type | Description |
|------|------|-------------|
| 78f365a | feat | implement MainChart component with TDD (FE-09) |
| 3507fee | feat | implement PnLChart component with TDD (FE-11) |

## Self-Check: PASSED

- frontend/src/components/MainChart.tsx: FOUND
- frontend/src/components/PnLChart.tsx: FOUND
- frontend/__tests__/MainChart.test.tsx: FOUND
- frontend/__tests__/PnLChart.test.tsx: FOUND
- createChart in MainChart.tsx: FOUND
- addSeries(LineSeries in MainChart.tsx: FOUND
- useTicker( in MainChart.tsx: FOUND
- setData([]) in MainChart.tsx: FOUND
- chart.remove() in MainChart.tsx: FOUND
- addSeries(AreaSeries in PnLChart.tsx: FOUND
- /api/portfolio/history in PnLChart.tsx: FOUND
- refreshInterval: 30_000 in PnLChart.tsx: FOUND
- No portfolio history yet. in PnLChart.tsx: FOUND
- commit 78f365a: FOUND
- commit 3507fee: FOUND
