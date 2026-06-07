---
phase: 04-frontend-portfolio-trading
plan: "03"
subsystem: frontend
tags: [portfolio, heatmap, positions-table, tdd, react, swr, zustand]
dependency_graph:
  requires: ["04-01"]
  provides: ["PortfolioHeatmap", "PositionsTable"]
  affects: ["frontend/src/components/PortfolioHeatmap.tsx", "frontend/src/components/PositionsTable.tsx"]
tech_stack:
  added: []
  patterns: ["SWR shared cache key /api/portfolio", "CSS flexbox treemap", "useTicker flash lifecycle"]
key_files:
  created:
    - frontend/src/components/PortfolioHeatmap.tsx
    - frontend/src/components/PositionsTable.tsx
    - frontend/__tests__/PortfolioHeatmap.test.tsx
    - frontend/__tests__/PositionsTable.test.tsx
  modified: []
decisions:
  - "Used flash-up/flash-down CSS classes (actual tailwind.config.js animation names), not animate-flash-up/animate-flash-down as written in PLAN.md — the plan text referenced incorrect class names; the codebase source of truth (WatchlistRow.tsx + tailwind.config.js) was used"
  - "Added data-price-cell={pos.ticker} attribute to current-price <td> for clean test targeting without relying on DOM position"
  - "PositionsRow is an inner component (not exported) — keeps flash refs and useTicker calls per-row while PositionsTable owns the SWR fetch"
metrics:
  duration: "291s"
  completed: "2026-06-07T05:24:44Z"
  tasks_completed: 2
  files_created: 4
---

# Phase 04 Plan 03: Portfolio Data Panels Summary

CSS flexbox treemap heatmap and Bloomberg-style positions table with live SSE flashing, both reading `/api/portfolio` via shared SWR cache.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | PortfolioHeatmap component (FE-10) + test | 50674cb | frontend/src/components/PortfolioHeatmap.tsx, frontend/__tests__/PortfolioHeatmap.test.tsx |
| 2 | PositionsTable component (FE-12) + test | 7dfa520 | frontend/src/components/PositionsTable.tsx, frontend/__tests__/PositionsTable.test.tsx |

## What Was Built

### PortfolioHeatmap.tsx
- CSS flexbox treemap — no library, hand-built per CONTEXT D-05
- Each tile: `width: (posValue / totalValue) * 100%`, minimum 64px (keeps small positions clickable)
- Color intensity: profit `rgba(34, 197, 94, alpha)`, loss `rgba(239, 68, 68, alpha)`, alpha = `min(abs(pnl_pct) / 20, 1.0)` floored at 0.3
- Each tile shows: ticker (font-semibold), `$posValue` (tabular-nums), `±pnl_pct%` (colored terminal-up/down/muted)
- Empty state (no positions or undefined data): `No positions yet. Use the trade bar to buy shares.`
- SWR key: `/api/portfolio` (shared cache with Header and PositionsTable)

### PositionsTable.tsx
- Bloomberg-style compact `<table className="w-full text-xs border-collapse">` with 6 columns: Ticker · Qty · Avg Cost · Price · P&L · Change %
- Inner `PositionsRow` component per position:
  - `useTicker(pos.ticker)` for live Zustand price with SWR fallback
  - `priceRef` + `flashTimeoutRef` tracking the current-price `<td>`
  - Flash lifecycle: `void cell.offsetWidth` reflow, `flash-up`/`flash-down` add + remove after 500ms
  - Live P&L computed: `(currentPrice - avg_cost) * quantity`
  - P&L and Change% colored terminal-up/down/muted based on sign
- Empty state: `No positions yet. Use the trade bar to buy shares.`
- SWR key: `/api/portfolio` (shared cache)

## Verification Results

```
Test Suites: 2 passed, 2 total
Tests:       7 passed, 7 total
```

- PortfolioHeatmap: 4 tests — tile count, width% proportions, empty state, undefined data
- PositionsTable: 3 tests — 6 columns render, flash-up lifecycle (500ms), empty state

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] CSS flash class names corrected from plan text**
- **Found during:** Task 2 implementation
- **Issue:** PLAN.md and PATTERNS.md reference `animate-flash-up`/`animate-flash-down` but tailwind.config.js animation block defines them as `flash-up`/`flash-down`, and WatchlistRow.tsx uses `flash-up`/`flash-down` directly
- **Fix:** Used the correct class names from the actual codebase (WatchlistRow.tsx is the canonical source)
- **Files modified:** frontend/src/components/PositionsTable.tsx, frontend/__tests__/PositionsTable.test.tsx

**2. [Rule 2 - Missing critical functionality] Added data-price-cell attribute for test targeting**
- **Found during:** Task 2 test implementation
- **Issue:** The current-price `<td>` needed a reliable selector in tests (using `ref` for flash, but tests needed DOM query)
- **Fix:** Added `data-price-cell={pos.ticker}` attribute to the price `<td>`
- **Files modified:** frontend/src/components/PositionsTable.tsx

## Known Stubs

None — both components wire live data sources (SWR + Zustand).

## Threat Flags

None — all position data rendered as React text children (auto-escaped). No dangerouslySetInnerHTML. Division guarded by `pos.avg_cost > 0` before computing pnl_pct. No new network endpoints or auth paths introduced.

## TDD Gate Compliance

Both tasks followed RED → GREEN cycle:
1. `test(04-03)` RED: test file written, failed with "Cannot find module" 
2. `feat(04-03)` GREEN: component implemented, all tests pass
3. No REFACTOR needed — code was clean on first pass

## Self-Check: PASSED
