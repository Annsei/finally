# Phase 3: Frontend Foundation - Pattern Map

**Mapped:** 2026-06-06
**Files analyzed:** 12 new files (frontend does not exist yet)
**Analogs found:** 8 / 12 (backend analogs for interface/type reference; 4 have no frontend analog — all patterns drawn from RESEARCH.md verified sources)

---

## File Classification

| New File | Role | Data Flow | Closest Analog | Match Quality |
|----------|------|-----------|----------------|---------------|
| `frontend/src/types/market.ts` | type definition | — | `backend/app/market/models.py` | interface-match (defines identical shape) |
| `frontend/src/stores/priceStore.ts` | store | event-driven | `backend/app/market/cache.py` | pattern-match (same read-by-key concept) |
| `frontend/src/hooks/usePriceStream.ts` | hook | event-driven | `backend/app/market/stream.py` | pattern-match (producer/consumer inversion) |
| `frontend/src/pages/_app.tsx` | config/layout | request-response | `backend/app/main.py` | structure-match (app entry, global setup) |
| `frontend/src/pages/index.tsx` | component/page | event-driven + request-response | `backend/app/main.py` | structure-match (top-level wiring) |
| `frontend/src/components/Header.tsx` | component | request-response | `backend/app/routes/health.py` | weak-match (status indicator pattern) |
| `frontend/src/components/WatchlistPanel.tsx` | component | request-response | `backend/app/routes/watchlist.py` | interface-match (consumes GET /api/watchlist shape) |
| `frontend/src/components/WatchlistRow.tsx` | component | event-driven | no analog | none — pure frontend pattern |
| `frontend/src/components/SparklineChart.tsx` | component | event-driven | no analog | none — canvas charting, no backend analog |
| `frontend/next.config.js` | config | — | `backend/pyproject.toml` | weak-match (project config conventions) |
| `frontend/tailwind.config.js` | config | — | no analog | none — frontend-only |
| `frontend/src/styles/globals.css` | styles | — | no analog | none — frontend-only |

---

## Pattern Assignments

### `frontend/src/types/market.ts` (type definition)

**Analog:** `backend/app/market/models.py` (lines 10-49)

This TypeScript interface MUST mirror the Python `PriceUpdate.to_dict()` serialization exactly. The backend sends snake_case field names — do NOT camelCase them. All seven fields are required.

**Exact field names from `backend/app/market/models.py` lines 40-49:**
```python
# to_dict() output — these become JSON keys over SSE and REST:
{
    "ticker": self.ticker,           # str
    "price": self.price,             # float
    "previous_price": self.previous_price,  # float
    "timestamp": self.timestamp,     # float (Unix seconds)
    "change": self.change,           # float
    "change_percent": self.change_percent,  # float
    "direction": self.direction,     # "up" | "down" | "flat"
}
```

**TypeScript type to create** (`frontend/src/types/market.ts`):
```typescript
// All field names are snake_case — match backend PriceUpdate.to_dict() exactly
export interface PriceUpdate {
  ticker: string;
  price: number;
  previous_price: number;
  timestamp: number;       // Unix seconds (float)
  change: number;
  change_percent: number;
  direction: 'up' | 'down' | 'flat';
}

// SSE event.data is a JSON object keyed by ticker symbol:
// { "AAPL": PriceUpdate, "GOOGL": PriceUpdate, ... }
export type PriceMap = Record<string, PriceUpdate>;
```

**Default tickers from `backend/app/market/seed_prices.py` lines 4-15** (use for mock data in tests):
```typescript
// For test fixtures — matches SEED_PRICES in backend
export const DEFAULT_TICKERS = [
  'AAPL', 'GOOGL', 'MSFT', 'AMZN', 'TSLA',
  'NVDA', 'META', 'JPM', 'V', 'NFLX',
] as const;
```

**Watchlist REST response shape from `backend/app/routes/watchlist.py` lines 109-116:**
```typescript
// GET /api/watchlist response:
export interface WatchlistEntry {
  ticker: string;
  added_at: string;           // ISO timestamp string
  price: number | null;       // null if not in price cache yet
  change_percent: number | null;
  direction: 'up' | 'down' | 'flat' | null;
}
export interface WatchlistResponse {
  tickers: WatchlistEntry[];
}
```

**Portfolio REST response shape from `backend/app/routes/portfolio.py` lines 236-239:**
```typescript
// GET /api/portfolio response:
export interface Position {
  ticker: string;
  quantity: number;
  avg_cost: number;
  current_price: number;
  unrealized_pnl: number;
  pnl_pct: number;
}
export interface PortfolioResponse {
  cash: number;
  total_value: number;
  positions: Position[];
}
```

---

### `frontend/src/stores/priceStore.ts` (store, event-driven)

**Analog:** `backend/app/market/cache.py` (conceptual: keyed-by-ticker in-memory store)

**Core pattern** — from RESEARCH.md Pattern 1 (verified against Zustand v5 README):
```typescript
// frontend/src/stores/priceStore.ts
import { create } from 'zustand';
import type { PriceUpdate, PriceMap } from '@/types/market';

interface PriceStore {
  prices: PriceMap;
  connectionStatus: 'connected' | 'reconnecting' | 'disconnected';
  setPrices: (data: PriceMap) => void;
  setConnectionStatus: (status: PriceStore['connectionStatus']) => void;
}

export const usePriceStore = create<PriceStore>()((set) => ({
  prices: {},
  connectionStatus: 'disconnected',
  setPrices: (data) => set({ prices: data }),
  setConnectionStatus: (status) => set({ connectionStatus: status }),
}));

// Per-ticker selector — only re-renders when THIS ticker's data changes
// Use this in WatchlistRow, SparklineChart — NOT an object selector
export const useTicker = (ticker: string) =>
  usePriceStore((state) => state.prices[ticker]);
```

**Critical Zustand v5 rule** — do NOT write object selectors without useShallow:
```typescript
// WRONG — causes "Maximum update depth exceeded" in Zustand v5:
const { prices, connectionStatus } = usePriceStore(s => ({
  prices: s.prices,
  connectionStatus: s.connectionStatus,
}));

// CORRECT — separate selectors per atom:
const prices = usePriceStore((s) => s.prices);
const connectionStatus = usePriceStore((s) => s.connectionStatus);

// ALSO CORRECT — useShallow for multi-value in one call:
import { useShallow } from 'zustand/react/shallow';
const { prices, connectionStatus } = usePriceStore(
  useShallow((s) => ({ prices: s.prices, connectionStatus: s.connectionStatus }))
);
```

---

### `frontend/src/hooks/usePriceStream.ts` (hook, event-driven)

**Analog:** `backend/app/market/stream.py` (inverted: backend is producer, this hook is the browser-side consumer)

**SSE event format** — from `backend/app/market/stream.py` line 80:
```python
# Backend sends: data: {"AAPL": {ticker, price, previous_price, ...}, "GOOGL": {...}, ...}
# One JSON object with all tickers as keys — NOT one event per ticker
data = {ticker: update.to_dict() for ticker, update in prices.items()}
payload = json.dumps(data)
yield f"data: {payload}\n\n"
```

**Consumer hook pattern** — from RESEARCH.md Pattern 2 (verified against MDN EventSource + Next.js hydration docs):
```typescript
// frontend/src/hooks/usePriceStream.ts
import { useEffect } from 'react';
import { usePriceStore } from '@/stores/priceStore';

export function usePriceStream() {
  // Separate selectors — avoid object selector anti-pattern
  const setPrices = usePriceStore((s) => s.setPrices);
  const setConnectionStatus = usePriceStore((s) => s.setConnectionStatus);

  useEffect(() => {
    // MUST be inside useEffect — EventSource is undefined in Node.js (SSR build)
    const es = new EventSource('/api/stream/prices');

    es.onopen = () => setConnectionStatus('connected');

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        // data is PriceMap: { AAPL: PriceUpdate, GOOGL: PriceUpdate, ... }
        setPrices(data);
      } catch {
        // Silently ignore malformed events — same defensive pattern as backend
      }
    };

    es.onerror = () => {
      if (es.readyState === EventSource.CONNECTING) {
        setConnectionStatus('reconnecting');
      } else {
        setConnectionStatus('disconnected');
      }
    };

    return () => {
      es.close();                       // Always close on unmount — prevent memory leak
      setConnectionStatus('disconnected');
    };
  }, []); // Empty deps — one connection for the lifetime of the page
}
```

**Connection states to connection dot color mapping** (from CONTEXT.md specifics):
- `'connected'` → green dot
- `'reconnecting'` → yellow dot (EventSource CONNECTING state during auto-reconnect)
- `'disconnected'` → red dot (EventSource ERROR state after reconnect fails)

---

### `frontend/src/pages/_app.tsx` (config/layout)

**Analog:** `backend/app/main.py` (entry point, global wiring)

**Pattern** — from RESEARCH.md Pattern 5 (verified against nextjs.org font docs):
```typescript
// frontend/src/pages/_app.tsx
import { JetBrains_Mono } from 'next/font/google';
import type { AppProps } from 'next/app';
import '@/styles/globals.css';

const jetbrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  weight: ['400', '600'],    // Only the two weights used
  variable: '--font-mono',   // CSS variable for Tailwind integration
  display: 'swap',
});

export default function App({ Component, pageProps }: AppProps) {
  return (
    <main className={`${jetbrainsMono.variable} font-mono`}>
      <Component {...pageProps} />
    </main>
  );
}
```

**Required mock for testing** (`frontend/__mocks__/nextFontMock.js`):
```javascript
// Mocks next/font/google — required for Jest (next/font doesn't work in jsdom)
module.exports = {
  JetBrains_Mono: () => ({
    variable: '--font-mono',
    className: 'mock-font',
  }),
};
```

---

### `frontend/src/pages/index.tsx` (component/page, event-driven + request-response)

**Analog:** `backend/app/main.py` (top-level wiring of all subsystems)

**Responsibilities:**
1. Mount SSE stream via `usePriceStream()` hook (single call, never in child components)
2. Render layout: Header + WatchlistPanel in the dark terminal grid
3. Manage `selectedTicker` local state (passed down to WatchlistPanel and main chart area placeholder)

**Core pattern:**
```typescript
// frontend/src/pages/index.tsx
import { useState } from 'react';
import { usePriceStream } from '@/hooks/usePriceStream';
import Header from '@/components/Header';
import WatchlistPanel from '@/components/WatchlistPanel';

export default function Dashboard() {
  // SSE hook called ONCE at page root — creates one EventSource for the app's lifetime
  usePriceStream();

  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);

  return (
    <div className="min-h-screen bg-terminal-bg text-terminal-text font-mono">
      <Header />
      <div className="flex gap-4 p-4">
        <WatchlistPanel
          selectedTicker={selectedTicker}
          onSelectTicker={setSelectedTicker}
        />
        {/* Phase 4: main chart area, portfolio panels go here */}
      </div>
    </div>
  );
}
```

---

### `frontend/src/components/Header.tsx` (component, request-response)

**Analog:** `backend/app/routes/health.py` (status endpoint — parallel: provides system status to consumers)

**Data sources:**
- `connectionStatus` from Zustand: `usePriceStore((s) => s.connectionStatus)` — single selector, no useShallow needed
- `cash` and `total_value` from SWR `GET /api/portfolio`

**Response shape** — from `backend/app/routes/portfolio.py` lines 236-239:
```python
# GET /api/portfolio returns:
return {
    "cash": cash_balance,         # float
    "total_value": total_value,   # float (cash + position market value)
    "positions": positions,       # array (Header only needs cash + total_value)
}
```

**Pattern:**
```typescript
// frontend/src/components/Header.tsx
import useSWR from 'swr';
import { usePriceStore } from '@/stores/priceStore';
import type { PortfolioResponse } from '@/types/market';

const fetcher = (url: string) => fetch(url).then((r) => r.json());

// Dot color map — from CONTEXT.md specifics section
const DOT_COLORS = {
  connected: 'bg-green-500',
  reconnecting: 'bg-amber-400',
  disconnected: 'bg-red-500',
} as const;

export default function Header() {
  // Single atom selector — no useShallow needed for one value
  const connectionStatus = usePriceStore((s) => s.connectionStatus);

  const { data } = useSWR<PortfolioResponse>('/api/portfolio', fetcher, {
    refreshInterval: 5000,  // Poll every 5s for live cash/value updates
  });

  return (
    <header className="flex items-center justify-between px-4 py-2 border-b border-terminal-border bg-terminal-surface">
      <span className="text-terminal-accent font-semibold text-lg">FinAlly</span>
      <div className="flex items-center gap-6 text-sm">
        <span className="text-terminal-muted">
          Cash: <span className="text-terminal-text">
            ${data?.cash?.toLocaleString('en-US', { minimumFractionDigits: 2 }) ?? '—'}
          </span>
        </span>
        <span className="text-terminal-muted">
          Portfolio: <span className="text-terminal-text">
            ${data?.total_value?.toLocaleString('en-US', { minimumFractionDigits: 2 }) ?? '—'}
          </span>
        </span>
        {/* Connection status dot — size: 8px, no label, right-aligned */}
        <div
          className={`w-2 h-2 rounded-full ${DOT_COLORS[connectionStatus]}`}
          title={connectionStatus}
        />
      </div>
    </header>
  );
}
```

---

### `frontend/src/components/WatchlistPanel.tsx` (component, request-response)

**Analog:** `backend/app/routes/watchlist.py` (provides GET /api/watchlist — this component is the consumer)

**GET /api/watchlist response shape** — from `backend/app/routes/watchlist.py` lines 108-118:
```python
# Backend returns:
return {"tickers": [
    {
        "ticker": "AAPL",
        "added_at": "2024-...",
        "price": 190.5,          # float | None
        "change_percent": 0.23,  # float | None
        "direction": "up",       # "up"|"down"|"flat" | None
    },
    ...
]}
```

**Pattern:**
```typescript
// frontend/src/components/WatchlistPanel.tsx
import useSWR from 'swr';
import WatchlistRow from './WatchlistRow';
import type { WatchlistResponse } from '@/types/market';

interface Props {
  selectedTicker: string | null;
  onSelectTicker: (ticker: string) => void;
}

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export default function WatchlistPanel({ selectedTicker, onSelectTicker }: Props) {
  const { data } = useSWR<WatchlistResponse>('/api/watchlist', fetcher);
  const tickers = data?.tickers?.map((t) => t.ticker) ?? [];

  return (
    <div className="w-64 shrink-0">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="text-terminal-muted border-b border-terminal-border">
            <th className="text-left py-1 pl-3">Symbol</th>
            <th className="text-right py-1">Price</th>
            <th className="text-right py-1">Chg%</th>
            <th className="text-right py-1 pr-2">Chart</th>
          </tr>
        </thead>
        <tbody>
          {tickers.map((ticker) => (
            <WatchlistRow
              key={ticker}
              ticker={ticker}
              isSelected={ticker === selectedTicker}
              onSelect={() => onSelectTicker(ticker)}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

---

### `frontend/src/components/WatchlistRow.tsx` (component, event-driven)

**Analog:** None (pure frontend pattern — no backend equivalent for DOM manipulation)

**Responsibilities:** price flash animation (D-07), sparkline host (D-06), selected state (D-10)

**Pattern** — from RESEARCH.md Patterns 3 & 4:
```typescript
// frontend/src/components/WatchlistRow.tsx
import { useEffect, useRef } from 'react';
import { useTicker } from '@/stores/priceStore';
import SparklineChart from './SparklineChart';

interface Props {
  ticker: string;
  isSelected: boolean;
  onSelect: () => void;
}

export default function WatchlistRow({ ticker, isSelected, onSelect }: Props) {
  // Per-ticker selector — only this ticker's update triggers re-render
  const priceUpdate = useTicker(ticker);

  // Flash animation refs — pattern from RESEARCH.md Pattern 4
  const priceRef = useRef<HTMLTableCellElement>(null);
  const flashTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!priceUpdate || !priceRef.current) return;
    if (priceUpdate.direction === 'flat') return;

    const cell = priceRef.current;
    if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current);

    const cls = priceUpdate.direction === 'up' ? 'flash-up' : 'flash-down';
    cell.classList.remove('flash-up', 'flash-down');
    void cell.offsetWidth; // Force reflow — re-triggers animation on rapid updates
    cell.classList.add(cls);

    flashTimeoutRef.current = setTimeout(() => {
      cell.classList.remove(cls);
    }, 500);

    return () => {
      if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current);
    };
  }, [priceUpdate?.direction, priceUpdate?.timestamp]);

  // Selected row: 2px yellow left accent bar + slightly lighter bg (D-10)
  const rowClass = isSelected
    ? 'border-l-2 border-terminal-accent bg-terminal-surface cursor-pointer'
    : 'border-l-2 border-transparent cursor-pointer hover:bg-terminal-surface/50';

  return (
    <tr className={rowClass} onClick={onSelect}>
      <td className="py-1 pl-1 font-semibold text-terminal-text">{ticker}</td>
      <td ref={priceRef} className="text-right py-1 tabular-nums">
        {priceUpdate?.price?.toFixed(2) ?? '—'}
      </td>
      <td className={`text-right py-1 tabular-nums ${
        priceUpdate?.direction === 'up' ? 'text-terminal-up' :
        priceUpdate?.direction === 'down' ? 'text-terminal-down' :
        'text-terminal-muted'
      }`}>
        {priceUpdate?.change_percent != null
          ? `${priceUpdate.change_percent > 0 ? '+' : ''}${priceUpdate.change_percent.toFixed(2)}%`
          : '—'}
      </td>
      <td className="py-1 pr-2">
        <SparklineChart ticker={ticker} width={80} height={28} />
      </td>
    </tr>
  );
}
```

---

### `frontend/src/components/SparklineChart.tsx` (component, event-driven)

**Analog:** None (canvas charting — no backend equivalent)

**Critical v5 API note** — from RESEARCH.md Pitfall 1 and Pattern 3:
- v5: `chart.addSeries(LineSeries, options)` — `LineSeries` explicitly imported
- v4 (wrong): `chart.addLineSeries(options)` — method removed in v5, compiles but throws at runtime

**Pattern** — from RESEARCH.md Pattern 3 (verified against tradingview.github.io React tutorial):
```typescript
// frontend/src/components/SparklineChart.tsx
import { useEffect, useRef } from 'react';
import { createChart, LineSeries } from 'lightweight-charts'; // v5: LineSeries is a named export
import type { ISeriesApi, IChartApi } from 'lightweight-charts';
import { useTicker } from '@/stores/priceStore';

interface Props {
  ticker: string;
  width?: number;
  height?: number;
}

export default function SparklineChart({ ticker, width = 80, height = 28 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Line'> | null>(null);

  const priceUpdate = useTicker(ticker);

  // Mount effect: create chart + series; cleanup on unmount
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      width,
      height,
      layout: {
        background: { color: 'transparent' },
        textColor: 'transparent',
      },
      rightPriceScale: { visible: false },
      timeScale: { visible: false },
      crosshair: { mode: 0 }, // CrosshairMode.Hidden = 0
      grid: {
        vertLines: { visible: false },
        horzLines: { visible: false },
      },
      handleScroll: false,
      handleScale: false,
    });

    // v5 API: addSeries with explicit LineSeries type import
    const series = chart.addSeries(LineSeries, {
      color: '#209dd7',   // terminal-blue from design spec
      lineWidth: 1,
    });

    chartRef.current = chart;
    seriesRef.current = series as ISeriesApi<'Line'>;

    return () => {
      chart.remove(); // Cleanup — prevents stacked canvas on hot-reload (Pitfall 4)
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []); // Mount only

  // Update effect: append price point on each SSE tick
  useEffect(() => {
    if (!seriesRef.current || !priceUpdate) return;
    seriesRef.current.update({
      time: Math.floor(priceUpdate.timestamp) as any, // UTCTimestamp in v5
      value: priceUpdate.price,
    });
  }, [priceUpdate]);

  return <div ref={containerRef} style={{ width, height }} />;
}
```

---

### `frontend/next.config.js` (config)

**Pattern** — from RESEARCH.md Code Examples (verified against nextjs.org/docs/pages/guides/static-exports):
```javascript
// frontend/next.config.js
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export',           // Emit static HTML/CSS/JS to out/ directory
  images: { unoptimized: true }, // Required — next/image optimizer disabled in static export
};
module.exports = nextConfig;  // CommonJS — NOT ESM export default (Pitfall 6)
```

---

### `frontend/tailwind.config.js` (config)

**Pattern** — from RESEARCH.md Pattern 5 (verified against nextjs.org Tailwind v3 guide):
```javascript
// frontend/tailwind.config.js  (Tailwind v3 — NOT v4 @theme syntax)
module.exports = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx}',
    './src/components/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        mono: ['var(--font-mono)', 'ui-monospace', 'monospace'],
      },
      colors: {
        terminal: {
          bg:      '#0d1117',   // Main background (PLAN.md §2)
          surface: '#1a1a2e',   // Card/panel surfaces (PLAN.md §2)
          border:  '#30363d',   // Muted gray borders
          text:    '#e6edf3',   // Primary text
          muted:   '#8b949e',   // Secondary/label text
          accent:  '#ecad0a',   // Accent yellow (PLAN.md §2 color scheme)
          blue:    '#209dd7',   // Blue primary (PLAN.md §2 color scheme)
          purple:  '#753991',   // Purple secondary (PLAN.md §2 color scheme)
          up:      '#22c55e',   // Price uptick green
          down:    '#ef4444',   // Price downtick red
          amber:   '#f59e0b',   // Reconnecting/warning amber
        },
      },
      keyframes: {
        flashUp: {
          '0%':   { backgroundColor: 'rgba(34, 197, 94, 0.25)' },
          '100%': { backgroundColor: 'transparent' },
        },
        flashDown: {
          '0%':   { backgroundColor: 'rgba(239, 68, 68, 0.25)' },
          '100%': { backgroundColor: 'transparent' },
        },
      },
      animation: {
        'flash-up':   'flashUp 500ms ease-out forwards',
        'flash-down': 'flashDown 500ms ease-out forwards',
      },
    },
  },
  plugins: [],
};
```

---

### `frontend/src/styles/globals.css` (styles)

**Pattern** — from RESEARCH.md Pattern 4 (CSS flash classes):
```css
/* frontend/src/styles/globals.css */
@tailwind base;
@tailwind components;
@tailwind utilities;

/* Flash animation classes — toggled by WatchlistRow.tsx via JS (D-07) */
/* These use CSS transition (not keyframes) for the class-toggle approach */
.flash-up {
  background-color: rgba(34, 197, 94, 0.25);
  transition: background-color 500ms ease-out;
}

.flash-down {
  background-color: rgba(239, 68, 68, 0.25);
  transition: background-color 500ms ease-out;
}
```

---

## Shared Patterns

### SWR Fetcher (REST data fetching)
**Apply to:** `Header.tsx`, `WatchlistPanel.tsx`
```typescript
// Standard fetcher — one definition, import where needed or define once in a lib file
const fetcher = (url: string) => fetch(url).then((r) => r.json());
```

### snake_case Field Names
**Source:** `backend/app/market/models.py` (to_dict method) + `backend/app/routes/portfolio.py` + `backend/app/routes/watchlist.py`
**Apply to:** ALL files that consume API responses
No snake_to_camel conversion exists in the backend. Frontend TypeScript types must use `snake_case` field names: `previous_price`, `change_percent`, `avg_cost`, `unrealized_pnl`, `pnl_pct`, `added_at`, `total_value`. Using camelCase will silently produce `undefined`.

### useEffect + Cleanup
**Apply to:** `usePriceStream.ts`, `SparklineChart.tsx`, `WatchlistRow.tsx`
Every `useEffect` that allocates a resource (EventSource, chart instance, setTimeout) MUST return a cleanup function. This is the single most important pattern for correctness — backend tests demonstrate the same discipline with `conn.close()` in `finally` blocks (see `backend/app/routes/portfolio.py` lines 260-265).

### Zustand Single Atom Selectors
**Apply to:** All components that read from `usePriceStore`
Never use `usePriceStore(s => ({ a: s.a, b: s.b }))` — this is a Zustand v5 breaking change. Use `const a = usePriceStore(s => s.a)` per value, or `useShallow` if multi-value is unavoidable.

---

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `frontend/src/components/WatchlistRow.tsx` | component | event-driven | DOM class-toggle animations have no backend equivalent |
| `frontend/src/components/SparklineChart.tsx` | component | event-driven | Canvas-based charting has no backend equivalent |
| `frontend/tailwind.config.js` | config | — | CSS configuration has no backend equivalent |
| `frontend/src/styles/globals.css` | styles | — | CSS has no backend equivalent |

All four files must be built from RESEARCH.md patterns exclusively (verified against official library documentation).

---

## Backend API Contract Summary

This section distills all API contracts the frontend must respect, sourced from actual backend files.

### SSE Stream — `GET /api/stream/prices`
**Source:** `backend/app/market/stream.py` lines 80-83
- Media type: `text/event-stream`
- Event format: `data: <JSON>\n\n` where JSON is `Record<string, PriceUpdate>` (all tickers as keys)
- Retry directive: `retry: 1000\n\n` sent on first connect (auto-reconnect in 1s)
- Interval: ~500ms between events

### Portfolio — `GET /api/portfolio`
**Source:** `backend/app/routes/portfolio.py` lines 236-239
```
{ cash: number, total_value: number, positions: Position[] }
```

### Watchlist — `GET /api/watchlist`
**Source:** `backend/app/routes/watchlist.py` lines 108-118
```
{ tickers: [{ ticker, added_at, price, change_percent, direction }] }
```

---

## Metadata

**Analog search scope:** `backend/app/` (all Python source files)
**Files scanned:** 19 backend Python files
**No existing frontend files:** `frontend/` directory does not exist yet — all patterns from RESEARCH.md (verified against official documentation) and backend interface extraction
**Pattern extraction date:** 2026-06-06
