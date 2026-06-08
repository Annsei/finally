# Phase 3: Frontend Foundation - Context

**Gathered:** 2026-06-06
**Status:** Ready for planning

<domain>
## Phase Boundary

Build the Next.js frontend from scratch: scaffold the project (Pages Router, static export), wire up the SSE `EventSource` connection with Zustand state management, implement the header (live portfolio value, cash, connection status dot), and build the watchlist panel (compact table rows, price flash animation, sparkline mini-charts via Lightweight Charts). Phase 4 picks up main chart, heatmap, positions table, trade bar, and chat UI.

Phase 3 delivers FE-01 through FE-08.

</domain>

<decisions>
## Implementation Decisions

### Next.js Project Structure
- **D-01:** Pages Router (not App Router) — simpler, battle-tested with `output: 'export'`, no dynamic-route constraints, no RSC complexity
- **D-02:** Single-page layout: `pages/index.tsx` contains the full dashboard; `pages/_app.tsx` stays minimal (global styles, font imports only)

### State Management
- **D-03:** Zustand for SSE price data — components subscribe to specific slices so only affected components re-render on each 500ms price tick. Connection status also in the Zustand store.
- **D-04:** REST endpoint data (portfolio positions, watchlist list, chat history) fetched via SWR or plain fetch on demand — NOT in Zustand. Clean split: streaming data in Zustand, request-response data fetched where needed.

### Charting Library
- **D-05:** TradingView Lightweight Charts for all charts — sparklines (Phase 3) and main ticker chart (Phase 4). Canvas-based, ~40KB, financial-specific. One library across the entire app — no mixing.
- **D-06:** Sparklines implemented as Lightweight Charts mini instances (one per watchlist row), not SVG path math. Handles live updates natively, consistent with main chart.

### Price Flash Animation
- **D-07:** CSS class toggle approach: add `.flash-up` (green) or `.flash-down` (red) class on each price update, remove after 500ms via `setTimeout`. Tailwind custom keyframe or transition handles the fade. No inline style updates.

### Watchlist Panel
- **D-08:** Compact table rows (Bloomberg-style) — all 10 tickers visible at once without scrolling. No card grid.
- **D-09:** Column order: Symbol | Price | Change% | Sparkline (left to right)
- **D-10:** Selected ticker: 2px left accent bar in `#ecad0a` (accent yellow) + subtly lighter row background. Not a full background swap.

### Claude's Discretion
- Project scaffold method (`create-next-app` flags, TypeScript config, ESLint setup)
- SWR vs plain `fetch` + `useEffect` for REST endpoints — either works given the "on demand" decision
- Internal folder structure within `frontend/src/` (e.g., `hooks/`, `components/`, `stores/`, `types/`)
- `next.config.ts` specifics beyond `output: 'export'` (image optimization, etc.)
- Font choice (system font stack or a monospace like JetBrains Mono — fits the terminal feel)
- Tailwind config details (custom colors are locked but plugin/preset choices are discretionary)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Frontend Design Spec
- `planning/PLAN.md §10` — Frontend design: full layout description, component list, technical notes (EventSource, charting library preference, Tailwind, price flash)
- `planning/PLAN.md §2` — User experience: what the user sees on first launch, visual design, color scheme
- `planning/PLAN.md §3` — Architecture overview: static Next.js export, single container, single port

### Requirements
- `.planning/REQUIREMENTS.md` — FE-01 through FE-08: the 8 requirements this phase must satisfy
- `.planning/ROADMAP.md §Phase 3` — success criteria and key constraints for this phase

### API Contract (what the frontend consumes)
- `planning/PLAN.md §8` — API endpoints table: SSE stream, portfolio, watchlist, chat endpoints
- `planning/PLAN.md §6` — Market data / SSE: event format (ticker, price, previous_price, timestamp, direction)
- `backend/app/market/stream.py` — SSE endpoint implementation: exact event format the `EventSource` will receive
- `backend/app/market/models.py` — `PriceUpdate` dataclass: ticker, price, previous_price, timestamp + computed direction/change_percent
- `backend/app/routes/portfolio.py` — portfolio endpoint response shape (cash, total_value, positions)
- `backend/app/routes/watchlist.py` — watchlist endpoint response shape (ticker list with prices)

### Environment & Config
- `planning/PLAN.md §5` — Environment variables: no frontend-specific vars; all API calls same-origin `/api/*`
- `planning/PLAN.md §11` — Docker: multi-stage build (Node 20 builds frontend, Python serves static output)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `backend/app/market/models.py` — `PriceUpdate` defines the SSE event shape: `ticker`, `price`, `previous_price`, `timestamp`, `change`, `change_percent`, `direction` (`"up"` | `"down"` | `"flat"`) — use `direction` to pick flash class
- `backend/app/market/seed_prices.py` — `SEED_PRICES` dict: the 10 default tickers with realistic seed prices — useful for TypeScript type definitions and mock data in frontend tests

### Established Patterns (Backend — for interface reference)
- SSE endpoint at `GET /api/stream/prices` already implemented in `backend/app/market/stream.py` — emits JSON events; frontend uses native `EventSource` and `JSON.parse(event.data)`
- Route response patterns in `backend/app/routes/` use `JSONResponse` with plain dicts — no snake_to_camel conversion, frontend must handle Python `snake_case` field names

### Integration Points
- `GET /api/stream/prices` — SSE connection; one `EventSource` instance at app root, feed into Zustand store
- `GET /api/portfolio` — fetch on page load + after each trade; updates positions display
- `GET /api/watchlist` — fetch on page load; used to initialize the watchlist panel ticker list
- `GET /api/portfolio/history` — fetch for P&L chart (Phase 4)
- `POST /api/portfolio/trade` — trade execution (Phase 4)
- `POST /api/watchlist` / `DELETE /api/watchlist/{ticker}` — watchlist management (Phase 4)
- `POST /api/chat` — AI chat (Phase 4)
- All API calls same-origin `/api/*` — no CORS, no base URL config needed

### Frontend Starting Point
- `frontend/` directory does NOT exist — Phase 3 creates it from scratch
- Build output target: `frontend/out/` (Next.js static export default) → Dockerfile copies to `backend/static/`

</code_context>

<specifics>
## Specific Ideas

- **Left accent bar detail:** 2px `border-left` or absolutely-positioned `<div>` in `#ecad0a` on the selected watchlist row — exact implementation to agent discretion, but the 2px yellow bar is the required visual
- **Flash class names:** `.flash-up` with brief green background highlight, `.flash-down` with red, fading over ~500ms via CSS `transition: background-color`. Add to the price `<td>` or price `<span>`, not the whole row.
- **10 tickers always visible:** Watchlist panel height should accommodate all 10 rows without a scrollbar under normal conditions
- **Connection status dot:** Green (connected), yellow (reconnecting/EventSource CONNECTING state), red (EventSource ERROR state after reconnect attempts fail). Small colored dot in the header.
- **Zustand store shape (suggested):** `{ prices: Record<string, PriceUpdate>, connectionStatus: 'connected' | 'reconnecting' | 'disconnected', setPrices: ..., setConnectionStatus: ... }`

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within Phase 3 scope.

</deferred>

---

*Phase: 3-Frontend Foundation*
*Context gathered: 2026-06-06*
