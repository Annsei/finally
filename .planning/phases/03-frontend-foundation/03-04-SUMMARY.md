---
phase: 03-frontend-foundation
plan: "04"
subsystem: frontend
tags: [react, zustand, swr, lightweight-charts, watchlist, sparkline, flash-animation, tdd]
dependency_graph:
  requires:
    - frontend/src/types/market.ts (PriceUpdate, WatchlistResponse — from 03-01)
    - frontend/src/stores/priceStore.ts (useTicker, usePriceStore — from 03-02)
    - frontend/src/hooks/usePriceStream.ts (usePriceStream — from 03-02)
    - frontend/src/components/Header.tsx (default export — from 03-03)
    - frontend/src/lib/fetcher.ts (fetcher — from 03-03)
  provides:
    - frontend/src/components/SparklineChart.tsx
    - frontend/src/components/WatchlistRow.tsx
    - frontend/src/components/WatchlistPanel.tsx
    - frontend/src/pages/index.tsx (full dashboard wiring)
  affects:
    - Phase 4 chart area (index.tsx placeholder comment marks injection point)
tech_stack:
  added:
    - lightweight-charts v5 (SparklineChart — addSeries(LineSeries) v5 API)
  patterns:
    - Lightweight Charts v5: addSeries(LineSeries, opts) — NOT addLineSeries (Pitfall 1)
    - Flash animation: classList.remove → void offsetWidth → classList.add → setTimeout 500ms (Pitfall 5)
    - Three-ref pattern for SparklineChart: containerRef, chartRef, seriesRef
    - chart.remove() in useEffect cleanup (Pitfall 4 — no stacked canvases)
    - useTicker(ticker) per-ticker selector in WatchlistRow and SparklineChart
    - useSWR('/api/watchlist', fetcher) in WatchlistPanel
    - usePriceStream() called once at index page root (single EventSource lifetime)
    - moduleNameMapper CJS stub for lightweight-charts ESM resolution in Jest
key_files:
  created:
    - frontend/src/components/SparklineChart.tsx
    - frontend/src/components/WatchlistRow.tsx
    - frontend/src/components/WatchlistPanel.tsx
    - frontend/__tests__/SparklineChart.test.tsx
    - frontend/__tests__/WatchlistRow.test.tsx
    - frontend/__tests__/WatchlistPanel.test.tsx
    - frontend/__tests__/index.test.tsx
    - frontend/__mocks__/lightweightChartsStub.js
  modified:
    - frontend/src/pages/index.tsx (full Dashboard replacing placeholder)
    - frontend/jest.config.js (moduleNameMapper entry for lightweight-charts)
decisions:
  - "lightweight-charts is pure ESM; Jest moduleNameMapper stub resolves it as CJS so
    jest.mock('lightweight-charts', factory) can intercept imports in tests without
    requiring SWC to transform the mjs bundle"
  - "addLineSeries mention appears only in a comment (Pitfall 1 warning); actual call
    is chart.addSeries(LineSeries, opts) using the v5 named-export API"
  - "Flash useEffect keyed on [priceUpdate?.direction, priceUpdate?.timestamp] (not
    [priceUpdate]) so rapid same-direction ticks still re-trigger the animation via
    a unique timestamp"
  - "WatchlistPanel empty state shows when tickers.length === 0 (covers both undefined
    SWR data and empty watchlist response)"
metrics:
  tests_added: 14
  tests_total: 31
  duration: "~20 minutes"

## Self-Check: PASSED

### Acceptance Criteria Verification

| Criterion | Status |
|-----------|--------|
| SparklineChart imports `{ createChart, LineSeries }` from 'lightweight-charts' | ✓ |
| `addSeries(LineSeries` present; `addLineSeries` not in actual code | ✓ |
| Mount useEffect cleanup calls `chart.remove()` | ✓ |
| SparklineChart tests (3/3) pass | ✓ |
| WatchlistRow adds flash-up/flash-down on up/down, nothing on flat | ✓ |
| Flash useEffect clears prior timeout and forces reflow via offsetWidth | ✓ |
| Selected row carries border-l-2, border-terminal-accent, bg-terminal-surface | ✓ |
| WatchlistRow renders SparklineChart in last column | ✓ |
| WatchlistRow tests (6/6) pass | ✓ |
| WatchlistPanel contains useSWR against /api/watchlist and maps to WatchlistRow | ✓ |
| Column headers Symbol, Price, Change % present; empty state 'No prices yet' | ✓ |
| index.tsx calls usePriceStream() exactly once (grep count = 1) | ✓ |
| index.tsx root element carries bg-terminal-bg class | ✓ |
| index.test.tsx renders page and asserts root className contains bg-terminal-bg | ✓ |
| WatchlistPanel tests (4/4) pass | ✓ |
| index tests (1/1) pass — FE-03 dark-theme root class | ✓ |
| Full suite (31/31) passes | ✓ |
| `npm run build` exits 0 and produces out/index.html | ✓ |
