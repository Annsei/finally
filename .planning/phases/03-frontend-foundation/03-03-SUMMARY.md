---
phase: 03-frontend-foundation
plan: "03"
subsystem: frontend
tags: [react, swr, zustand, header, tdd, connection-status, portfolio]
dependency_graph:
  requires:
    - frontend/src/types/market.ts (PortfolioResponse — from 03-01)
    - frontend/src/stores/priceStore.ts (connectionStatus atom — from 03-02)
    - frontend/jest.config.js (from 03-01)
  provides:
    - frontend/src/lib/fetcher.ts
    - frontend/src/components/Header.tsx
  affects:
    - frontend/src/pages/index.tsx (will import and render Header)
    - All future components using SWR (can import shared fetcher from src/lib/fetcher.ts)
tech_stack:
  added: []
  patterns:
    - SWR polling (refreshInterval:5000) for REST data per locked decision D-04
    - Single-atom Zustand selector (s) => s.connectionStatus — Pitfall 2 compliance
    - DOT_COLORS map: connected→bg-terminal-up, reconnecting→bg-terminal-amber, disconnected→bg-terminal-down
    - tabular-nums on numeric spans for layout-shift-free live updates
    - '—' placeholder via optional chaining + ternary when SWR data is undefined
    - React JSX text node rendering (no dangerouslySetInnerHTML) — T-03-H-XSS mitigation
    - Shared fetcher in src/lib/ to avoid duplicate inline fetcher definitions
key_files:
  created:
    - frontend/src/lib/fetcher.ts
    - frontend/src/components/Header.tsx
    - frontend/__tests__/Header.test.tsx
  modified:
    - frontend/.gitignore (added !src/lib/ override for root lib/ Python pattern)
decisions:
  - "DOT_COLORS uses bg-terminal-amber (#f59e0b) for reconnecting state so accent yellow (#ecad0a) stays exclusively reserved for the selected-row indicator (UI-SPEC § Color decision)"
  - "Shared fetcher placed in src/lib/fetcher.ts (separate file per plan spec) not inlined in Header — enables reuse by WatchlistPanel and future components"
  - "Test 5 assertion uses /—/ regex because '$' and '—' are sibling text nodes inside the same <span>, so getAllByText('—') with exact match finds no element"
  - "frontend/.gitignore negates root-level lib/ pattern to allow tracking of frontend/src/lib/"
metrics:
  duration: "~10 minutes"
  completed: "2026-06-06"
  tasks_completed: 1
  tasks_total: 1
  files_created: 3
  files_modified: 1
---

# Phase 03 Plan 03: Header Component Summary

**One-liner:** Header bar with live cash + portfolio total via SWR 5s polling against /api/portfolio, and a green/amber/red connection dot driven by Zustand connectionStatus, fully TDD-covered with 5 passing tests.

## What Was Built

### Task 1 (TDD): Shared fetcher + Header component

**RED phase (commit a635cd9):**
Created `frontend/__tests__/Header.test.tsx` with 5 failing behaviors:
- Test 1: `connectionStatus='connected'` → dot has class `bg-terminal-up`
- Test 2: `connectionStatus='reconnecting'` → dot has class `bg-terminal-amber`
- Test 3: `connectionStatus='disconnected'` → dot has class `bg-terminal-down`
- Test 4: SWR returns `{ cash: 10000, total_value: 12345.67 }` → output contains `'10,000'` and `'12,345.67'`
- Test 5: SWR returns `undefined` → renders `'—'` placeholders without throwing

**GREEN phase (commit 0d8d87a):**

Created `frontend/src/lib/fetcher.ts`:
- Exports `fetcher = (url: string) => fetch(url).then(r => r.json())` — shared across all SWR consumers

Created `frontend/src/components/Header.tsx`:
- Reads `connectionStatus` via `usePriceStore((s) => s.connectionStatus)` — single-atom selector, no object selector (Pitfall 2 compliance)
- Fetches portfolio via `useSWR<PortfolioResponse>('/api/portfolio', fetcher, { refreshInterval: 5000 })` — satisfies FE-02 "live updating" per locked decision D-04
- `DOT_COLORS` map: `connected → bg-terminal-up`, `reconnecting → bg-terminal-amber`, `disconnected → bg-terminal-down` — amber for reconnecting per UI-SPEC so `#ecad0a` stays for row selection
- Renders `FinAlly` brand label in `text-terminal-accent`
- Cash balance: `text-sm font-normal` with `tabular-nums`, `$` prefix, `'—'` fallback
- Portfolio total: `text-xl font-semibold` (display size per UI-SPEC), `tabular-nums`, `'—'` fallback
- Connection dot: `w-2 h-2 rounded-full ${DOT_COLORS[connectionStatus]}` with `title={connectionStatus}` for test accessibility
- Values rendered as React text nodes — never `dangerouslySetInnerHTML` (T-03-H-XSS mitigation)

## Verification Results

| Check | Result |
|-------|--------|
| `npm test -- --testPathPatterns=Header` exits 0 | PASS (5/5 tests) |
| Full suite `npm test -- --watchAll=false` exits 0 | PASS (17/17 tests) |
| `npm run build` (via main repo) exits 0 | PASS |
| Header.tsx contains `useSWR` with `/api/portfolio` and `refreshInterval: 5000` | PASS |
| `connectionStatus` read via single-atom selector (not object selector) | PASS |
| `DOT_COLORS` maps connected→bg-terminal-up, reconnecting→bg-terminal-amber, disconnected→bg-terminal-down | PASS |
| No `dangerouslySetInnerHTML` in Header.tsx | PASS |
| `'—'` rendered when `data` is `undefined` | PASS |
| `tabular-nums` on both numeric spans | PASS |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Test 5 assertion used exact string '—' which fails due to adjacent '$' text node**
- **Found during:** Task 1 GREEN phase (test ran against real implementation)
- **Issue:** `getAllByText('—')` uses exact matching and looks for elements whose full text content equals `'—'`. The rendered span contains `$` and `—` as adjacent text nodes (`<span>$—</span>`), so no element has text `'—'` alone.
- **Fix:** Updated assertion to `getAllByText(/—/)` which uses regex partial matching and finds the spans containing the dash.
- **Files modified:** `frontend/__tests__/Header.test.tsx`
- **Commit:** 0d8d87a (included in GREEN phase commit)

**2. [Rule 3 - Blocking] Root .gitignore `lib/` pattern blocked tracking of `frontend/src/lib/`**
- **Found during:** Task 1 GREEN phase commit
- **Issue:** The root `.gitignore` (line 17) has a bare `lib/` pattern intended for Python virtual environments. This pattern matches any `lib/` directory anywhere in the tree, including `frontend/src/lib/`. Git refused to stage `frontend/src/lib/fetcher.ts`.
- **Fix:** Added `!src/lib/` and `!src/lib/**` negation overrides to `frontend/.gitignore`.
- **Files modified:** `frontend/.gitignore`
- **Commit:** 0d8d87a

## Known Stubs

None — `Header.tsx` and `fetcher.ts` are production-ready. The component renders live data when connected to the backend API and a connection dot tracking the real SSE state.

## Threat Flags

No new security surface beyond the plan's threat model. All values rendered as React text nodes (JSX escaping active). No `dangerouslySetInnerHTML`. Optional chaining + `'—'` fallback handles null/undefined portfolio fields (T-03-H-NaN mitigation).

## TDD Gate Compliance

- RED gate: `test(03-03)` commit `a635cd9` — failing tests written first
- GREEN gate: `feat(03-03)` commit `0d8d87a` — implementation makes all 5 tests pass

## Self-Check: PASSED
