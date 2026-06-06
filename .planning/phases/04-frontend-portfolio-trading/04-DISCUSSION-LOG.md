# Phase 4: Frontend Portfolio & Trading - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-06
**Phase:** 4-Frontend Portfolio & Trading
**Areas discussed:** Dashboard layout, Treemap approach, Chat panel behavior, Trade bar wiring

---

## Dashboard Layout

| Option | Description | Selected |
|--------|-------------|----------|
| 2-col: Watchlist left, everything else right | Chat as overlay/sidebar | |
| 3-col: Watchlist \| Content \| Chat always visible | Bloomberg-style dense layout | ✓ |
| 2-col + collapsible chat overlay | Chat slides in from right edge | |

**User's choice:** 3-col with watchlist ~200px, chat ~320px, content fills rest

**Follow-up — center column stack:**

| Option | Selected |
|--------|----------|
| Main chart → heatmap + P&L row → positions + trade bar | ✓ |
| Main chart → positions → heatmap + P&L + trade bar | |
| You decide | |

**Follow-up — column widths:**

| Option | Selected |
|--------|----------|
| Watchlist ~200px, Chat ~320px | ✓ |
| Watchlist ~240px, Chat ~380px | |
| You decide | |

**Follow-up — trade bar position:**

| Option | Selected |
|--------|----------|
| Above the positions table | ✓ |
| Below the positions table | |
| You decide | |

**Follow-up — no ticker selected state:**

| Option | Selected |
|--------|----------|
| Empty state with prompt | |
| Auto-select first watchlist ticker (e.g. AAPL) | ✓ |
| You decide | |

**Notes:** Auto-select ensures the main chart area is never blank on page load, better first impression.

---

## Treemap Approach

| Option | Description | Selected |
|--------|-------------|----------|
| CSS flexbox (no extra library) | Approximate proportions, zero deps | ✓ |
| d3-hierarchy / visx | Accurate squarified treemap, ~15-30KB extra | |
| recharts TreeMap | Would add a second charting library | |

**User's choice:** CSS flexbox/flex-wrap, no extra library. User explicitly said: "Use a lightweight CSS-based heatmap for MVP. Size tiles approximately by portfolio weight, color by unrealized P&L percentage, and show ticker/value/P&L in each tile. Avoid adding d3/visx/recharts in Phase 4."

**Follow-up — empty state:**

| Option | Selected |
|--------|----------|
| "No positions yet. Use the trade bar to buy shares." | ✓ |
| Grayed-out placeholder tiles | |
| You decide | |

**Notes:** User prioritized simplicity and zero new dependencies over visual accuracy of treemap proportions.

---

## Chat Panel Behavior

| Option | Description | Selected |
|--------|-------------|----------|
| Always open (never collapses) | Simpler, max demo impact | |
| Open by default, collapsible via button | Starts open, user can hide | ✓ |
| Collapsed by default, open via button | Focus on trading first | |

**User's choice:** Open by default with a toggle button to collapse

**Follow-up — action confirmations:**

| Option | Selected |
|--------|----------|
| Inline in assistant message bubble | |
| Separate badge/pill below message bubble | ✓ |
| You decide | |

**Follow-up — chat history on mount:**

| Option | Selected |
|--------|----------|
| Yes — load last N messages from DB | ✓ |
| No — start fresh each page load | |
| You decide | |

**Notes:** Loading history requires adding GET /api/chat to the backend — Phase 2 only implemented POST /api/chat. Planner must include this backend addition in Phase 4 work.

---

## Trade Bar Wiring

| Option | Description | Selected |
|--------|-------------|----------|
| Yes — auto-fill ticker on watchlist click | Uses existing selectedTicker state | ✓ |
| No — independent ticker input | User types manually | |

**User's choice:** Auto-fill ticker field from watchlist selection

**Follow-up — post-trade behavior:**

| Option | Selected |
|--------|----------|
| Optimistic update + immediate SWR revalidation | ✓ |
| Wait for API response then refresh | |
| You decide | |

**Follow-up — error handling:**

| Option | Selected |
|--------|----------|
| Inline error below trade bar inputs | ✓ |
| Error in chat panel | |
| You decide | |

**Notes:** Optimistic update matches the ROADMAP constraint ("optimistic update on submit, reconcile on API response"). Inline errors are co-located with the trade action.

---

## Claude's Discretion

- Exact CSS class names and Tailwind utilities for heatmap tiles
- Number of messages to load for chat history (suggested: last 20)
- Collapse animation style for chat panel (slide or fade)
- Column and header styling of positions table
- Internal component file names and folder structure
- Quantity input behavior (integer vs fractional — PLAN.md says fractional supported)

## Deferred Ideas

None — discussion stayed within Phase 4 scope.
