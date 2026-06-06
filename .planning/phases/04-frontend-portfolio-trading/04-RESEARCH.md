# Phase 4: Frontend Portfolio & Trading - Research

**Researched:** 2026-06-06
**Domain:** Next.js 16 (Pages Router, static export) + TradingView Lightweight Charts v5 + SWR v2 + Zustand v5
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** 3-column layout: Watchlist (~200px fixed) | Content (flex-1) | Chat (~320px fixed, always visible)
- **D-02:** Center column stack (top to bottom): Main chart (tall) → row of [Heatmap | P&L chart] → Trade bar → Positions table
- **D-03:** On page load with no selected ticker: auto-select the first watchlist ticker so the main chart is never blank
- **D-04:** Trade bar is positioned above the positions table in the center column
- **D-05:** Heatmap: CSS flexbox only — NO d3, visx, recharts, or external treemap library
- **D-06:** Heatmap tile content: ticker symbol + current value + unrealized P&L%
- **D-07:** Heatmap color: green = profit, red = loss; intensity proportional to P&L% magnitude
- **D-08:** Heatmap empty state: "No positions yet. Use the trade bar to buy shares."
- **D-09:** Chat panel open by default, collapsible via toggle button
- **D-10:** AI action confirmations display as badge/pill below assistant message bubble
- **D-11:** Load last N messages from DB on mount via new `GET /api/chat` endpoint
- **D-12:** Clicking a ticker auto-fills trade bar ticker input via `selectedTicker` state
- **D-13:** After successful trade: optimistic update + immediate SWR revalidation of `/api/portfolio`
- **D-14:** Trade errors: inline error message below inputs, cleared on next attempt
- **D-15:** P&L chart uses TradingView Lightweight Charts (same library already committed)
- **D-16:** Poll `GET /api/portfolio/history` every 30 seconds (not SSE)

### Claude's Discretion

- Exact CSS class names and Tailwind utilities for heatmap tiles
- Number of messages to load for chat history (suggest last 20)
- Collapse animation for chat panel (slide preferred)
- Column and header styling of positions table (follows established Bloomberg-style compact table)
- Internal component file names and folder structure within `frontend/src/components/`
- Quantity input behavior (fractional shares supported per PLAN.md §2)

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within Phase 4 scope.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| FE-09 | Main chart area shows larger price-over-time chart for selected ticker | Lightweight Charts v5 `addSeries(LineSeries)` pattern confirmed from SparklineChart.tsx; multiple independent instances use same `createChart` factory, each with its own `chart.remove()` cleanup |
| FE-10 | Portfolio heatmap (treemap) shows positions sized by portfolio weight, colored by P&L | CSS flexbox width-percent sizing confirmed; `rgba()` intensity mapping pattern specified in UI-SPEC |
| FE-11 | P&L chart shows total portfolio value over time from `GET /api/portfolio/history` | `AreaSeries` confirmed in Lightweight Charts v5 typings; `setData()` + `update()` API verified; 30s poll with `setInterval` + cleanup |
| FE-12 | Positions table shows ticker, qty, avg cost, current price, unrealized P&L, % change | `useTicker()` per-row selector confirmed; flash class pattern verified from WatchlistRow |
| FE-13 | Trade bar: ticker input, qty input, buy/sell buttons, instant market order execution | SWR `mutate` with `optimisticData` confirmed in installed swr@2.4.1; POST /api/portfolio/trade response shape verified from portfolio.py |
| FE-14 | AI chat panel: message input, scrolling history, loading indicator | GET /api/chat endpoint does NOT exist yet — must be added to backend chat.py; response shape fully specified |
| FE-15 | Chat panel shows inline confirmations for trades/watchlist changes by AI | POST /api/chat response `trades` + `watchlist_changes` arrays confirmed from chat.py; badge pattern specified in UI-SPEC |
</phase_requirements>

---

## Summary

Phase 4 builds 7 new UI panels onto the Phase 3 dashboard skeleton. The existing `index.tsx` has a single placeholder comment where all Phase 4 content goes. The codebase already has all necessary patterns established: Lightweight Charts v5 instance lifecycle (`SparklineChart.tsx`), Zustand per-ticker selector (`useTicker`), SWR fetcher, and flash animation classes.

The one backend addition is `GET /api/chat` — Phase 2 only implemented `POST /api/chat`. The new endpoint is a simple DB query returning the last 20 `chat_messages` rows in ascending order; the existing `create_chat_router` factory function simply adds a second route handler. The `conftest.py` `chat_client` fixture already seeds the full app and will cover the new endpoint with minimal additions.

The CSS flexbox treemap requires computing `width: X%` from `(positionValue / totalPortfolioValue) * 100` with a `min-width: 64px` floor so small positions remain clickable. Color intensity maps P&L% magnitude to an alpha value (0.3 at ≈0% → 1.0 at high magnitude). The Lightweight Charts `AreaSeries` (exported as `AreaSeries`) handles the P&L chart with `topColor`/`bottomColor` set conditionally based on whether total value is above or below the starting baseline.

**Primary recommendation:** Follow the SparklineChart instance pattern exactly for MainChart and PnLChart components. Use SWR `mutate` bound mutator with `optimisticData` for the trade bar. Add `GET /api/chat/history` as a new route inside `create_chat_router`. Use CSS flexbox widths for the heatmap — no library.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Main ticker chart | Browser/Client | — | Pure canvas render from Zustand price store; no server state needed beyond initial data |
| Portfolio heatmap | Browser/Client | API/Backend | Client renders CSS tiles from `GET /api/portfolio` data; backend owns P&L calculation |
| P&L line chart | Browser/Client | API/Backend | Client renders from polled `GET /api/portfolio/history`; backend owns snapshot logic |
| Positions table (live prices) | Browser/Client | — | `useTicker()` from Zustand for live current-price column; static fields from SWR `/api/portfolio` |
| Trade execution | API/Backend | Browser/Client | Server validates cash/shares; client shows optimistic UI, reconciles on response |
| Trade bar form | Browser/Client | — | Client-side form state; submits to backend via `POST /api/portfolio/trade` |
| Chat history load | API/Backend | Browser/Client | `GET /api/chat` returns DB rows; client renders on mount |
| Chat message send | API/Backend | Browser/Client | Server calls LLM, auto-executes trades/watchlist changes; client shows loading state |
| Chat action badges | Browser/Client | — | Client maps `trades[]`/`watchlist_changes[]` from POST /api/chat response to badge components |

---

## Standard Stack

### Core (already installed — zero new dependencies required)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `lightweight-charts` | 5.2.0 | Main chart + P&L chart canvas rendering | Already committed (Phase 3), TradingView MIT, v5 API confirmed in typings |
| `swr` | 2.4.1 | REST data fetching for portfolio, history, chat | Already committed (Phase 3), `optimisticData` mutator confirmed in installed types |
| `zustand` | 5.0.14 | Price store + `useTicker` per-ticker selector | Already committed (Phase 3) |
| `next` | 16.2.7 | Pages Router, static export | Project foundation — `output: 'export'` confirmed in next.config.js |
| Tailwind CSS | 3.4.19 | Styling — all tokens already in tailwind.config.js | Already committed (Phase 3) |

### Supporting (optional)

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `lucide-react` | 1.17.0 (registry) | Chat panel collapse/expand chevron icon | Use ONLY if a text/CSS chevron is insufficient; not currently installed — prefer text character `‹›` or CSS border triangle to avoid a new install |

**Installation:** No new packages. All dependencies already in `frontend/package.json`.

---

## Package Legitimacy Audit

Phase 4 introduces **zero new npm packages**. All libraries (`lightweight-charts`, `swr`, `zustand`, `next`, Tailwind) were vetted and installed in Phases 1–3. The UI-SPEC notes `lucide-react` as optional; prefer CSS/text to avoid adding a dependency.

| Package | Registry | Already Installed | Disposition |
|---------|----------|------------------|-------------|
| lightweight-charts | npm | Yes (5.2.0) | Approved — Phase 3 |
| swr | npm | Yes (2.4.1) | Approved — Phase 3 |
| zustand | npm | Yes (5.0.14) | Approved — Phase 3 |
| lucide-react | npm | No | Avoid — use CSS/text chevron instead |

*slopcheck was unavailable at research time. All packages above were installed in prior phases; only lucide-react is new and is flagged as avoidable.*

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

---

## Architecture Patterns

### System Architecture Diagram

```
SSE /api/stream/prices
        |
        v
  Zustand priceStore (prices: PriceMap)
        |
   ┌────┴─────────────────────────┐
   |                              |
useTicker('AAPL')            useTicker('MSFT')
   |                              |
MainChart              PositionsTable rows (live price col)
(selected ticker)


SWR /api/portfolio (5s refresh + optimistic mutate after trade)
        |
   ┌────┴──────────────────┐
   |                       |
PortfolioHeatmap      PositionsTable (qty, avg_cost, unrealized_pnl)
(widthPercent, color)


SWR /api/portfolio/history (30s poll)
        |
      PnLChart (AreaSeries)


SWR /api/chat → GET (mount once)
        |
ChatPanel (history render)

User action (chat submit)
        |
POST /api/chat → response {message, trades[], watchlist_changes[]}
        |
   ┌────┴─────────────────────────┐
   |                              |
ChatPanel (message + badges)   mutate('/api/portfolio')
                                 (revalidate — trades may have changed positions)


User action (trade submit)
        |
optimistic mutate('/api/portfolio')  →  POST /api/portfolio/trade
        |                                      |
immediate UI update                    response or error
                                              |
                               SWR revalidate('/api/portfolio')
```

### Recommended Project Structure

```
frontend/src/
├── components/
│   ├── Header.tsx              # Existing (Phase 3)
│   ├── WatchlistPanel.tsx      # Existing (Phase 3)
│   ├── WatchlistRow.tsx        # Existing (Phase 3)
│   ├── SparklineChart.tsx      # Existing (Phase 3)
│   ├── MainChart.tsx           # NEW: Larger Lightweight Charts instance for selected ticker
│   ├── PortfolioHeatmap.tsx    # NEW: CSS flexbox treemap
│   ├── PnLChart.tsx            # NEW: Lightweight Charts AreaSeries, 30s poll
│   ├── PositionsTable.tsx      # NEW: HTML table, useTicker per row, flash on current price
│   ├── TradeBar.tsx            # NEW: Form with optimistic SWR mutate
│   └── ChatPanel.tsx           # NEW: Collapsible panel, history + send + badges
├── pages/
│   └── index.tsx               # Existing — add 3-column layout wiring
├── stores/
│   └── priceStore.ts           # Existing (Phase 3) — no changes
├── hooks/
│   └── usePriceStream.ts       # Existing (Phase 3) — no changes
├── lib/
│   └── fetcher.ts              # Existing (Phase 3) — no changes
├── types/
│   └── market.ts               # Existing — add ChatMessage, PortfolioHistory types
└── styles/
    └── globals.css             # Existing — no changes needed
```

### Pattern 1: Lightweight Charts Multiple Independent Instances

**What:** Each chart component (MainChart, PnLChart, SparklineChart) creates its own independent `IChartApi` instance inside a `useEffect`. Each must call `chart.remove()` on unmount. Multiple instances on the same page are fully supported.

**When to use:** Any canvas chart component.

**Key insight from typings:** `autoSize: true` enables ResizeObserver-driven resize without explicit `applyOptions({width, height})` calls. For MainChart (full-width), this avoids needing to measure container width explicitly.

```typescript
// Source: SparklineChart.tsx (established Phase 3 pattern) + lightweight-charts typings.d.ts
// MainChart.tsx — larger instance, follows same lifecycle

import { useEffect, useRef } from 'react';
import { createChart, LineSeries } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, UTCTimestamp } from 'lightweight-charts';

export default function MainChart({ ticker }: { ticker: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const tickCountRef = useRef<number>(0);
  const priceUpdate = useTicker(ticker);

  // Mount: create chart once
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      autoSize: true,          // ResizeObserver handles container sizing
      layout: { background: { color: 'transparent' }, textColor: '#8b949e' },
      grid: { vertLines: { color: '#30363d' }, horzLines: { color: '#30363d' } },
      rightPriceScale: { borderColor: '#30363d' },
      timeScale: { borderColor: '#30363d' },
    });
    const series = chart.addSeries(LineSeries, { color: '#209dd7', lineWidth: 2 });
    chartRef.current = chart;
    seriesRef.current = series as ISeriesApi<'Line'>;
    return () => { chart.remove(); chartRef.current = null; seriesRef.current = null; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Ticker change: reset series data when ticker changes
  useEffect(() => {
    seriesRef.current?.setData([]);
    tickCountRef.current = 0;
  }, [ticker]);

  // Price update: append point
  useEffect(() => {
    if (!seriesRef.current || !priceUpdate) return;
    tickCountRef.current += 1;
    seriesRef.current.update({ time: tickCountRef.current as UTCTimestamp, value: priceUpdate.price });
  }, [priceUpdate]);

  return <div ref={containerRef} style={{ width: '100%', height: '240px' }} />;
}
```

**SSR guard:** `createChart` only runs inside `useEffect` — never at module scope. This is the established pattern from `SparklineChart.tsx` and prevents `window is not defined` errors in Next.js static export pre-render. [VERIFIED: SparklineChart.tsx codebase]

### Pattern 2: PnLChart with AreaSeries

**What:** Renders total portfolio value over time from `GET /api/portfolio/history`. Uses `AreaSeries` (not `LineSeries`) to fill the area below/above the baseline. Color flips based on direction relative to initial value.

```typescript
// Source: lightweight-charts typings.d.ts — AreaSeries, setData, topColor/bottomColor
import { createChart, AreaSeries } from 'lightweight-charts';
import type { ISeriesApi, UTCTimestamp } from 'lightweight-charts';

// In useEffect on mount:
const series = chart.addSeries(AreaSeries, {
  lineColor: '#209dd7',
  topColor: 'rgba(34, 197, 94, 0.4)',    // green above baseline
  bottomColor: 'rgba(34, 197, 94, 0.0)',
  lineWidth: 2,
});

// When data arrives from GET /api/portfolio/history:
// response.snapshots: [{total_value: number, recorded_at: ISO string}]
// Use monotonic counter as time (same as SparklineChart — avoids real timestamp parsing):
const points = snapshots.map((s, i) => ({
  time: (i + 1) as UTCTimestamp,
  value: s.total_value,
}));
series.setData(points);

// 30s poll via setInterval + cleanup:
useEffect(() => {
  const id = setInterval(fetchAndUpdateChart, 30_000);
  return () => clearInterval(id);
}, []);
```

**Note on AreaSeries colors:** The P&L chart should show green when portfolio is growing, red when declining. Simplest implementation: always use green area series (re-color to red when latest value < first value using `applyOptions`). This avoids splitting the dataset.

### Pattern 3: CSS Flexbox Treemap

**What:** Compute `widthPercent` from portfolio weight; apply `rgba()` background color with intensity proportional to P&L%.

**Math:**
- `widthPercent = (position.quantity * position.current_price) / totalPortfolioValue * 100`
- `alpha = Math.min(Math.abs(pnlPct) / 20, 1.0)` — saturates at ±20% P&L (tune as needed)
- Profit tile: `rgba(34, 197, 94, alpha)` | Loss tile: `rgba(239, 68, 68, alpha)` | Flat: `#1a1a2e`
- Minimum width: `64px` (from UI-SPEC)

```tsx
// Source: CONTEXT.md specifics + UI-SPEC §Color
<div className="flex flex-wrap gap-1 p-2 bg-terminal-surface rounded">
  {positions.map((pos) => {
    const posValue = pos.quantity * pos.current_price;
    const widthPct = (posValue / totalValue) * 100;
    const alpha = Math.min(Math.abs(pos.pnl_pct) / 20, 1.0);
    const bg = pos.pnl_pct > 0
      ? `rgba(34, 197, 94, ${Math.max(alpha, 0.3)})`
      : pos.pnl_pct < 0
        ? `rgba(239, 68, 68, ${Math.max(alpha, 0.3)})`
        : '#1a1a2e';
    return (
      <div
        key={pos.ticker}
        style={{ width: `${widthPct}%`, minWidth: '64px', backgroundColor: bg }}
        className="p-2 text-terminal-text rounded text-xs"
      >
        <div className="font-semibold">{pos.ticker}</div>
        <div className="tabular-nums">${posValue.toFixed(0)}</div>
        <div className={`tabular-nums ${pos.pnl_pct >= 0 ? 'text-terminal-up' : 'text-terminal-down'}`}>
          {pos.pnl_pct > 0 ? '+' : ''}{pos.pnl_pct.toFixed(2)}%
        </div>
      </div>
    );
  })}
</div>
```

**Note:** `flex-wrap` is intentional — tiles wrap to next row when total exceeds 100%. For a single-row treemap, add `overflow-hidden` and accept truncation. The UI-SPEC says "approximately area proportions" — wrapping is acceptable.

### Pattern 4: SWR Optimistic Trade Update

**What:** On trade submit, immediately update the local portfolio cache with a computed optimistic state, then POST the trade, then revalidate.

```typescript
// Source: swr 2.4.1 _internal/types.d.ts — MutatorOptions.optimisticData verified
const { data: portfolio, mutate } = useSWR<PortfolioResponse>('/api/portfolio', fetcher);

const handleTrade = async (side: 'buy' | 'sell') => {
  const price = portfolio?.positions.find(p => p.ticker === ticker)?.current_price ?? 0;
  const cost = qty * price;

  await mutate(
    // Async mutator: POST the actual trade
    async (current) => {
      const res = await fetch('/api/portfolio/trade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker, quantity: qty, side }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.error ?? 'Trade failed');
      }
      return current; // will be replaced by revalidation
    },
    {
      // Optimistic: compute immediately without waiting for API
      optimisticData: (current) => {
        if (!current) return current;
        if (side === 'buy') {
          return { ...current, cash: current.cash - cost };
        } else {
          return { ...current, cash: current.cash + cost };
        }
      },
      rollbackOnError: true,
      revalidate: true,  // fetch fresh data after mutation completes
    }
  );
};
```

**Error handling:** Wrap in try/catch; on catch, display the error message inline below inputs. Clear error on next submit attempt (D-14).

### Pattern 5: GET /api/chat Backend Endpoint

**What:** New route handler added inside `create_chat_router`. Returns last 20 messages ordered ascending by `created_at`. Follows existing `chat_client` fixture pattern.

```python
# Source: backend/app/routes/chat.py — existing create_chat_router factory
# Add inside create_chat_router(), alongside the existing @router.post("/")

@router.get("/")
async def get_chat_history(request: Request) -> dict:
    """Return last 20 chat messages in ascending chronological order.
    
    Returns:
        {"messages": [{"role", "content", "actions", "created_at"}, ...]}
    """
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT role, content, actions, created_at
            FROM chat_messages
            WHERE user_id = 'default'
            ORDER BY created_at DESC
            LIMIT 20
            """
        ).fetchall()
        messages = list(reversed([
            {
                "role": row["role"],
                "content": row["content"],
                "actions": json.loads(row["actions"]) if row["actions"] else None,
                "created_at": row["created_at"],
            }
            for row in rows
        ]))
        return {"messages": messages}
    finally:
        conn.close()
```

**Frontend consumption:**

```typescript
// GET /api/chat response type to add to market.ts
export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  actions: {
    trades: TradeOutcome[];
    watchlist_changes: WatchlistOutcome[];
  } | null;
  created_at: string;
}

export interface ChatHistoryResponse {
  messages: ChatMessage[];
}
```

**On mount in ChatPanel:**
```typescript
const { data } = useSWR<ChatHistoryResponse>('/api/chat', fetcher);
// then also call mutate('/api/chat') after each POST /api/chat response to update history
```

### Pattern 6: Positions Table with Live Price Flash

**What:** Each row uses `useTicker(pos.ticker)` for the live current-price cell, reusing the exact flash pattern from `WatchlistRow.tsx`.

```tsx
// Source: WatchlistRow.tsx (Phase 3 established pattern)
function PositionsRow({ pos }: { pos: Position }) {
  const priceUpdate = useTicker(pos.ticker);
  const priceRef = useRef<HTMLTableCellElement>(null);
  const flashTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!priceUpdate || !priceRef.current || priceUpdate.direction === 'flat') return;
    const cell = priceRef.current;
    if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current);
    cell.classList.remove('animate-flash-up', 'animate-flash-down');
    void cell.offsetWidth; // force reflow
    const cls = priceUpdate.direction === 'up' ? 'animate-flash-up' : 'animate-flash-down';
    cell.classList.add(cls);
    flashTimeoutRef.current = setTimeout(() => cell.classList.remove(cls), 500);
    return () => { if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current); };
  }, [priceUpdate?.direction, priceUpdate?.timestamp]);

  // Use live current_price from store if available, fall back to portfolio data
  const currentPrice = priceUpdate?.price ?? pos.current_price;
  const liveUnrealizedPnl = (currentPrice - pos.avg_cost) * pos.quantity;
  const livePnlPct = pos.avg_cost > 0 ? ((currentPrice - pos.avg_cost) / pos.avg_cost) * 100 : 0;
  // ...
}
```

### Pattern 7: Chat Panel Collapse

**What:** Chat column collapses to a thin strip (toggle button only). Use CSS `width` transition.

```tsx
// Slide animation — CONTEXT D-09, Claude's discretion for animation
const [chatOpen, setChatOpen] = useState(true);

// In the layout div:
<div
  className={`shrink-0 overflow-hidden transition-all duration-300 border-l border-terminal-border ${
    chatOpen ? 'w-80' : 'w-8'
  }`}
>
  <button onClick={() => setChatOpen(!chatOpen)} className="...">
    {chatOpen ? '›' : '‹'}
  </button>
  {chatOpen && <ChatPanel />}
</div>
```

### Anti-Patterns to Avoid

- **Creating EventSource in Phase 4 components:** `usePriceStream()` is called ONCE in `index.tsx`. Phase 4 components consume `useTicker()` from the Zustand store — they do NOT create new EventSource connections.
- **Using `new Date()` for chart time values:** SparklineChart uses a monotonic counter (`tickCountRef`). MainChart must do the same for live SSE data. For `PnLChart` using historical snapshots, use array index + 1 as the time value to avoid timestamp parsing complexity.
- **Object selector in Zustand v5:** Never `usePriceStore((s) => ({ price: s.prices[ticker] }))` — creates new object on every render. Always use `useTicker(ticker)` which returns a scalar.
- **Calling `chart.remove()` twice:** Each chart's `useEffect` cleanup calls `remove()` once and nulls the ref. Multiple cleanups (e.g., StrictMode double-invoke) are safe because `remove()` on a null ref is guarded.
- **SWR `refreshInterval` fighting with optimistic update:** During trade submit, the `mutate` with `optimisticData` shows immediate state. The `refreshInterval: 5000` on `/api/portfolio` in Header.tsx will also re-fetch — this is fine; SWR deduplicates. Do NOT set `revalidate: false` on the trade mutate — we want fresh data after the trade.
- **Flexbox `width%` on tiles without `min-width`:** Small positions (< 1% weight) render as 0-pixel-wide invisible tiles. Always apply `minWidth: '64px'` via inline style (Tailwind's `min-w-16` = 64px also works).

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Canvas charting | Custom SVG/canvas charts | `lightweight-charts` v5 (`createChart`, `addSeries`) | Edge cases: canvas pixel ratio, resize, crosshair, pan/zoom handling |
| Optimistic UI state | Manual `useState` copies of portfolio | SWR `mutate` with `optimisticData` | Race conditions, rollback on error, deduplication with other SWR keys |
| Price subscription | Per-component `usePriceStore((s) => s.prices)` | `useTicker(ticker)` selector | Object selector triggers re-render on every ticker update (Zustand v5 pitfall) |
| Chat state management | Local `useState` arrays for message history | SWR for GET + local state for in-flight message | SWR handles deduplication; local state handles optimistic new message append |
| Chart resize handling | `ResizeObserver` manual impl | `autoSize: true` in createChart options | Built into Lightweight Charts v5; fires `chart.resize()` automatically |

**Key insight:** Lightweight Charts v5 handles all canvas complexity. SWR handles all cache invalidation complexity. The codebase patterns from Phase 3 are the canonical reference — copy them, don't reinvent.

---

## API Shapes (Verified from Codebase)

### GET /api/portfolio (verified from portfolio.py)

```json
{
  "cash": 10000.00,
  "total_value": 10000.00,
  "positions": [
    {
      "ticker": "AAPL",
      "quantity": 10.0,
      "avg_cost": 185.50,
      "current_price": 188.25,
      "unrealized_pnl": 27.50,
      "pnl_pct": 1.48
    }
  ]
}
```

[VERIFIED: backend/app/routes/portfolio.py — `get_portfolio` handler, lines 199-244]

### POST /api/portfolio/trade

**Request:** `{ "ticker": "AAPL", "quantity": 10, "side": "buy" }`

**Success response (200):**
```json
{ "status": "ok", "ticker": "AAPL", "side": "buy", "quantity": 10, "price": 188.25, "trade_id": "uuid" }
```

**Error response (400):**
```json
{ "error": "Insufficient cash" }
```

[VERIFIED: backend/app/routes/portfolio.py — `execute_trade` handler, lines 244-277]

### GET /api/portfolio/history (verified from portfolio.py)

```json
{
  "snapshots": [
    { "total_value": 10000.00, "recorded_at": "2026-06-06T10:00:00+00:00" }
  ]
}
```

[VERIFIED: backend/app/routes/portfolio.py — `get_portfolio_history` handler, lines 279-302]

### POST /api/chat (verified from chat.py)

**Request:** `{ "message": "Buy 5 shares of AAPL" }`

**Response:**
```json
{
  "message": "I've bought 5 shares of AAPL for you.",
  "trades": [
    { "status": "executed", "ticker": "AAPL", "side": "buy", "quantity": 5, "price": 188.25, "trade_id": "uuid" }
  ],
  "watchlist_changes": [
    { "status": "added", "ticker": "PYPL", "action": "add" }
  ]
}
```

[VERIFIED: backend/app/routes/chat.py — `chat` handler return, lines 254-258]

### GET /api/chat (to be added to backend)

**Proposed response:**
```json
{
  "messages": [
    { "role": "user", "content": "Buy some AAPL", "actions": null, "created_at": "ISO timestamp" },
    { "role": "assistant", "content": "I bought 5 AAPL.", "actions": { "trades": [...], "watchlist_changes": [...] }, "created_at": "ISO timestamp" }
  ]
}
```

[ASSUMED] — endpoint does not yet exist; shape derived from `chat_messages` schema and how `chat.py` writes rows.

---

## index.tsx Wiring

The current `index.tsx` has this placeholder:

```tsx
<div className="flex gap-4 p-4">
  <WatchlistPanel selectedTicker={selectedTicker} onSelectTicker={setSelectedTicker} />
  {/* Phase 4: main chart area, portfolio panels, and AI chat go here */}
</div>
```

Phase 4 fills this exactly:

```tsx
// Additions to index.tsx state:
const [chatOpen, setChatOpen] = useState(true);

// Additions: auto-select first ticker on mount (D-03)
const { data: watchlistData } = useSWR<WatchlistResponse>('/api/watchlist', fetcher);
useEffect(() => {
  if (!selectedTicker && watchlistData?.tickers?.length) {
    setSelectedTicker(watchlistData.tickers[0].ticker);
  }
}, [watchlistData, selectedTicker]);

// Layout becomes:
<div className="flex gap-4 p-4 h-[calc(100vh-52px)]">
  <WatchlistPanel selectedTicker={selectedTicker} onSelectTicker={setSelectedTicker} />

  {/* Center column: charts + trade bar + positions */}
  <div className="flex-1 flex flex-col gap-4 overflow-auto">
    <MainChart ticker={selectedTicker ?? ''} />
    <div className="flex gap-4">
      <PortfolioHeatmap />
      <PnLChart />
    </div>
    <TradeBar selectedTicker={selectedTicker} onTradeComplete={() => mutatePortfolio()} />
    <PositionsTable />
  </div>

  {/* Chat column: fixed width, collapsible */}
  <div className={`shrink-0 overflow-hidden transition-all duration-300 border-l border-terminal-border ${chatOpen ? 'w-80' : 'w-8'}`}>
    <ChatPanel open={chatOpen} onToggle={() => setChatOpen(!chatOpen)} />
  </div>
</div>
```

[VERIFIED: frontend/src/pages/index.tsx — existing structure]

---

## Common Pitfalls

### Pitfall 1: Lightweight Charts — Ticker Change Must Reset Series Data

**What goes wrong:** When `selectedTicker` changes, the MainChart `priceUpdate` effect fires for the new ticker, appending its first price point onto the existing series that still contains the old ticker's history. The chart shows a wildly discontinuous line.

**Why it happens:** The `priceUpdate` effect only appends (`series.update()`). A ticker change requires `series.setData([])` to clear.

**How to avoid:** In MainChart, add a separate `useEffect` with `[ticker]` dependency that calls `seriesRef.current?.setData([])` and resets `tickCountRef.current = 0` whenever ticker changes.

**Warning signs:** Chart shows a sudden price jump when switching tickers.

### Pitfall 2: Zustand v5 Object Selector Causes Infinite Re-Render

**What goes wrong:** `usePriceStore((s) => ({ price: s.prices[ticker] }))` creates a new object on every call, triggering re-render loops.

**Why it happens:** Zustand v5 uses `Object.is` equality. A new object literal is never equal to the previous one.

**How to avoid:** Always use the existing `useTicker(ticker)` selector which returns a scalar (`PriceUpdate | undefined`).

**Warning signs:** "Maximum update depth exceeded" React error in console.

### Pitfall 3: SWR Key Consistency — Use Exact String `/api/portfolio`

**What goes wrong:** Header.tsx uses `useSWR('/api/portfolio', ...)`. TradeBar mutates `'/api/portfolio'`. If any component uses a different key variant (e.g., `'/api/portfolio/'` with trailing slash), the bound mutate won't invalidate the Header's cache.

**Why it happens:** SWR treats each unique key as a separate cache entry.

**How to avoid:** Use the exported constant or the exact string `/api/portfolio` everywhere. Do not add trailing slashes.

**Warning signs:** Portfolio updates after trade don't appear in Header until next poll cycle.

### Pitfall 4: P&L Chart Time Values — Index vs Real Timestamp

**What goes wrong:** Using real ISO timestamps from `portfolio_snapshots.recorded_at` as chart time values causes gaps in the time axis (30-second intervals look like hours when chart auto-scales).

**Why it happens:** Lightweight Charts renders time on a proportional axis. Sparse real timestamps create large visual gaps.

**How to avoid:** Use array index (1, 2, 3...) as the `time` value for P&L chart, same as SparklineChart. The X axis becomes "snapshot count" rather than real time — acceptable for this use case.

**Alternative (better UX):** Parse `recorded_at` to Unix seconds and use as `UTCTimestamp`. The time scale will show actual clock time. This requires `new Date(snapshot.recorded_at).getTime() / 1000` cast to `UTCTimestamp`.

### Pitfall 5: Trade Error vs Network Error

**What goes wrong:** The SWR `mutate` async function throws on 4xx (we throw `new Error(err.error)`) but the optimistic update already showed cash change. If `rollbackOnError: true` is not set, the optimistic state remains incorrect.

**How to avoid:** Always pass `rollbackOnError: true` in the mutate options. SWR reverts `optimisticData` if the async mutator throws.

### Pitfall 6: Chat Panel Auto-Scroll

**What goes wrong:** New messages append at the bottom but the scroll position doesn't follow. User must manually scroll down to see new messages.

**Why it happens:** The scrollable div doesn't automatically scroll on content change.

**How to avoid:** Use a `messagesEndRef = useRef<HTMLDivElement>(null)` at the end of the message list. After each new message is appended, call `messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })` in a `useEffect` that watches the messages array length.

### Pitfall 7: `GET /api/chat` Route Conflicts with `POST /api/chat`

**What goes wrong:** The chat router uses `prefix="/api/chat"` and `@router.post("/")` — resulting in path `/api/chat/`. Adding `@router.get("/")` creates a route at `/api/chat/`. Both are correct; the FastAPI router dispatches by method.

**Note:** Frontend SWR should call `GET /api/chat/` (with trailing slash) to match how FastAPI registers the route, OR configure the router without trailing slash. The existing `POST /api/chat/` works in test_chat.py with the trailing slash — follow the same pattern for `GET /api/chat/`.

**Warning signs:** 404 on `GET /api/chat` without trailing slash. Fix: use `GET /api/chat/` in frontend.

---

## Code Examples

### MainChart Component Skeleton

```typescript
// Source: established pattern from SparklineChart.tsx (Phase 3) + typings.d.ts autoSize
import { useEffect, useRef } from 'react';
import { createChart, LineSeries } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, UTCTimestamp } from 'lightweight-charts';
import { useTicker } from '@/stores/priceStore';

interface Props { ticker: string; }

export default function MainChart({ ticker }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const tickCountRef = useRef<number>(0);
  const priceUpdate = useTicker(ticker);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: '#8b949e' },
      grid: { vertLines: { color: '#30363d' }, horzLines: { color: '#30363d' } },
    });
    const series = chart.addSeries(LineSeries, { color: '#209dd7', lineWidth: 2 });
    chartRef.current = chart;
    seriesRef.current = series as ISeriesApi<'Line'>;
    return () => { chart.remove(); chartRef.current = null; seriesRef.current = null; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    // Reset data when ticker changes (Pitfall 1)
    seriesRef.current?.setData([]);
    tickCountRef.current = 0;
  }, [ticker]);

  useEffect(() => {
    if (!seriesRef.current || !priceUpdate) return;
    tickCountRef.current += 1;
    seriesRef.current.update({ time: tickCountRef.current as UTCTimestamp, value: priceUpdate.price });
  }, [priceUpdate]);

  return <div ref={containerRef} style={{ width: '100%', height: '240px' }} />;
}
```

### PnLChart Poll Pattern

```typescript
// Source: portfolio.py GET /api/portfolio/history response shape + SWR refreshInterval
import useSWR from 'swr';
import { fetcher } from '@/lib/fetcher';

const { data } = useSWR<PortfolioHistoryResponse>('/api/portfolio/history', fetcher, {
  refreshInterval: 30_000,
});

// In useEffect watching data:
useEffect(() => {
  if (!data?.snapshots?.length || !seriesRef.current) return;
  const points = data.snapshots.map((s, i) => ({
    time: (i + 1) as UTCTimestamp,
    value: s.total_value,
  }));
  seriesRef.current.setData(points);
}, [data]);
```

### TradeBar Optimistic Mutate

```typescript
// Source: swr 2.4.1 MutatorOptions type + portfolio.py trade response shape
const { data: portfolio, mutate } = useSWR<PortfolioResponse>('/api/portfolio', fetcher);
const [error, setError] = useState<string | null>(null);
const [pending, setPending] = useState(false);

const handleTrade = async (side: 'buy' | 'sell') => {
  setError(null);
  setPending(true);
  try {
    await mutate(
      async (current) => {
        const res = await fetch('/api/portfolio/trade', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ticker, quantity: Number(qty), side }),
        });
        if (!res.ok) {
          const data = await res.json();
          throw new Error(data.error ?? 'Trade failed');
        }
        return current; // will be replaced by revalidate:true
      },
      {
        optimisticData: (current) => {
          if (!current) return current;
          const price = current.positions.find(p => p.ticker === ticker)?.current_price
            ?? usePriceStore.getState().prices[ticker]?.price ?? 0;
          const cost = Number(qty) * price;
          return { ...current, cash: current.cash + (side === 'sell' ? cost : -cost) };
        },
        rollbackOnError: true,
        revalidate: true,
      }
    );
  } catch (e) {
    setError(e instanceof Error ? e.message : 'Trade failed');
  } finally {
    setPending(false);
  }
};
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `chart.addLineSeries()` | `chart.addSeries(LineSeries, opts)` | Lightweight Charts v5 | SparklineChart.tsx already uses v5 API; new chart components must do the same |
| SWR `mutate(key, data)` bare | `mutate(asyncFn, { optimisticData, rollbackOnError })` | SWR v2 | Full optimistic flow in one call |
| Manual `ResizeObserver` | `autoSize: true` in `createChart` options | Lightweight Charts v5 | MainChart should use this; SparklineChart uses explicit `width`/`height` props (still valid for fixed-size sparklines) |

**Deprecated/outdated:**
- `addLineSeries()`, `addAreaSeries()` as top-level chart methods: replaced by `addSeries(LineSeries)`, `addSeries(AreaSeries)` in v5. [VERIFIED: lightweight-charts typings.d.ts line 1640]

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Node.js | npm run build | ✓ | (system) | — |
| lightweight-charts | MainChart, PnLChart, SparklineChart | ✓ (installed) | 5.2.0 | — |
| swr | All REST data fetching | ✓ (installed) | 2.4.1 | — |
| zustand | Price store | ✓ (installed) | 5.0.14 | — |
| Backend GET /api/chat | ChatPanel mount | ✗ (not yet implemented) | — | Show empty state on mount; no blocking |
| lucide-react | Chat panel toggle icon | ✗ (not installed) | — | Use CSS text character `‹ ›` |

**Missing dependencies with no fallback:**
- `GET /api/chat` backend endpoint — must be added as part of Phase 4 (backend task Wave 1)

**Missing dependencies with fallback:**
- `lucide-react` — use `‹` / `›` text characters for chat panel toggle

---

## Validation Architecture

`nyquist_validation` is enabled (confirmed in `.planning/config.json`).

### Test Framework

| Property | Value |
|----------|-------|
| Framework | Jest 30 + Testing Library React 16 |
| Config file | `frontend/jest.config.js` |
| Quick run command | `npm test -- --testPathPattern=<file> --watchAll=false` |
| Full suite command | `npm test -- --watchAll=false` |

Backend tests:

| Property | Value |
|----------|-------|
| Framework | pytest-asyncio |
| Config file | `backend/pyproject.toml` |
| Quick run command | `uv run --extra dev pytest tests/test_chat.py -v` |
| Full suite command | `uv run --extra dev pytest -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| FE-09 | MainChart renders canvas and calls `createChart` once | unit | `npm test -- --testPathPattern=MainChart --watchAll=false` | ❌ Wave 1 |
| FE-09 | MainChart resets series data on ticker change | unit | same | ❌ Wave 1 |
| FE-10 | PortfolioHeatmap renders tiles with correct width% per position | unit | `npm test -- --testPathPattern=PortfolioHeatmap --watchAll=false` | ❌ Wave 1 |
| FE-10 | Heatmap empty state renders when no positions | unit | same | ❌ Wave 1 |
| FE-11 | PnLChart calls `createChart` and `setData` when history data loads | unit | `npm test -- --testPathPattern=PnLChart --watchAll=false` | ❌ Wave 1 |
| FE-12 | PositionsTable renders all columns with mock data | unit | `npm test -- --testPathPattern=PositionsTable --watchAll=false` | ❌ Wave 1 |
| FE-12 | PositionsTable current-price cell flashes on price change | unit | same | ❌ Wave 1 |
| FE-13 | TradeBar calls POST /api/portfolio/trade on Buy click | unit | `npm test -- --testPathPattern=TradeBar --watchAll=false` | ❌ Wave 1 |
| FE-13 | TradeBar shows inline error on 400 response | unit | same | ❌ Wave 1 |
| FE-14 | ChatPanel loads history on mount via GET /api/chat | unit | `npm test -- --testPathPattern=ChatPanel --watchAll=false` | ❌ Wave 1 |
| FE-14 | ChatPanel shows loading indicator during POST /api/chat | unit | same | ❌ Wave 1 |
| FE-15 | ChatPanel renders trade action badge from response trades[] | unit | same | ❌ Wave 1 |
| FE-15 | ChatPanel renders watchlist badge from response watchlist_changes[] | unit | same | ❌ Wave 1 |
| CHAT-01 (backend) | GET /api/chat returns 200 with messages array | integration | `uv run --extra dev pytest tests/test_chat.py::TestChat::test_get_chat_history -v` | ❌ Wave 1 |

### Sampling Rate

- **Per task commit:** `npm test -- --testPathPattern=<component> --watchAll=false`
- **Per wave merge:** `npm test -- --watchAll=false` (full frontend suite)
- **Backend endpoint addition:** `uv run --extra dev pytest tests/test_chat.py -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `frontend/__tests__/MainChart.test.tsx` — covers FE-09
- [ ] `frontend/__tests__/PortfolioHeatmap.test.tsx` — covers FE-10
- [ ] `frontend/__tests__/PnLChart.test.tsx` — covers FE-11
- [ ] `frontend/__tests__/PositionsTable.test.tsx` — covers FE-12
- [ ] `frontend/__tests__/TradeBar.test.tsx` — covers FE-13
- [ ] `frontend/__tests__/ChatPanel.test.tsx` — covers FE-14, FE-15
- [ ] Backend: `GET /api/chat/` test case in `backend/tests/test_chat.py`

*(The lightweight-charts mock in `__mocks__/lightweightChartsStub.js` and `jest.config.js` mapper are already configured — new chart tests follow SparklineChart.test.tsx mock pattern exactly.)*

---

## Security Domain

`security_enforcement` not explicitly set to `false` in config.json — treat as enabled.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | No auth — single-user app by design |
| V3 Session Management | no | No sessions |
| V4 Access Control | no | No access control — single-user |
| V5 Input Validation | yes | Trade bar: validate ticker (non-empty, alphanumeric) and quantity (>0, isFinite) client-side before POST; server validates independently |
| V6 Cryptography | no | No cryptographic operations in Phase 4 |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| XSS via chat messages | Spoofing | React renders text as `{message}` — auto-escaped. Never use `dangerouslySetInnerHTML` in ChatPanel |
| Injection via ticker input | Tampering | Backend validates ticker against price cache; frontend should trim + uppercase ticker before submit |
| Large quantity causing NaN | Denial of Service | Validate `isFinite(qty) && qty > 0` before submit; backend validates independently |
| Chat badge displaying raw API data | Information Disclosure | Badge text is constructed from known fields (ticker, qty, price) — not raw LLM output |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | GET /api/chat returns `{"messages": [...]}` with `actions` as parsed JSON object | API Shapes | If backend returns `actions` as JSON string, frontend must `JSON.parse(msg.actions)` — one-line fix |
| A2 | GET /api/chat route path should use trailing slash `/api/chat/` to match FastAPI router convention | Pitfall 7 | If frontend uses `/api/chat` without slash and FastAPI does not redirect, SWR gets 307 redirect — fix by adding slash |
| A3 | `autoSize: true` in createChart works in Next.js static export (jsdom doesn't have ResizeObserver) | Pattern 1 | Unit tests will fail if ResizeObserver is absent in jsdom; fix: mock ResizeObserver in jest.setup.ts, OR use explicit width/height in MainChart (as SparklineChart does) |

---

## Open Questions

1. **Auto-select first ticker: watchlist order**
   - What we know: `GET /api/watchlist` returns tickers in `added_at ASC` order
   - What's unclear: On first load, all 10 seed tickers have identical `added_at` timestamps — sort order may be non-deterministic
   - Recommendation: Use `data?.tickers?.[0]?.ticker` as auto-select; the order is consistent enough for demo purposes

2. **PnLChart baseline color: green vs blue**
   - What we know: UI-SPEC says "P&L chart line (neutral baseline)" is `terminal-blue`; area is green above baseline, red below
   - What's unclear: Switching `topColor`/`bottomColor` dynamically on `AreaSeries` requires `series.applyOptions()` — possible but adds complexity
   - Recommendation: Start with static green area series; implement color flip as a stretch goal

3. **MainChart height: fixed vs flex-1**
   - What we know: D-02 says "Main chart (tall)" — specific pixel height not defined
   - What's unclear: Whether to use `h-60` (240px) fixed or `flex-1` with a min-height
   - Recommendation: Use `min-h-60` with `autoSize: true` so chart fills available space; the center column uses `flex flex-col` so the chart takes its natural height

---

## Sources

### Primary (HIGH confidence)

- `backend/app/routes/portfolio.py` — GET /api/portfolio response shape (cash, total_value, positions array), POST /api/portfolio/trade request/response, GET /api/portfolio/history snapshots
- `backend/app/routes/chat.py` — POST /api/chat response shape (message, trades, watchlist_changes); chat_messages schema
- `backend/app/db/schema.sql` — chat_messages table structure (role, content, actions, created_at)
- `frontend/src/components/SparklineChart.tsx` — Lightweight Charts v5 `createChart`/`addSeries(LineSeries)` lifecycle pattern
- `frontend/src/components/WatchlistRow.tsx` — Flash animation lifecycle pattern
- `frontend/node_modules/lightweight-charts/dist/typings.d.ts` — `IChartApi.remove()`, `addSeries()`, `AreaSeries`, `autoSize`, `setData()` API
- `frontend/node_modules/swr/dist/_internal/types.d.ts` — `MutatorOptions.optimisticData`, `rollbackOnError`, `revalidate` types
- `frontend/tailwind.config.js` — All color tokens, flash keyframes
- `.planning/phases/04-frontend-portfolio-trading/04-CONTEXT.md` — All locked decisions D-01 through D-16
- `.planning/phases/04-frontend-portfolio-trading/04-UI-SPEC.md` — Color specs, spacing, typography, component inventory
- `frontend/package.json` — Installed package versions

### Secondary (MEDIUM confidence)

- SWR official docs (swr.vercel.app/docs/mutation) — `optimisticData` pattern; cross-verified against installed package types

### Tertiary (LOW confidence)

- None

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all packages verified from installed `node_modules` and `package.json`
- Architecture: HIGH — all patterns traced to existing codebase (`SparklineChart.tsx`, `WatchlistRow.tsx`, `portfolio.py`, `chat.py`)
- API shapes: HIGH — verified directly from backend route handlers
- Pitfalls: HIGH — most derived from existing code comments and established Phase 3 patterns (e.g., Zustand v5 object selector issue already documented in `usePriceStream.ts`)
- GET /api/chat endpoint: MEDIUM — design is clear, shape is [ASSUMED] pending implementation

**Research date:** 2026-06-06
**Valid until:** 2026-07-06 (stable library versions; all packages locked in package.json)
