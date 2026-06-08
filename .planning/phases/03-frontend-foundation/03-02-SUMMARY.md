---
phase: 03-frontend-foundation
plan: "02"
subsystem: frontend
tags: [zustand, eventsource, sse, react-hooks, typescript, tdd, jest]
dependency_graph:
  requires:
    - frontend/src/types/market.ts (from 03-01)
    - frontend/jest.config.js (from 03-01)
    - frontend/jest.setup.ts (from 03-01)
  provides:
    - frontend/src/stores/priceStore.ts
    - frontend/src/hooks/usePriceStream.ts
  affects:
    - frontend/src/pages/index.tsx (calls usePriceStream once at page root)
    - frontend/src/components/Header.tsx (reads connectionStatus via usePriceStore)
    - frontend/src/components/WatchlistRow.tsx (reads per-ticker data via useTicker)
    - frontend/src/components/SparklineChart.tsx (reads per-ticker data via useTicker)
tech_stack:
  added: []
  patterns:
    - Zustand v5 create<Store>()() with single-atom setters (no object selectors)
    - useTicker(ticker) per-row selector returning PriceUpdate | undefined
    - EventSource inside useEffect only (SSR-safe, Pitfall 3 compliance)
    - JSON.parse in try/catch with silent drop of malformed events (T-03-PP)
    - useEffect cleanup always calls es.close() (T-03-DoS mitigation)
    - Separate Zustand selectors for setPrices and setConnectionStatus (Pitfall 2 compliance)
    - Manual EventSource mock class with static constants attached to global.EventSource
key_files:
  created:
    - frontend/src/stores/priceStore.ts
    - frontend/src/hooks/usePriceStream.ts
    - frontend/__tests__/priceStore.test.ts
    - frontend/__tests__/usePriceStream.test.tsx
  modified: []
decisions:
  - "Attached static CONNECTING/OPEN/CLOSED constants to the jest.fn() EventSource mock so the hook's es.readyState === EventSource.CONNECTING check resolves correctly in jsdom"
  - "Test 4 uses a separate beforeEach store reset so malformed JSON test starts from empty prices"
  - "onerror handler checks es.readyState (not event properties) — matches the real EventSource API and the plan's behavior specification"
metrics:
  duration: "~15 minutes"
  completed: "2026-06-06"
  tasks_completed: 2
  tasks_total: 2
  files_created: 4
  files_modified: 0
---

# Phase 03 Plan 02: SSE Data Layer Summary

**One-liner:** Zustand v5 price store with per-ticker selector and a SSR-safe EventSource hook that defensively parses SSE messages and maps connection state, both fully TDD-covered with 11 passing tests.

## What Was Built

### Task 1 (TDD): Zustand Price Store

Created `frontend/src/stores/priceStore.ts`:
- `usePriceStore` — Zustand v5 store with `prices: PriceMap`, `connectionStatus: 'connected' | 'reconnecting' | 'disconnected'`, `setPrices`, and `setConnectionStatus`
- `useTicker(ticker)` — exported per-row selector returning `state.prices[ticker]` (a single atom, never an object literal)
- No object-literal selectors, no `useShallow` import — Zustand v5 Pitfall 2 compliance
- Imports types from `@/types/market` (snake_case fields from Plan 01)

Created `frontend/__tests__/priceStore.test.ts` with 4 tests:
- Initial state: `prices === {}`, `connectionStatus === 'disconnected'`
- `setPrices` replaces map; `AAPL.price` equals set value
- `setConnectionStatus('connected')` updates atom
- Store state for known ticker returns `PriceUpdate`; unknown ticker returns `undefined`

### Task 2 (TDD): usePriceStream EventSource Hook

Created `frontend/src/hooks/usePriceStream.ts`:
- Single `useEffect(() => { ... }, [])` — one connection per page lifetime
- `new EventSource('/api/stream/prices')` inside `useEffect` only (never module scope — Pitfall 3 / T-03-SSR)
- `onopen` → `setConnectionStatus('connected')`
- `onmessage` → `JSON.parse(event.data)` in `try/catch`; success calls `setPrices(data)`; failures silently dropped (T-03-XSS / T-03-PP)
- `onerror` → checks `es.readyState === EventSource.CONNECTING` → `'reconnecting'` else `'disconnected'`
- Cleanup → `es.close()` + `setConnectionStatus('disconnected')` (T-03-DoS)
- Two separate Zustand selectors (`s.setPrices`, `s.setConnectionStatus`) — no object selector (Pitfall 2)

Created `frontend/__tests__/usePriceStream.test.tsx` with 7 tests (6 behaviors + 1 readyState variant):
- Mount creates EventSource exactly once with `/api/stream/prices`
- `onopen` → store status `'connected'`
- Valid JSON `onmessage` → `store.prices.AAPL` populated
- Malformed JSON `onmessage` → no throw, prices unchanged
- `onerror` with `readyState=CONNECTING` → `'reconnecting'`
- `onerror` with `readyState=CLOSED` → `'disconnected'`
- Unmount → `es.close()` called exactly once

## Verification Results

| Check | Result |
|-------|--------|
| `npm test -- --watchAll=false --testPathPatterns=priceStore` exits 0 | PASS (4/4 tests) |
| `npm test -- --watchAll=false --testPathPatterns=usePriceStream` exits 0 | PASS (7/7 tests) |
| Combined: `--testPathPatterns="priceStore\|usePriceStream"` | PASS (11/11 tests) |
| `usePriceStore` exports `usePriceStore` and `useTicker` | PASS |
| No object-literal selector in priceStore.ts | PASS |
| No `useShallow` import in priceStore.ts | PASS |
| `new EventSource('/api/stream/prices')` inside useEffect only | PASS |
| `JSON.parse` wrapped in try/catch (catch does not rethrow) | PASS |
| `es.close()` in useEffect cleanup | PASS |
| No object-literal selector in usePriceStream.ts | PASS |
| `npm run build` exits 0 (no SSR window error) | PASS |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] EventSource.CONNECTING undefined in jest mock — Test 5a failing**
- **Found during:** Task 2 GREEN phase
- **Issue:** `global.EventSource` was set to `jest.fn().mockImplementation(...)`, which is a bare function with no static properties. The hook's `es.readyState === EventSource.CONNECTING` compared against `undefined`, making any `readyState` appear non-CONNECTING, causing Test 5a to produce `'disconnected'` instead of `'reconnecting'`.
- **Fix:** After creating the `jest.fn()` mock, explicitly assigned `MockES.CONNECTING = 0`, `MockES.OPEN = 1`, `MockES.CLOSED = 2` to the mock constructor — matching the static interface of the real browser `EventSource`.
- **Files modified:** `frontend/__tests__/usePriceStream.test.tsx`
- **Commit:** 7719758

## Known Stubs

None — both deliverables are fully wired. `priceStore.ts` and `usePriceStream.ts` are production-ready implementations (not placeholders). They will be consumed by Plan 03 (Header) and Plan 04 (WatchlistPanel/WatchlistRow/SparklineChart).

## Threat Flags

No new security surface beyond what the plan's threat model covered. All four STRIDE threats (T-03-XSS, T-03-PP, T-03-DoS, T-03-SSR) are mitigated as specified.

## Self-Check: PASSED
