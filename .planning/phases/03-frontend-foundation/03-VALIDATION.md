---
phase: 3
slug: frontend-foundation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-06
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | Jest 30.4.2 + React Testing Library 16.3.2 |
| **Config file** | `frontend/jest.config.js` — Wave 0 gap (must be created) |
| **Quick run command** | `cd frontend && npm test -- --testPathPattern=<file> --watchAll=false` |
| **Full suite command** | `cd frontend && npm test -- --watchAll=false` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `cd frontend && npm test -- --watchAll=false --testPathPattern=<changed-component>`
- **After every plan wave:** Run `cd frontend && npm test -- --watchAll=false`
- **Before `/gsd:verify-work`:** Full suite must be green + `cd frontend && npm run build` succeeds
- **Max feedback latency:** ~15 seconds

---

## Per-Task Verification Map

| Req ID | Behavior | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|--------|----------|------------|-----------------|-----------|-------------------|-------------|--------|
| FE-01 | `next build` produces `out/` directory with static HTML | T-XSS-01 | Static export has no server-side code execution surface | build smoke | `cd frontend && npm run build` | ❌ W0 | ⬜ pending |
| FE-02 | Header renders connection status dot, portfolio total value, and cash balance | — | N/A | unit | `cd frontend && npm test -- --testPathPattern=Header --watchAll=false` | ❌ W0 | ⬜ pending |
| FE-03 | Root element has dark terminal background color `#0d1117` or `#1a1a2e` applied | — | N/A | unit | `cd frontend && npm test -- --testPathPattern=index --watchAll=false` | ❌ W0 | ⬜ pending |
| FE-04 | `EventSource` created on mount at `/api/stream/prices`; `es.close()` called on unmount | T-DoS-01 | Cleanup prevents memory leak from unclosed EventSource | unit | `cd frontend && npm test -- --testPathPattern=usePriceStream --watchAll=false` | ❌ W0 | ⬜ pending |
| FE-05 | Watchlist panel renders a row for each ticker from `/api/watchlist` | — | N/A | unit | `cd frontend && npm test -- --testPathPattern=WatchlistPanel --watchAll=false` | ❌ W0 | ⬜ pending |
| FE-06 | Price cell receives `.flash-up` / `.flash-down` class on direction change; class removed after ~500ms | T-XSS-01 | Class toggled on existing DOM element — no `innerHTML`, no script injection | unit | `cd frontend && npm test -- --testPathPattern=WatchlistRow --watchAll=false` | ❌ W0 | ⬜ pending |
| FE-07 | Sparkline chart instance created on mount; `series.update()` called on each price tick | — | N/A | unit (canvas mock) | `cd frontend && npm test -- --testPathPattern=SparklineChart --watchAll=false` | ❌ W0 | ⬜ pending |
| FE-08 | Clicking a watchlist row sets it as selected; selected row gains 2px accent bar | — | N/A | unit | `cd frontend && npm test -- --testPathPattern=WatchlistRow --watchAll=false` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `frontend/jest.config.js` — Jest + `next/jest` configuration with jsdom environment + jest-canvas-mock
- [ ] `frontend/jest.setup.ts` — `import '@testing-library/jest-dom'` + `import 'jest-canvas-mock'`
- [ ] `frontend/__mocks__/nextFontMock.js` — mock for `next/font/google` (required by next/jest guide)
- [ ] `frontend/__tests__/Header.test.tsx` — covers FE-02 (connection dot, portfolio value, cash)
- [ ] `frontend/__tests__/WatchlistPanel.test.tsx` — covers FE-05 (renders ticker rows)
- [ ] `frontend/__tests__/WatchlistRow.test.tsx` — covers FE-06 (flash class) and FE-08 (selection)
- [ ] `frontend/__tests__/SparklineChart.test.tsx` — covers FE-07 (createChart + series.update called)
- [ ] `frontend/__tests__/usePriceStream.test.tsx` — covers FE-04 (EventSource mock: create on mount, close on unmount)

**Testing notes:**
- **Lightweight Charts + canvas:** Jest runs in jsdom with no real canvas. `jest-canvas-mock` mocks `HTMLCanvasElement` so `createChart()` doesn't throw. Tests verify `createChart` and `series.update` were called — pixel output is not asserted.
- **EventSource in jsdom:** jsdom does not implement `EventSource`. Tests for `usePriceStream` must mock it with `jest.fn()` or a manual class mock. Verify mount (`es.onopen`, `es.onmessage` set) and unmount (`es.close()` called) behavior.
- **Static export validation (FE-01):** `npm run build` is the acceptance command. Test passes when the `out/` directory exists and contains `index.html`.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Prices flash visually green/red in browser | FE-06 | CSS animation requires a real browser; jsdom does not run animations | Open app, connect to SSE stream, watch watchlist — prices should briefly flash green or red on each update |
| Sparklines fill in progressively as SSE data arrives | FE-07 | Canvas rendering requires a real browser | Open app, watch sparkline columns — lines should grow left-to-right as new price ticks arrive since page load |
| Connection dot changes color on disconnect/reconnect | FE-02 | EventSource state transitions require a real backend + network | Kill the backend, verify dot turns red; restart backend, verify dot returns to green within a few seconds |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags (all commands use `--watchAll=false`)
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
