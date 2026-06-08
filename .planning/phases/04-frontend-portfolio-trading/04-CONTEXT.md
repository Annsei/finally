# Phase 4: Frontend Portfolio & Trading - Context

**Gathered:** 2026-06-06
**Status:** Ready for planning

<domain>
## Phase Boundary

Build the complete portfolio and trading UI that fills the Phase 3 placeholder: main ticker chart, portfolio heatmap (CSS treemap), P&L chart, positions table, trade bar, and AI chat panel. Wires all these panels into the 3-column dashboard layout established by Phase 3 (watchlist | content | chat).

Also requires adding `GET /api/chat` to the backend (Phase 2 only has POST) to load conversation history on mount.

Phase 4 delivers FE-09 through FE-15.

</domain>

<decisions>
## Implementation Decisions

### Dashboard Layout
- **D-01:** 3-column layout: Watchlist (~200px fixed) | Content (flex-1) | Chat (~320px fixed, always visible)
- **D-02:** Center column stack (top to bottom): Main chart (tall, selected ticker) → row of [Heatmap | P&L chart] → Trade bar → Positions table
- **D-03:** On page load with no selected ticker: auto-select the first watchlist ticker (e.g. AAPL) so the main chart is never blank
- **D-04:** Trade bar is positioned above the positions table in the center column

### Portfolio Heatmap (Treemap)
- **D-05:** CSS flexbox implementation — no extra library (no d3, no visx, no recharts). Approximate area proportions using `flex-basis` or `width%` calculated from portfolio weight.
- **D-06:** Tile content: ticker symbol + current value + unrealized P&L% displayed in each tile
- **D-07:** Color: green = profit, red = loss; intensity proportional to P&L% (e.g., deeper red = bigger loss)
- **D-08:** Empty state (no positions): "No positions yet. Use the trade bar to buy shares."

### Chat Panel
- **D-09:** Open by default on page load; collapsible via a toggle button
- **D-10:** Action confirmations (trades/watchlist changes executed by AI) display as a separate badge/pill below the assistant's message bubble — visually distinct from the text response
- **D-11:** Load last N messages from DB on mount — requires adding `GET /api/chat` (or `/api/chat/history`) endpoint to the backend. Planner must include this backend task.

### Trade Bar
- **D-12:** Clicking a ticker in the watchlist auto-fills the trade bar's ticker input (uses the existing `selectedTicker` state from `index.tsx`)
- **D-13:** After a successful trade: optimistic update + immediate SWR revalidation of `/api/portfolio` (matches ROADMAP constraint)
- **D-14:** Trade errors (e.g. insufficient cash): inline error message below the trade bar inputs, cleared on next attempt

### P&L Chart
- **D-15:** Use TradingView Lightweight Charts (established in D-05 of Phase 3 context) — same library already committed for sparklines and main chart
- **D-16:** Poll `GET /api/portfolio/history` every 30 seconds (not SSE — ROADMAP explicitly allows this)

### Claude's Discretion
- Exact CSS class names and Tailwind utilities for the heatmap tiles
- Number of messages to load for chat history (N — suggest last 20)
- Collapse animation for the chat panel (slide or fade)
- Column and header styling of the positions table (follows established Bloomberg-style compact table from Phase 3)
- Internal component file names and folder structure within `frontend/src/components/`
- Quantity input behavior (integer only vs fractional shares — see PLAN.md §2 which says fractional shares supported)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase 3 Decisions (locked, carry forward)
- `.planning/phases/03-frontend-foundation/03-CONTEXT.md` — All Phase 3 decisions: Pages Router (D-01), Zustand for SSE (D-03), SWR/fetch for REST (D-04), TradingView Lightweight Charts (D-05), compact Bloomberg-style table (D-08), selected ticker 2px yellow accent (D-10)

### Frontend Design Spec
- `planning/PLAN.md §10` — Full frontend layout, component list, technical notes (EventSource, charting library, Tailwind, price flash)
- `planning/PLAN.md §2` — UX details: what the user sees, visual design, color scheme, all panel descriptions
- `planning/PLAN.md §3` — Architecture: static Next.js export, single container, single port

### Requirements
- `.planning/REQUIREMENTS.md` — FE-09 through FE-15: the 7 requirements this phase must satisfy
- `.planning/ROADMAP.md §Phase 4` — Success criteria and key constraints (treemap library options, P&L polling interval, optimistic update requirement)

### API Contract (what Phase 4 frontend consumes)
- `planning/PLAN.md §8` — All API endpoints (portfolio, watchlist, chat, history)
- `backend/app/routes/portfolio.py` — Portfolio endpoint response shape (cash, total_value, positions with unrealized P&L)
- `backend/app/routes/chat.py` — POST /api/chat: request/response shape; note: GET /api/chat for history does NOT exist yet — Phase 4 planner must add it
- `backend/app/routes/watchlist.py` — Watchlist response shape

### Existing Frontend Code
- `frontend/src/pages/index.tsx` — Current dashboard layout (Header + WatchlistPanel + selectedTicker state); Phase 4 fills the placeholder comment
- `frontend/src/stores/priceStore.ts` — Zustand store: `usePriceStore`, `useTicker(ticker)` per-ticker selector
- `frontend/src/components/WatchlistPanel.tsx` — Selected ticker pattern, accent bar implementation
- `frontend/src/components/SparklineChart.tsx` — Lightweight Charts mini instance pattern — reference for main chart and P&L chart

### Environment & Config
- `planning/PLAN.md §5` — No frontend env vars; all API calls same-origin `/api/*`

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `frontend/src/stores/priceStore.ts` — `useTicker(ticker)` selector for per-ticker price updates; use in positions table for live current price column
- `frontend/src/hooks/usePriceStream.ts` — already initialized at root in `index.tsx`; Phase 4 does NOT create another EventSource
- `frontend/src/components/SparklineChart.tsx` — Lightweight Charts instance pattern; reference for main ticker chart component (larger version) and P&L line chart
- `frontend/src/lib/fetcher.ts` — SWR fetcher utility; use for all REST data fetching (portfolio, history, chat)
- `frontend/src/components/Header.tsx` — SWR usage pattern for live portfolio value; follow same pattern for positions/portfolio

### Established Patterns
- **Zustand per-ticker selector:** `useTicker(ticker)` returns single atom — prevents re-renders on other tickers' updates. Use in positions table rows.
- **SWR for REST:** fetcher from `frontend/src/lib/fetcher.ts`, pass `/api/*` path — no base URL config needed
- **Lightweight Charts instances:** created inside `useEffect` with ref to container `<div>`, cleaned up on unmount — follow SparklineChart pattern
- **CSS class flash:** Phase 3 implements `.flash-up` / `.flash-down` in `globals.css`; reuse in positions table for live price column
- **`selectedTicker` state:** lives in `index.tsx`, passed as prop — use same `onSelectTicker` prop pattern

### Integration Points
- `index.tsx`: add three new column areas to the existing `flex gap-4 p-4` layout
- `selectedTicker` state in `index.tsx`: pass to MainChart and TradeBar components
- `GET /api/portfolio` — fetch on mount + after each trade; provides positions, cash, total_value
- `GET /api/portfolio/history` — fetch on mount + poll every 30s for P&L chart
- `POST /api/portfolio/trade` — trade execution from TradeBar
- `GET /api/chat` — **must be added to backend** — load conversation history on mount
- `POST /api/chat` — send user message; response includes message + trades[] + watchlist_changes[]
- `POST /api/watchlist` / `DELETE /api/watchlist/{ticker}` — AI chat may trigger these; frontend reflects changes after POST /api/chat response

</code_context>

<specifics>
## Specific Ideas

- **Heatmap tile sizing:** Compute `widthPercent = (positionValue / totalPortfolioValue) * 100` and apply as `width: ${widthPercent}%` with a minimum tile width so small positions remain clickable
- **Heatmap color intensity:** Map P&L% to opacity or saturation: e.g., `rgba(34, 197, 94, 0.3)` for small gains to `rgba(34, 197, 94, 1.0)` for large — creates visual depth
- **Trade bar auto-fill:** `selectedTicker` passed as `defaultTicker` prop; use `useEffect` to sync ticker input when `selectedTicker` changes
- **Chat badge design:** Pill/badge with accent yellow border for buy actions, red for sell, neutral for watchlist changes — consistent with the terminal color palette
- **Optimistic trade update:** On submit, immediately call SWR `mutate()` on `/api/portfolio` with a manually computed optimistic state, then revalidate on API response
- **GET /api/chat endpoint:** Backend needs to return array of `{role, content, actions, created_at}` ordered by `created_at` asc; limit to last 20; planner adds this to Phase 4 work

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within Phase 4 scope.

</deferred>

---

*Phase: 4-Frontend Portfolio & Trading*
*Context gathered: 2026-06-06*
