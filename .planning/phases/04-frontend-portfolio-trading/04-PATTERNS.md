# Phase 4: Frontend Portfolio & Trading - Pattern Map

**Mapped:** 2026-06-07
**Files analyzed:** 15 (9 new frontend components/tests + 2 type additions + 1 index.tsx modify + 1 backend route modify + 2 backend tests)
**Analogs found:** 15 / 15

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `frontend/src/components/MainChart.tsx` | component | event-driven (SSE via Zustand) | `frontend/src/components/SparklineChart.tsx` | exact |
| `frontend/src/components/PnLChart.tsx` | component | request-response (SWR poll) | `frontend/src/components/SparklineChart.tsx` | role-match |
| `frontend/src/components/PortfolioHeatmap.tsx` | component | request-response (SWR) | `frontend/src/components/WatchlistPanel.tsx` | role-match |
| `frontend/src/components/PositionsTable.tsx` | component | event-driven + request-response | `frontend/src/components/WatchlistRow.tsx` | exact |
| `frontend/src/components/TradeBar.tsx` | component | request-response (SWR mutate) | `frontend/src/components/Header.tsx` | role-match |
| `frontend/src/components/ChatPanel.tsx` | component | request-response (SWR + POST) | `frontend/src/components/WatchlistPanel.tsx` | role-match |
| `frontend/src/pages/index.tsx` | page | — | `frontend/src/pages/index.tsx` (self) | exact |
| `frontend/src/types/market.ts` | types | — | `frontend/src/types/market.ts` (self) | exact |
| `backend/app/routes/chat.py` | route | request-response | `backend/app/routes/portfolio.py` | exact |
| `frontend/__tests__/MainChart.test.tsx` | test | — | `frontend/__tests__/SparklineChart.test.tsx` | exact |
| `frontend/__tests__/PnLChart.test.tsx` | test | — | `frontend/__tests__/SparklineChart.test.tsx` | exact |
| `frontend/__tests__/PortfolioHeatmap.test.tsx` | test | — | `frontend/__tests__/WatchlistRow.test.tsx` | role-match |
| `frontend/__tests__/PositionsTable.test.tsx` | test | — | `frontend/__tests__/WatchlistRow.test.tsx` | exact |
| `frontend/__tests__/TradeBar.test.tsx` | test | — | `frontend/__tests__/WatchlistRow.test.tsx` | role-match |
| `frontend/__tests__/ChatPanel.test.tsx` | test | — | `frontend/__tests__/WatchlistRow.test.tsx` | role-match |

---

## Pattern Assignments

### `frontend/src/components/MainChart.tsx` (component, event-driven)

**Analog:** `frontend/src/components/SparklineChart.tsx`

**Imports pattern** (lines 1-4):
```typescript
import { useEffect, useRef } from 'react';
import { createChart, LineSeries } from 'lightweight-charts';
import type { ISeriesApi, IChartApi, UTCTimestamp } from 'lightweight-charts';
import { useTicker } from '@/stores/priceStore';
```

**Refs pattern** (lines 14-17):
```typescript
const containerRef = useRef<HTMLDivElement>(null);
const chartRef = useRef<IChartApi | null>(null);
const seriesRef = useRef<ISeriesApi<'Line'> | null>(null);
const tickCountRef = useRef<number>(0);
```

**Mount/cleanup pattern** (lines 21-56):
```typescript
useEffect(() => {
  if (!containerRef.current) return;
  const chart = createChart(containerRef.current, {
    width,
    height,
    layout: { background: { color: 'transparent' }, textColor: 'transparent' },
    rightPriceScale: { visible: false },
    timeScale: { visible: false },
    crosshair: { mode: 0 },
    grid: { vertLines: { visible: false }, horzLines: { visible: false } },
    handleScroll: false,
    handleScale: false,
  });
  const series = chart.addSeries(LineSeries, { color: '#209dd7', lineWidth: 1 });
  chartRef.current = chart;
  seriesRef.current = series as ISeriesApi<'Line'>;
  return () => {
    chart.remove();
    chartRef.current = null;
    seriesRef.current = null;
  };
}, []); // eslint-disable-line react-hooks/exhaustive-deps
```

**Price append pattern** (lines 64-71):
```typescript
useEffect(() => {
  if (!seriesRef.current || !priceUpdate) return;
  tickCountRef.current += 1;
  seriesRef.current.update({
    time: tickCountRef.current as UTCTimestamp,
    value: priceUpdate.price,
  });
}, [priceUpdate]);
```

**Differences from SparklineChart for MainChart:**
- Use `autoSize: true` instead of explicit `width`/`height` props — eliminates need for `applyOptions` effect
- Show grid, price scale, time scale (not hidden like sparklines)
- Add a second `useEffect([ticker])` that calls `seriesRef.current?.setData([])` and resets `tickCountRef.current = 0` on ticker change (Pitfall 1)
- Return `<div ref={containerRef} style={{ width: '100%', height: '240px' }} />` (full width, tall)

---

### `frontend/src/components/PnLChart.tsx` (component, request-response poll)

**Analog:** `frontend/src/components/SparklineChart.tsx` (chart lifecycle) + `frontend/src/components/Header.tsx` (SWR pattern)

**Imports pattern:**
```typescript
import { useEffect, useRef } from 'react';
import { createChart, AreaSeries } from 'lightweight-charts';
import type { ISeriesApi, IChartApi, UTCTimestamp } from 'lightweight-charts';
import useSWR from 'swr';
import { fetcher } from '@/lib/fetcher';
import type { PortfolioHistoryResponse } from '@/types/market';
```

**SWR poll pattern** (from Header.tsx lines 36-38, adapted):
```typescript
const { data } = useSWR<PortfolioHistoryResponse>('/api/portfolio/history', fetcher, {
  refreshInterval: 30_000,
});
```

**AreaSeries mount** (adapts SparklineChart lines 21-56, uses AreaSeries not LineSeries):
```typescript
useEffect(() => {
  if (!containerRef.current) return;
  const chart = createChart(containerRef.current, {
    autoSize: true,
    layout: { background: { color: 'transparent' }, textColor: '#8b949e' },
    grid: { vertLines: { color: '#30363d' }, horzLines: { color: '#30363d' } },
  });
  const series = chart.addSeries(AreaSeries, {
    lineColor: '#209dd7',
    topColor: 'rgba(34, 197, 94, 0.4)',
    bottomColor: 'rgba(34, 197, 94, 0.0)',
    lineWidth: 2,
  });
  chartRef.current = chart;
  seriesRef.current = series as ISeriesApi<'Area'>;
  return () => { chart.remove(); chartRef.current = null; seriesRef.current = null; };
}, []); // eslint-disable-line react-hooks/exhaustive-deps
```

**Data load pattern** (fires when SWR data arrives):
```typescript
useEffect(() => {
  if (!data?.snapshots?.length || !seriesRef.current) return;
  const points = data.snapshots.map((s, i) => ({
    time: (i + 1) as UTCTimestamp,
    value: s.total_value,
  }));
  seriesRef.current.setData(points);
}, [data]);
```

---

### `frontend/src/components/PortfolioHeatmap.tsx` (component, request-response)

**Analog:** `frontend/src/components/WatchlistPanel.tsx`

**Imports pattern** (WatchlistPanel.tsx lines 1-4):
```typescript
import useSWR from 'swr';
import type { PortfolioResponse } from '@/types/market';
import { fetcher } from '@/lib/fetcher';
```

**SWR fetch pattern** (WatchlistPanel.tsx line 12):
```typescript
const { data } = useSWR<PortfolioResponse>('/api/portfolio', fetcher);
```

**Empty state pattern** (WatchlistPanel.tsx lines 15-20):
```typescript
if (!positions || positions.length === 0) {
  return (
    <div className="p-4 text-terminal-muted text-xs">
      No positions yet. Use the trade bar to buy shares.
    </div>
  );
}
```

**Heatmap tile pattern** (CSS flexbox — no library, from CONTEXT.md D-05/D-06/D-07):
```tsx
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

**Color tokens in use** (from `frontend/tailwind.config.js` lines 13-26):
- `text-terminal-up` = `#22c55e`
- `text-terminal-down` = `#ef4444`
- `bg-terminal-surface` = `#1a1a2e`
- `text-terminal-text` = `#e6edf3`
- `text-terminal-muted` = `#8b949e`

---

### `frontend/src/components/PositionsTable.tsx` (component, event-driven + request-response)

**Analog:** `frontend/src/components/WatchlistRow.tsx` (flash + useTicker) + `frontend/src/components/WatchlistPanel.tsx` (table structure)

**Flash animation imports** (WatchlistRow.tsx lines 1-2):
```typescript
import { useEffect, useRef } from 'react';
import { useTicker } from '@/stores/priceStore';
```

**Per-row flash pattern** (WatchlistRow.tsx lines 16-36):
```typescript
const priceRef = useRef<HTMLTableCellElement>(null);
const flashTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

useEffect(() => {
  if (!priceUpdate || !priceRef.current) return;
  const cell = priceRef.current;
  if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current);
  cell.classList.remove('animate-flash-up', 'animate-flash-down');
  if (priceUpdate.direction === 'flat') return;
  void cell.offsetWidth; // force reflow so re-adding the class re-triggers the animation
  const cls = priceUpdate.direction === 'up' ? 'animate-flash-up' : 'animate-flash-down';
  cell.classList.add(cls);
  flashTimeoutRef.current = setTimeout(() => {
    cell.classList.remove(cls);
  }, 500);
  return () => {
    if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current);
  };
}, [priceUpdate?.direction, priceUpdate?.timestamp]);
```

**Table structure pattern** (WatchlistPanel.tsx lines 27-47):
```tsx
<table className="w-full text-xs border-collapse">
  <thead>
    <tr className="text-terminal-muted border-b border-terminal-border">
      <th className="text-left py-1 pl-1 font-semibold">Symbol</th>
      {/* ...headers */}
    </tr>
  </thead>
  <tbody>
    {positions.map((pos) => (
      <PositionsRow key={pos.ticker} pos={pos} />
    ))}
  </tbody>
</table>
```

**Live price fallback pattern** (from RESEARCH.md Pattern 6):
```typescript
// Use live price from Zustand store; fall back to SWR portfolio data
const currentPrice = priceUpdate?.price ?? pos.current_price;
const liveUnrealizedPnl = (currentPrice - pos.avg_cost) * pos.quantity;
const livePnlPct = pos.avg_cost > 0 ? ((currentPrice - pos.avg_cost) / pos.avg_cost) * 100 : 0;
```

---

### `frontend/src/components/TradeBar.tsx` (component, request-response with optimistic mutate)

**Analog:** `frontend/src/components/Header.tsx` (SWR pattern) + SWR mutate (from installed types)

**SWR import + fetcher pattern** (Header.tsx lines 19-22):
```typescript
import useSWR from 'swr';
import type { PortfolioResponse } from '@/types/market';
import { fetcher } from '@/lib/fetcher';
```

**SWR mutate binding pattern** (adapts Header.tsx line 36):
```typescript
const { data: portfolio, mutate } = useSWR<PortfolioResponse>('/api/portfolio', fetcher);
```

**Optimistic trade submit pattern** (from RESEARCH.md Pattern 4, SWR v2 `optimisticData`):
```typescript
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
        return current; // replaced by revalidate: true
      },
      {
        optimisticData: (current) => {
          if (!current) return current;
          const price = current.positions.find(p => p.ticker === ticker)?.current_price ?? 0;
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

**SWR key consistency note:** Use exact string `/api/portfolio` — matches Header.tsx line 36. No trailing slash variation.

**Auto-fill from selectedTicker** (CONTEXT.md D-12):
```typescript
useEffect(() => {
  if (selectedTicker) setTicker(selectedTicker);
}, [selectedTicker]);
```

**Input validation** (RESEARCH.md Security V5):
```typescript
// Before submit: validate ticker non-empty alphanumeric, qty > 0 and finite
const trimmedTicker = ticker.trim().toUpperCase();
if (!trimmedTicker || !/^[A-Z]+$/.test(trimmedTicker)) { setError('Invalid ticker'); return; }
if (!isFinite(Number(qty)) || Number(qty) <= 0) { setError('Quantity must be > 0'); return; }
```

---

### `frontend/src/components/ChatPanel.tsx` (component, request-response)

**Analog:** `frontend/src/components/WatchlistPanel.tsx` (panel structure + SWR)

**Imports pattern** (WatchlistPanel.tsx lines 1-4):
```typescript
import useSWR from 'swr';
import type { ChatHistoryResponse } from '@/types/market';
import { fetcher } from '@/lib/fetcher';
```

**History fetch on mount** (follows WatchlistPanel.tsx line 12):
```typescript
const { data: history, mutate: mutateHistory } = useSWR<ChatHistoryResponse>('/api/chat/', fetcher);
// Note: trailing slash required — matches FastAPI router convention (RESEARCH Pitfall 7)
```

**Collapse pattern** (CONTEXT.md D-09, RESEARCH.md Pattern 7):
```tsx
// In index.tsx — controls the column width:
<div className={`shrink-0 overflow-hidden transition-all duration-300 border-l border-terminal-border ${
  chatOpen ? 'w-80' : 'w-8'
}`}>
  <button onClick={() => setChatOpen(!chatOpen)} className="...">
    {chatOpen ? '›' : '‹'}
  </button>
  {chatOpen && <ChatPanel onNewTrade={() => mutatePortfolio()} />}
</div>
```

**Auto-scroll pattern** (RESEARCH.md Pitfall 6):
```typescript
const messagesEndRef = useRef<HTMLDivElement>(null);
useEffect(() => {
  messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
}, [messages.length]);
// At bottom of message list: <div ref={messagesEndRef} />
```

**Chat submit + revalidate pattern:**
```typescript
const handleSubmit = async () => {
  setLoading(true);
  try {
    const res = await fetch('/api/chat/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: input }),
    });
    const data = await res.json();
    await mutateHistory(); // refresh history from server
    if (data.trades?.length || data.watchlist_changes?.length) {
      onNewTrade?.(); // parent revalidates /api/portfolio
    }
  } finally {
    setLoading(false);
    setInput('');
  }
};
```

**Action badge pattern** (CONTEXT.md D-10):
```tsx
// Below assistant message bubble — pill/badge per trade outcome
{msg.actions?.trades?.map((t, i) => (
  <span key={i} className="inline-block px-2 py-0.5 rounded text-xs border border-terminal-accent text-terminal-accent mr-1">
    {t.side.toUpperCase()} {t.quantity} {t.ticker} @ ${t.price?.toFixed(2)}
  </span>
))}
{msg.actions?.watchlist_changes?.map((w, i) => (
  <span key={i} className="inline-block px-2 py-0.5 rounded text-xs border border-terminal-muted text-terminal-muted mr-1">
    {w.action.toUpperCase()} {w.ticker}
  </span>
))}
```

**XSS safety** (RESEARCH.md Security): Render `{msg.content}` as plain text — never `dangerouslySetInnerHTML`.

---

### `frontend/src/pages/index.tsx` (MODIFY — add 3-column layout)

**Analog:** `frontend/src/pages/index.tsx` (self — current state at lines 1-24)

**Current state** (lines 1-24 — full file):
```typescript
import { useState } from 'react';
import { usePriceStream } from '@/hooks/usePriceStream';
import Header from '@/components/Header';
import WatchlistPanel from '@/components/WatchlistPanel';

export default function Dashboard() {
  usePriceStream();
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  return (
    <div className="min-h-screen bg-terminal-bg text-terminal-text font-mono">
      <Header />
      <div className="flex gap-4 p-4">
        <WatchlistPanel selectedTicker={selectedTicker} onSelectTicker={setSelectedTicker} />
        {/* Phase 4: main chart area, portfolio panels, and AI chat go here */}
      </div>
    </div>
  );
}
```

**Additions required:**
1. Import all 6 new components + `useSWR` + `fetcher` + `WatchlistResponse` type
2. Add `const [chatOpen, setChatOpen] = useState(true)` state
3. Add SWR for watchlist + `useEffect` auto-select first ticker on mount (CONTEXT.md D-03)
4. Replace placeholder comment with 3-column layout per RESEARCH.md `index.tsx Wiring` section
5. The outer `div` gets height constraint: `h-[calc(100vh-52px)]` and `overflow-hidden`

**Auto-select pattern** (CONTEXT.md D-03, RESEARCH.md index.tsx Wiring):
```typescript
const { data: watchlistData } = useSWR<WatchlistResponse>('/api/watchlist', fetcher);
useEffect(() => {
  if (!selectedTicker && watchlistData?.tickers?.length) {
    setSelectedTicker(watchlistData.tickers[0].ticker);
  }
}, [watchlistData, selectedTicker]);
```

---

### `frontend/src/types/market.ts` (MODIFY — add new interfaces)

**Analog:** `frontend/src/types/market.ts` (self — current state lines 1-51)

**Existing interfaces to preserve** (lines 1-51 — all current exports intact):
- `PriceUpdate`, `PriceMap`, `WatchlistEntry`, `WatchlistResponse`, `Position`, `PortfolioResponse`, `DEFAULT_TICKERS`

**New interfaces to add** (from RESEARCH.md Pattern 5):
```typescript
// GET /api/portfolio/history response:
export interface PortfolioSnapshot {
  total_value: number;
  recorded_at: string;  // ISO timestamp
}

export interface PortfolioHistoryResponse {
  snapshots: PortfolioSnapshot[];
}

// GET /api/chat/ and POST /api/chat/ types:
export interface TradeOutcome {
  status: 'executed' | 'failed';
  ticker: string;
  side?: string;
  quantity?: number;
  price?: number;
  trade_id?: string;
  error?: string;
}

export interface WatchlistOutcome {
  status: string;
  ticker: string;
  action: string;
}

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

export interface ChatPostResponse {
  message: string;
  trades: TradeOutcome[];
  watchlist_changes: WatchlistOutcome[];
}
```

---

### `backend/app/routes/chat.py` (MODIFY — add GET /api/chat/ route)

**Analog:** `backend/app/routes/portfolio.py` `@router.get("/")` handler (lines 198-242)

**Pattern: add GET handler inside existing `create_chat_router` factory** (chat.py lines 135-145):
```python
def create_chat_router(price_cache: PriceCache, db_path: str) -> APIRouter:
    router = APIRouter(prefix="/api/chat", tags=["chat"])

    @router.get("/")  # ADD THIS HANDLER — path becomes /api/chat/
    async def get_chat_history(request: Request) -> dict:
        """Return last 20 chat messages in ascending chronological order."""
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

    @router.post("/")  # existing handler — unchanged
    async def chat(body: ChatRequest, request: Request) -> dict:
        ...
```

**DB connection pattern** (portfolio.py lines 201-242 — try/finally):
```python
conn = get_conn(db_path)
try:
    # ... query ...
    return { ... }
finally:
    conn.close()
```

**`json` import:** Already present at chat.py line 16 — no new imports needed.

---

## Test Patterns

### `frontend/__tests__/MainChart.test.tsx` and `frontend/__tests__/PnLChart.test.tsx`

**Analog:** `frontend/__tests__/SparklineChart.test.tsx` (exact copy pattern)

**Jest mock pattern** (SparklineChart.test.tsx lines 12-24):
```typescript
jest.mock('lightweight-charts', () => {
  const mockSeriesUpdate = jest.fn();
  const mockSetData = jest.fn();
  const mockChartRemove = jest.fn();
  const mockApplyOptions = jest.fn();
  const mockAddSeries = jest.fn().mockReturnValue({
    update: mockSeriesUpdate,
    setData: mockSetData,   // PnLChart additionally needs setData
  });
  const mockCreateChart = jest.fn().mockReturnValue({
    addSeries: mockAddSeries,
    remove: mockChartRemove,
    applyOptions: mockApplyOptions,
  });
  const LineSeries = { __sentinelType: 'LineSeries' };
  const AreaSeries = { __sentinelType: 'AreaSeries' };  // PnLChart only
  return { createChart: mockCreateChart, LineSeries, AreaSeries };
});
```

**Store reset pattern** (SparklineChart.test.tsx lines 33-35):
```typescript
beforeEach(() => {
  usePriceStore.setState({ prices: {}, connectionStatus: 'disconnected' });
  jest.clearAllMocks();
});
```

**Price dispatch pattern** (SparklineChart.test.tsx lines 45-70):
```typescript
act(() => {
  usePriceStore.setState({
    prices: {
      AAPL: {
        ticker: 'AAPL', price: 190.5, previous_price: 189.5,
        timestamp: 1717700000.75, change: 1, change_percent: 0.53, direction: 'up',
      },
    },
  });
});
expect(series.update).toHaveBeenCalledWith(expect.objectContaining({ time: 1, value: 190.5 }));
```

**MainChart additional test — ticker change resets series:**
```typescript
it('resets series data on ticker change', () => {
  const mockSetData = jest.fn();
  // ... mock setup ...
  const { rerender } = render(<MainChart ticker="AAPL" />);
  rerender(<MainChart ticker="MSFT" />);
  expect(mockSetData).toHaveBeenCalledWith([]);
});
```

**PnLChart additional test — setData called when SWR data arrives:**
```typescript
// Mock useSWR to return snapshot data, verify series.setData called
jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));
// Set useSWR mock to return snapshots, assert mockSetData called with correct points
```

---

### `frontend/__tests__/PositionsTable.test.tsx`

**Analog:** `frontend/__tests__/WatchlistRow.test.tsx` (exact flash + useTicker pattern)

**Flash test structure** (WatchlistRow.test.tsx lines 53-143 — copy setup):
```typescript
beforeEach(() => {
  usePriceStore.setState({ prices: {}, connectionStatus: 'disconnected' });
  jest.useFakeTimers();
});
afterEach(() => { jest.useRealTimers(); });
```

**Mock portfolio data shape** (from `frontend/src/types/market.ts` Position interface, lines 31-39):
```typescript
const mockPosition: Position = {
  ticker: 'AAPL', quantity: 10, avg_cost: 185.50,
  current_price: 188.25, unrealized_pnl: 27.50, pnl_pct: 1.48,
};
```

**Mock SWR** for PositionsTable (component uses SWR `/api/portfolio`):
```typescript
jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn().mockReturnValue({
    data: { cash: 10000, total_value: 11855, positions: [mockPosition] },
    mutate: jest.fn(),
  }),
}));
```

---

### `frontend/__tests__/TradeBar.test.tsx`

**Analog:** `frontend/__tests__/WatchlistRow.test.tsx` (event + state pattern)

**global.fetch mock pattern** (standard Jest pattern — no codebase example yet, but established approach):
```typescript
beforeEach(() => {
  global.fetch = jest.fn();
});
```

**Trade POST test:**
```typescript
it('calls POST /api/portfolio/trade on Buy click', async () => {
  (global.fetch as jest.Mock).mockResolvedValueOnce({
    ok: true,
    json: async () => ({ status: 'ok', ticker: 'AAPL', side: 'buy', quantity: 1, price: 190, trade_id: 'x' }),
  });
  // render TradeBar, fill inputs, click Buy
  // assert fetch called with correct body
});

it('shows inline error on 400 response', async () => {
  (global.fetch as jest.Mock).mockResolvedValueOnce({
    ok: false,
    json: async () => ({ error: 'Insufficient cash' }),
  });
  // render TradeBar, submit, assert error text visible
});
```

**SWR mock** (TradeBar uses `useSWR` + `mutate`):
```typescript
const mockMutate = jest.fn().mockImplementation(async (fn, opts) => {
  try { await fn(mockPortfolio); }
  catch (e) { if (!opts?.rollbackOnError) throw e; }
});
jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn().mockReturnValue({ data: mockPortfolio, mutate: mockMutate }),
}));
```

---

### `frontend/__tests__/ChatPanel.test.tsx`

**Analog:** `frontend/__tests__/WatchlistRow.test.tsx` (render + event pattern)

**SWR mock for history load:**
```typescript
jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn().mockReturnValue({
    data: {
      messages: [
        { role: 'user', content: 'Hello', actions: null, created_at: '2026-06-07T00:00:00Z' },
        { role: 'assistant', content: 'Hi there!', actions: null, created_at: '2026-06-07T00:00:01Z' },
      ],
    },
    mutate: jest.fn(),
  }),
}));
```

**Badge render test:**
```typescript
it('renders trade action badge from response trades[]', () => {
  // Set up SWR mock with assistant message that has actions.trades
  const msgWithTrades: ChatMessage = {
    role: 'assistant',
    content: 'I bought AAPL for you.',
    actions: { trades: [{ status: 'executed', ticker: 'AAPL', side: 'buy', quantity: 5, price: 190 }], watchlist_changes: [] },
    created_at: '2026-06-07T00:00:01Z',
  };
  // assert badge text contains "BUY 5 AAPL"
});
```

---

## Shared Patterns

### 1. Lightweight Charts Instance Lifecycle
**Source:** `frontend/src/components/SparklineChart.tsx` lines 21-56
**Apply to:** `MainChart.tsx`, `PnLChart.tsx`

Key rules:
- Always `createChart` inside `useEffect([], [])` — never at module scope (SSR guard)
- Always `chart.remove()` in cleanup; null refs after removal
- `addSeries(LineSeries, opts)` not `addLineSeries()` — v5 API (line 43)
- Use `autoSize: true` for full-width charts; explicit `width`/`height` only for fixed-size sparklines

### 2. Zustand Per-Ticker Selector
**Source:** `frontend/src/stores/priceStore.ts` lines 21-22
**Apply to:** `MainChart.tsx`, `PositionsTable.tsx` (per-row)

```typescript
export const useTicker = (ticker: string) =>
  usePriceStore((state) => state.prices[ticker]);
```

Never use `usePriceStore((s) => ({ price: s.prices[ticker] }))` — creates new object on every render, triggers infinite re-renders in Zustand v5.

### 3. SWR Data Fetching
**Source:** `frontend/src/components/Header.tsx` lines 36-38 + `frontend/src/lib/fetcher.ts` lines 1-5
**Apply to:** `PortfolioHeatmap.tsx`, `PnLChart.tsx`, `PositionsTable.tsx`, `TradeBar.tsx`, `ChatPanel.tsx`, `index.tsx`

```typescript
import useSWR from 'swr';
import { fetcher } from '@/lib/fetcher';
const { data } = useSWR<ResponseType>('/api/path', fetcher, { refreshInterval: N });
```

SWR key must be exact string — no trailing slash variation except `/api/chat/` which requires the slash (FastAPI router convention, RESEARCH Pitfall 7).

### 4. Price Flash Animation
**Source:** `frontend/src/components/WatchlistRow.tsx` lines 16-36
**Apply to:** `PositionsTable.tsx` current-price cell

CSS classes `animate-flash-up` / `animate-flash-down` defined in `frontend/tailwind.config.js` lines 27-40. The `void cell.offsetWidth` force-reflow trick (line 25) is required for re-triggering the animation on rapid successive updates.

### 5. Bloomberg-Style Compact Table
**Source:** `frontend/src/components/WatchlistPanel.tsx` lines 26-47
**Apply to:** `PositionsTable.tsx`

```tsx
<table className="w-full text-xs border-collapse">
  <thead>
    <tr className="text-terminal-muted border-b border-terminal-border">
      <th className="text-left py-1 pl-1 font-semibold">Symbol</th>
    </tr>
  </thead>
```

### 6. Backend Router Factory + DB Connection
**Source:** `backend/app/routes/portfolio.py` lines 186-242 + `backend/app/routes/chat.py` lines 135-266
**Apply to:** `backend/app/routes/chat.py` (new GET handler)

```python
def create_chat_router(price_cache: PriceCache, db_path: str) -> APIRouter:
    router = APIRouter(prefix="/api/chat", tags=["chat"])
    # Add routes here as nested functions
    return router
```

Always use `try/finally: conn.close()` for DB connections. Never leave connection open.

### 7. Test Infrastructure
**Source:** `frontend/__tests__/SparklineChart.test.tsx` + `frontend/__tests__/WatchlistRow.test.tsx`
**Apply to:** All 6 new test files

- `jest.config.js` maps `lightweight-charts` → `__mocks__/lightweightChartsStub.js` (line 16) — override with `jest.mock()` per test
- `jest.setup.ts` imports `jest-canvas-mock` — canvas API available in jsdom
- Always `jest.clearAllMocks()` in `beforeEach`
- Always `usePriceStore.setState({ prices: {}, connectionStatus: 'disconnected' })` in `beforeEach` to reset Zustand

### 8. Backend Test Fixture Pattern
**Source:** `backend/tests/conftest.py` lines 45-80 (`chat_client` fixture)
**Apply to:** New `test_get_chat_history` test in `backend/tests/test_chat.py`

The `chat_client` fixture already registers all routers including chat. New GET test uses same fixture:
```python
async def test_get_chat_history(self, chat_client):
    response = await chat_client.get("/api/chat/")
    assert response.status_code == 200
    data = response.json()
    assert "messages" in data
    assert isinstance(data["messages"], list)
```

---

## No Analog Found

All Phase 4 files have close analogs in the codebase. No entries.

---

## Metadata

**Analog search scope:** `frontend/src/components/`, `frontend/src/pages/`, `frontend/src/stores/`, `frontend/src/lib/`, `frontend/src/types/`, `frontend/__tests__/`, `backend/app/routes/`, `backend/tests/`
**Files scanned:** 14 source files read directly
**Pattern extraction date:** 2026-06-07
