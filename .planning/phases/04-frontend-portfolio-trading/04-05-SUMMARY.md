---
phase: 04-frontend-portfolio-trading
plan: "05"
subsystem: frontend
tags: [chat, ai, tdd, xss-safety, action-badges, swr]
dependency_graph:
  requires: ["04-01"]
  provides: ["frontend/src/components/ChatPanel.tsx"]
  affects: ["frontend/src/pages/index.tsx"]
tech_stack:
  added: []
  patterns:
    - "SWR history load with trailing-slash path (/api/chat/)"
    - "POST fetch + mutateHistory revalidation pattern"
    - "Structured action badge rendering (T-4-04: structured fields only)"
    - "React text child rendering for XSS safety (T-4-02)"
    - "scrollIntoView guarded for jsdom compatibility"
key_files:
  created:
    - frontend/src/components/ChatPanel.tsx
    - frontend/__tests__/ChatPanel.test.tsx
  modified: []
decisions:
  - "Collapse toggle uses text chevrons (›/‹) not lucide-react — avoids new package install (T-4-SC)"
  - "scrollIntoView guarded with typeof check to prevent jsdom TypeError in tests"
  - "Badge text uses structured fields only (ticker, qty, price, action) — never raw LLM output (T-4-04)"
  - "Message content rendered as {msg.content} React text child — dangerouslySetInnerHTML absent (T-4-02)"
metrics:
  duration: "352s (~6m)"
  completed_date: "2026-06-07"
  tasks_completed: 1
  files_created: 2
  files_modified: 0
---

# Phase 04 Plan 05: ChatPanel Component Summary

**One-liner:** AI chat panel with SWR history load, POST send + loading indicator, trade/watchlist action badges, and XSS-safe text rendering built TDD-first.

## What Was Built

`ChatPanel.tsx` — a collapsible AI chat panel component (FE-14, FE-15) with:

- **History load on mount:** `useSWR<ChatHistoryResponse>('/api/chat/', fetcher)` with trailing slash (FastAPI router convention, RESEARCH Pitfall 7)
- **Send flow:** POST to `/api/chat/` with `loading` state, `Thinking…` indicator during in-flight request, `mutateHistory()` revalidation on response, `onNewTrade?.()` callback when trades/watchlist changes are returned
- **Action badges:** Trade badges (`Bought N TICKER @ $PRICE` / `Sold N TICKER @ $PRICE`) with accent `#ecad0a` border for buys and red `#ef4444` border for sells; watchlist badges (`Added TICKER` / `Removed TICKER`) with muted `#8b949e` border. Badge text constructed only from structured fields (T-4-04).
- **XSS safety:** Message content rendered as `{msg.content}` React text child — no `dangerouslySetInnerHTML` anywhere in the component (T-4-02, verified by test and static grep)
- **Auto-scroll:** `useRef<HTMLDivElement>` anchor at end of list, `scrollIntoView` called on message count/loading change, guarded with `typeof` check for jsdom compatibility
- **Collapse toggle:** Header button with `›`/`‹` text chevrons, no lucide-react dependency (T-4-SC)
- **Props:** `{ open: boolean; onToggle: () => void; onNewTrade?: () => void }`

`ChatPanel.test.tsx` — 5 TDD tests covering all FE-14/FE-15 behaviors and T-4-02 security requirement.

## TDD Gate Compliance

| Gate | Commit | Status |
|------|--------|--------|
| RED | `dba066a` — `test(04-05): add failing tests for ChatPanel component (RED)` | PASS |
| GREEN | `ed3cad8` — `feat(04-05): implement ChatPanel component with history, send, and action badges (GREEN)` | PASS |
| REFACTOR | Not needed — implementation was clean on first pass | N/A |

## Task Commits

| Task | Commit | Files |
|------|--------|-------|
| ChatPanel RED test | `dba066a` | `frontend/__tests__/ChatPanel.test.tsx` |
| ChatPanel GREEN impl | `ed3cad8` | `frontend/src/components/ChatPanel.tsx`, `frontend/__tests__/ChatPanel.test.tsx` (test fixes) |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] jsdom `scrollIntoView` not a function**
- **Found during:** GREEN phase test run
- **Issue:** `messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })` threw `TypeError: messagesEndRef.current?.scrollIntoView is not a function` in jsdom (Test 1 through 5)
- **Fix:** Added `typeof el.scrollIntoView === 'function'` guard before calling. Production browsers all support scrollIntoView; the guard is a no-op at runtime.
- **Files modified:** `frontend/src/components/ChatPanel.tsx`
- **Commit:** `ed3cad8`

**2. [Rule 1 - Bug] `Response` not defined in jsdom**
- **Found during:** GREEN phase test run (Test 2)
- **Issue:** Test used `new Response(...)` to mock the fetch response, but jsdom does not expose the `Response` constructor
- **Fix:** Changed to a plain object mock `{ status: 200, ok: true, json: async () => ({...}) }` cast as `unknown as Response`
- **Files modified:** `frontend/__tests__/ChatPanel.test.tsx`
- **Commit:** `ed3cad8`

**3. [Rule 1 - Bug] `getByText` multiple elements match in Tests 3/4**
- **Found during:** GREEN phase test run
- **Issue:** Tests for badge text used `getByText(/Added NVDA/i)` which matched both the message bubble text ("Added NVDA to your watchlist.") and the badge span ("Added NVDA"), causing "Found multiple elements" error
- **Fix:** Test 3 used exact badge text `Bought 5 AAPL @ $190.00` which is unique; Test 4 used `getAllByText` + verified a `<span>` match
- **Files modified:** `frontend/__tests__/ChatPanel.test.tsx`
- **Commit:** `ed3cad8`

**4. [Rule 1 - Bug] Jest mock hoisting — `defaultMessages` before initialization**
- **Found during:** RED phase test run (test suite failed to run)
- **Issue:** `jest.mock('swr', ...)` factory referenced `defaultMessages` const before its `const` declaration; Jest hoists mock factories, causing a TDZ ReferenceError
- **Fix:** Moved `jest.mock('swr', ...)` to use `jest.fn()` with no initial return value; set `mockReturnValue` in each `beforeEach` after variable initialization
- **Files modified:** `frontend/__tests__/ChatPanel.test.tsx`
- **Commit:** `dba066a` (RED phase), refined in `ed3cad8`

## Known Stubs

None. ChatPanel wires to real API endpoints (`GET /api/chat/` added in Plan 01, `POST /api/chat/` from Phase 2). No hardcoded empty data flows to rendering.

## Threat Surface Scan

No new network endpoints, auth paths, or file access patterns introduced by this plan. `GET /api/chat/` was added by Plan 01. The threat model T-4-02 (XSS via message rendering) and T-4-04 (badge text from LLM output) are fully mitigated as verified by tests 3–5.

## Self-Check
