# Phase 3: Frontend Foundation - Research

**Researched:** 2026-06-06
**Domain:** Next.js Pages Router, TradingView Lightweight Charts v5, Zustand v5, EventSource SSE, Tailwind CSS v3
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Pages Router (not App Router) — simpler, battle-tested with `output: 'export'`, no dynamic-route constraints, no RSC complexity
- **D-02:** Single-page layout: `pages/index.tsx` contains the full dashboard; `pages/_app.tsx` stays minimal (global styles, font imports only)
- **D-03:** Zustand for SSE price data — components subscribe to specific slices so only affected components re-render on each 500ms price tick. Connection status also in the Zustand store.
- **D-04:** REST endpoint data (portfolio positions, watchlist list, chat history) fetched via SWR or plain fetch on demand — NOT in Zustand. Clean split: streaming data in Zustand, request-response data fetched where needed.
- **D-05:** TradingView Lightweight Charts for all charts — sparklines (Phase 3) and main ticker chart (Phase 4). Canvas-based, ~40KB, financial-specific. One library across the entire app — no mixing.
- **D-06:** Sparklines implemented as Lightweight Charts mini instances (one per watchlist row), not SVG path math. Handles live updates natively, consistent with main chart.
- **D-07:** CSS class toggle approach: add `.flash-up` (green) or `.flash-down` (red) class on each price update, remove after 500ms via `setTimeout`. Tailwind custom keyframe or transition handles the fade. No inline style updates.
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

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within Phase 3 scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| FE-01 | Next.js TypeScript project configured with static export (`output: 'export'`), served by FastAPI | `create-next-app` scaffold with `--ts --eslint --tailwind --src-dir` flags; `next.config.js` with `output: 'export'`; `next build` emits `out/` |
| FE-02 | Header shows live portfolio total value (updating from SSE), connection status indicator (green/yellow/red dot), and cash balance | Zustand `connectionStatus` drives dot color; portfolio total computed in component from Zustand prices + portfolio REST data |
| FE-03 | Dark terminal theme with backgrounds `#0d1117`/`#1a1a2e`, accent yellow `#ecad0a`, blue `#209dd7`, purple `#753991` | Tailwind v3 `theme.extend.colors` with all locked hex values; JetBrains Mono via `next/font/google` |
| FE-04 | App uses native `EventSource` to connect to `/api/stream/prices` SSE endpoint | Single `useEffect` in app root creates one `EventSource`; updates Zustand store on `onmessage`; closes on unmount |
| FE-05 | Watchlist panel shows all watched tickers with current price, daily change%, and sparkline mini-chart | Compact `<table>` rows; data from Zustand `prices` map keyed by ticker; sparklines as per-row Lightweight Charts instances |
| FE-06 | Prices flash green (uptick) or red (downtick) for ~500ms via CSS transition on each price update | D-07 class toggle: add `.flash-up` / `.flash-down`, `setTimeout(remove, 500)` on the price cell `<td>` |
| FE-07 | Sparklines accumulate price history from SSE since page load (fill in progressively) | Per-row `useRef<ISeriesApi>` for line series; call `series.update({time, value})` on each Zustand price tick |
| FE-08 | Clicking a ticker in the watchlist selects it for the main chart area | Local state `selectedTicker` in parent; passed as prop or via context; row gets `border-l-2 border-[#ecad0a]` + `bg-[#1a1a2e]` when selected |
</phase_requirements>

---

## Summary

Phase 3 bootstraps the Next.js frontend from scratch: scaffold, dark theme, SSE wiring, and the watchlist panel with price flash animations and sparklines. All key architectural decisions are locked from CONTEXT.md, so research focuses on exact APIs, known pitfalls, and verification that the chosen stack works together correctly.

The critical integration point is Lightweight Charts v5, which introduced a **breaking API change** from v4: series are now created via `chart.addSeries(LineSeries, options)` instead of `chart.addLineSeries(options)`. The `LineSeries` type must be explicitly imported. Getting this wrong compiles fine but produces no series. This is the single most likely blocker for an agent implementing sparklines from memory.

Zustand v5 introduced a behavioral change with object selectors: object selectors without `useShallow` now cause React to throw a maximum update depth error. Any component that subscribes to multiple pieces of Zustand state with a single object return must use `useShallow`. This is especially relevant for the header component which needs both `prices` (for portfolio value computation) and `connectionStatus`.

The static export constraint (`output: 'export'`) disables API Routes, `getServerSideProps`, and `Image` optimization (unless a custom loader is supplied). None of these are needed here — all data comes from same-origin `/api/*` REST + SSE.

**Primary recommendation:** Scaffold with `npx create-next-app@latest frontend --ts --eslint --tailwind --src-dir --no-app --no-turbopack --no-react-compiler --import-alias "@/*" --use-npm`, then manually set `output: 'export'` in `next.config.js` and add all locked color/font configuration.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| SSE price streaming | Browser/Client | — | `EventSource` is a browser API; runs client-side only inside `useEffect` |
| Zustand price state | Browser/Client | — | In-memory store initialized and mutated client-side; no server state |
| Watchlist panel render | Browser/Client | — | Pure display from Zustand slice; no server involvement |
| Sparkline chart instances | Browser/Client | — | Canvas-based, DOM-bound; one instance per row, lives entirely in browser |
| Portfolio REST data | API/Backend | Browser/Client (consumer) | Fetched from `/api/portfolio` by the frontend; backend owns the data |
| Watchlist REST data | API/Backend | Browser/Client (consumer) | Fetched from `/api/watchlist` on load; backend owns the list |
| Static file serving | CDN/Static (FastAPI) | — | Next.js `out/` served by FastAPI `StaticFiles`; no SSR |
| Font self-hosting | CDN/Static | — | `next/font/google` downloads at build time, serves from same origin |

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| next | 16.2.7 | React framework with static export | Locked requirement; Pages Router + `output: 'export'` |
| react | 19.x (bundled with next) | UI rendering | Bundled with Next.js 16 |
| typescript | 6.0.3 | Type safety | Default in `create-next-app`; Pages Router TypeScript support verified |
| lightweight-charts | 5.2.0 | Financial charting (sparklines + main chart) | Locked D-05; TradingView official; ~40KB; canvas-based |
| zustand | 5.0.14 | SSE price state management | Locked D-03; 500ms tick isolation per-slice; v5 uses `useSyncExternalStore` |
| tailwindcss | 3.4.19 | Utility-first CSS | Locked D-03 area; v3 for `tailwind.config.js` compatibility with Next.js 16 setup guide |
| postcss | 8.5.15 | CSS transform pipeline | Required peer of Tailwind v3 |
| autoprefixer | 10.5.0 | CSS vendor prefixes | Required peer of Tailwind v3 |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| swr | 2.4.1 | REST data fetching (portfolio, watchlist) | Locked D-04 area; preferred over plain fetch for automatic revalidation |
| @fontsource/jetbrains-mono | 5.2.8 | JetBrains Mono font (alternative to next/font) | Use `next/font/google` instead — zero extra package needed |
| lucide-react | 1.17.0 | Icon library if needed | UI-SPEC says icon-free in Phase 3; keep as fallback if an icon becomes necessary |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `next/font/google` for JetBrains Mono | `@fontsource/jetbrains-mono` npm package | `next/font/google` is zero-extra-package, builds font at compile time, no external requests; prefer it |
| Tailwind v3 | Tailwind v4 | Next.js 16 guides explicitly document v3 via `tailwind.config.js`; v4 uses `@theme` CSS directive — different config pattern, higher risk |
| zustand `useShallow` | custom equality fn | `useShallow` is the official recommended API for object selectors in v5 |
| SWR for REST | plain `fetch` + `useEffect` | SWR adds automatic revalidation, deduplication, and loading/error states; prefer SWR for portfolio and watchlist endpoints |

**Installation (production deps):**
```bash
npm install lightweight-charts zustand swr
```

**Installation (dev deps):**
```bash
npm install -D tailwindcss@^3 postcss autoprefixer
npm install -D jest jest-environment-jsdom @testing-library/react @testing-library/dom @testing-library/jest-dom ts-node @types/jest jest-canvas-mock
```

---

## Package Legitimacy Audit

> slopcheck could not be installed in this environment. All packages below are tagged `[ASSUMED]` for download counts and ages, with registry existence confirmed via `npm view`. Planner must gate each install behind a `checkpoint:human-verify` task.

| Package | Registry | Age | Source Repo | npm view | Disposition |
|---------|----------|-----|-------------|----------|-------------|
| next | npm | 7+ yrs | github.com/vercel/next.js | 16.2.7 | Approved — official Vercel product [VERIFIED: nextjs.org] |
| lightweight-charts | npm | 7+ yrs | github.com/tradingview/lightweight-charts | 5.2.0 | Approved — official TradingView product [VERIFIED: tradingview.github.io] |
| zustand | npm | 5+ yrs | github.com/pmndrs/zustand | 5.0.14 | Approved — well-known pmndrs package [VERIFIED: github.com/pmndrs/zustand] |
| swr | npm | 5+ yrs | github.com/vercel/swr | 2.4.1 | Approved — official Vercel product [VERIFIED: swr.vercel.app] |
| tailwindcss | npm | 7+ yrs | github.com/tailwindlabs/tailwindcss | 3.4.19 | Approved — official Tailwind Labs product [VERIFIED: tailwindcss.com] |
| postcss | npm | 10+ yrs | github.com/postcss/postcss | 8.5.15 | Approved — foundational CSS tooling [ASSUMED download count] |
| autoprefixer | npm | 10+ yrs | github.com/postcss/autoprefixer | 10.5.0 | Approved — foundational CSS tooling [ASSUMED download count] |
| jest-canvas-mock | npm | 5+ yrs | github.com/hustcc/jest-canvas-mock | 2.5.2 | Approved — standard canvas test mock [ASSUMED download count] |
| lucide-react | npm | 3+ yrs | github.com/lucide-icons/lucide | 1.17.0 | Approved — popular icon library [ASSUMED download count] |
| jest | npm | 10+ yrs | github.com/jestjs/jest | 30.4.2 | Approved — Meta/Facebook testing framework [ASSUMED download count] |

**Packages removed due to slopcheck [SLOP] verdict:** none  
**Packages flagged as suspicious [SUS]:** none

*slopcheck was unavailable at research time. All packages above are confirmed to exist on npm registry via `npm view`, and all production packages are confirmed via official documentation. Planner must add a `checkpoint:human-verify` before each `npm install` step.*

---

## Architecture Patterns

### System Architecture Diagram

```
Browser
│
├─ pages/_app.tsx
│   ├─ Imports global CSS + JetBrains Mono via next/font/google
│   ├─ Applies font CSS variable to <html>
│   └─ Mounts <Component {...pageProps} />
│
└─ pages/index.tsx  (single-page dashboard)
    ├─ useEffect: creates EventSource → /api/stream/prices
    │   ├─ onopen  → setConnectionStatus('connected')
    │   ├─ onmessage → JSON.parse(event.data) → setPrices(data)
    │   └─ onerror → setConnectionStatus('reconnecting'/'disconnected')
    │
    ├─ <Header />
    │   ├─ reads connectionStatus from Zustand (single atom)
    │   ├─ reads cash from SWR /api/portfolio
    │   └─ computes portfolioTotal from Zustand prices + position data
    │
    └─ <WatchlistPanel />
        └─ For each ticker in watchlistTickers (from /api/watchlist):
            └─ <WatchlistRow ticker={ticker} />
                ├─ reads priceUpdate = usePriceStore(state => state.prices[ticker])
                ├─ flash effect: useRef(timeoutId), add/remove CSS class on priceUpdate change
                └─ <SparklineChart ticker={ticker} />
                    ├─ useRef(chartContainerRef)  ← DOM mount point
                    ├─ useRef(seriesRef)           ← ISeriesApi handle
                    ├─ useEffect (mount): createChart → addSeries(LineSeries) → store in seriesRef
                    │   return () => chart.remove()  ← cleanup
                    └─ useEffect (tick): seriesRef.current.update({time, value})
                        triggered by priceUpdate change
```

### Recommended Project Structure
```
frontend/
├── src/
│   ├── pages/
│   │   ├── _app.tsx          # Global layout: font, global CSS
│   │   └── index.tsx         # Full dashboard (FE-01, FE-02, FE-04)
│   ├── components/
│   │   ├── Header.tsx        # Portfolio value, cash, connection dot (FE-02)
│   │   ├── WatchlistPanel.tsx # Table wrapper; fetches /api/watchlist (FE-05)
│   │   ├── WatchlistRow.tsx  # One row: flash + sparkline (FE-06, FE-07, FE-08)
│   │   └── SparklineChart.tsx # Lightweight Charts mini instance (FE-07)
│   ├── stores/
│   │   └── priceStore.ts     # Zustand store: prices, connectionStatus (FE-04, D-03)
│   ├── hooks/
│   │   └── usePriceStream.ts # EventSource setup/teardown (FE-04)
│   └── types/
│       └── market.ts         # PriceUpdate TypeScript interface matching backend
├── styles/
│   └── globals.css           # Tailwind directives + flash keyframes
├── next.config.js            # output: 'export'
├── tailwind.config.js        # Custom colors, font, keyframes
├── tsconfig.json             # Strict TS
└── package.json
```

### Pattern 1: Zustand Price Store (TypeScript)
**What:** Single store for streaming state. Components subscribe to individual tickers via selector to avoid re-rendering when other tickers update.
**When to use:** Any component that displays live price data.

```typescript
// Source: github.com/pmndrs/zustand README (verified)
// src/stores/priceStore.ts
import { create } from 'zustand';

interface PriceUpdate {
  ticker: string;
  price: number;
  previous_price: number;
  timestamp: number;
  change: number;
  change_percent: number;
  direction: 'up' | 'down' | 'flat';
}

interface PriceStore {
  prices: Record<string, PriceUpdate>;
  connectionStatus: 'connected' | 'reconnecting' | 'disconnected';
  setPrices: (data: Record<string, PriceUpdate>) => void;
  setConnectionStatus: (status: PriceStore['connectionStatus']) => void;
}

export const usePriceStore = create<PriceStore>()((set) => ({
  prices: {},
  connectionStatus: 'disconnected',
  setPrices: (data) => set({ prices: data }),
  setConnectionStatus: (status) => set({ connectionStatus: status }),
}));

// Per-row selector — only this ticker's data triggers re-render
export const useTicker = (ticker: string) =>
  usePriceStore((state) => state.prices[ticker]);
```

### Pattern 2: EventSource Setup with Zustand (SSR-safe)
**What:** Single `useEffect` in the page root opens one `EventSource`, feeds Zustand. Runs only on client (useEffect is client-only).
**When to use:** In `pages/index.tsx` or a `usePriceStream` hook called from there.

```typescript
// Source: nextjs.org hydration error docs + EventSource MDN pattern [VERIFIED: nextjs.org docs]
// src/hooks/usePriceStream.ts
import { useEffect } from 'react';
import { usePriceStore } from '@/stores/priceStore';

export function usePriceStream() {
  const setPrices = usePriceStore((s) => s.setPrices);
  const setConnectionStatus = usePriceStore((s) => s.setConnectionStatus);

  useEffect(() => {
    // useEffect only runs client-side — no window/EventSource SSR issue
    const es = new EventSource('/api/stream/prices');

    es.onopen = () => setConnectionStatus('connected');

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setPrices(data);
      } catch {
        // Ignore malformed events
      }
    };

    es.onerror = () => {
      // EventSource auto-reconnects; CONNECTING state during reconnect
      if (es.readyState === EventSource.CONNECTING) {
        setConnectionStatus('reconnecting');
      } else {
        setConnectionStatus('disconnected');
      }
    };

    return () => {
      es.close();
      setConnectionStatus('disconnected');
    };
  }, []); // Empty deps — one connection for the lifetime of the page
}
```

### Pattern 3: Lightweight Charts v5 Sparkline (per-row)
**What:** One chart instance per WatchlistRow, created on mount, destroyed on unmount. Live updates via `series.update()`.
**When to use:** SparklineChart component rendered once per ticker row.

```typescript
// Source: tradingview.github.io/lightweight-charts/tutorials/react/simple [VERIFIED]
// Key v5 change: addSeries(LineSeries, options) replaces addLineSeries(options)
import { useEffect, useRef } from 'react';
import { createChart, LineSeries } from 'lightweight-charts'; // v5: explicit import

interface SparklineProps {
  ticker: string;
  width?: number;
  height?: number;
}

export function SparklineChart({ ticker, width = 80, height = 32 }: SparklineProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const seriesRef = useRef<ReturnType<typeof chart.addSeries> | null>(null);
  const chartRef = useRef<ReturnType<typeof createChart> | null>(null);

  const priceUpdate = useTicker(ticker);

  // Mount: create chart + series
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

    // v5 API: addSeries with LineSeries type imported separately
    const series = chart.addSeries(LineSeries, {
      color: '#209dd7',
      lineWidth: 1,
    });

    chartRef.current = chart;
    seriesRef.current = series;

    return () => {
      chart.remove(); // Cleanup: destroys canvas, removes DOM nodes
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []); // Mount only — width/height changes don't need teardown in Phase 3

  // Update: append new price point on each SSE tick
  useEffect(() => {
    if (!seriesRef.current || !priceUpdate) return;
    seriesRef.current.update({
      time: Math.floor(priceUpdate.timestamp) as any,
      value: priceUpdate.price,
    });
  }, [priceUpdate]);

  return <div ref={containerRef} />;
}
```

### Pattern 4: Price Flash (CSS class toggle)
**What:** Add `.flash-up` or `.flash-down` to the price cell on direction change, remove after 500ms.
**When to use:** In `WatchlistRow` component when `priceUpdate.direction` changes.

```typescript
// Source: CONTEXT.md D-07 + UI-SPEC interaction contract [VERIFIED: CONTEXT.md]
const priceRef = useRef<HTMLTableCellElement>(null);
const flashTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

useEffect(() => {
  if (!priceUpdate || !priceRef.current) return;
  if (priceUpdate.direction === 'flat') return;

  const cell = priceRef.current;

  // Clear any in-flight timeout from a previous update
  if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current);

  const cls = priceUpdate.direction === 'up' ? 'flash-up' : 'flash-down';
  cell.classList.remove('flash-up', 'flash-down');

  // Force reflow so repeated same-direction updates re-trigger animation
  void cell.offsetWidth;

  cell.classList.add(cls);
  flashTimeoutRef.current = setTimeout(() => {
    cell.classList.remove(cls);
  }, 500);

  return () => {
    if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current);
  };
}, [priceUpdate?.direction, priceUpdate?.timestamp]);
```

```css
/* globals.css — flash classes */
.flash-up {
  background-color: rgba(34, 197, 94, 0.25);
  transition: background-color 500ms ease-out;
}
.flash-down {
  background-color: rgba(239, 68, 68, 0.25);
  transition: background-color 500ms ease-out;
}
```

### Pattern 5: JetBrains Mono via next/font in Pages Router
**What:** Self-hosted font loaded at build time, applied globally via CSS variable.
**When to use:** In `pages/_app.tsx` (applies to all routes).

```typescript
// Source: nextjs.org/docs/pages/api-reference/components/font [VERIFIED: nextjs.org]
// pages/_app.tsx
import { JetBrains_Mono } from 'next/font/google';
import type { AppProps } from 'next/app';
import '@/styles/globals.css';

const jetbrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  weight: ['400', '600'],       // Only the two weights used
  variable: '--font-mono',      // CSS variable for Tailwind integration
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

```javascript
// tailwind.config.js (v3 — NOT v4 @theme syntax)
// Source: nextjs.org/docs/pages/guides/tailwind-v3-css [VERIFIED: nextjs.org]
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
          bg:      '#0d1117',
          surface: '#1a1a2e',
          border:  '#30363d',
          text:    '#e6edf3',
          muted:   '#8b949e',
          accent:  '#ecad0a',
          blue:    '#209dd7',
          purple:  '#753991',
          up:      '#22c55e',
          down:    '#ef4444',
          amber:   '#f59e0b',
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

### Anti-Patterns to Avoid
- **Using `chart.addLineSeries()`:** This is the v4 API. In v5, the method is `chart.addSeries(LineSeries, options)` where `LineSeries` must be imported separately. The old method was removed.
- **Importing `LineSeries` from lightweight-charts in a UMD context:** Only valid in ESM. The import is `import { createChart, LineSeries } from 'lightweight-charts'`.
- **Creating EventSource at module scope or in component body:** Must be inside `useEffect` to avoid `window is not defined` during Next.js SSR/hydration.
- **Object selectors in Zustand v5 without `useShallow`:** `usePriceStore(s => ({ a: s.a, b: s.b }))` causes maximum update depth errors in v5. Use `useShallow` or separate atom selectors.
- **Using Tailwind v4 `@theme` syntax with Next.js 16:** The Next.js official guide documents v3 `tailwind.config.js` config. Mixing v4 CSS-based config with v3 install is a hard error.
- **Using `getStaticPaths` with `fallback: true` or `'blocking'`:** Incompatible with `output: 'export'`. Irrelevant for single-page SPA but noted for completeness.
- **Using `next/image` without a custom loader:** Unsupported in static export mode. Use `<img>` directly or configure a custom loader.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Financial sparklines | SVG path math, canvas drawing | `lightweight-charts` `LineSeries` | Handles time axis, NaN gaps, high-frequency updates without visible lag |
| Font self-hosting | Download font files, write `@font-face` | `next/font/google` | Build-time download, automatic subset optimization, CLS prevention baked in |
| REST data fetching with loading/error | `useState` + `useEffect` + manual error handling | `swr` | Deduplication, revalidation on focus, loading state, error retry |
| Price state diffing | Per-component local state | Zustand slice selectors | Subscription isolation ensures only affected rows re-render on each 500ms tick |

**Key insight:** Hand-rolling sparklines is a 200-line trap — time axis normalization, gap handling, animation smoothing, and resize handling are all edge cases the library already solves.

---

## Common Pitfalls

### Pitfall 1: Lightweight Charts v4 vs v5 API Confusion
**What goes wrong:** Agent writes `chart.addLineSeries({ color: '#209dd7' })` — this throws `TypeError: chart.addLineSeries is not a function` at runtime, not at compile time.
**Why it happens:** v5 removed `addLineSeries`/`addAreaSeries`/etc. in favor of `addSeries(SeriesType, options)`. Training data contains v4 patterns.
**How to avoid:** Always verify with `import { createChart, LineSeries } from 'lightweight-charts'` and use `chart.addSeries(LineSeries, options)`.
**Warning signs:** No TypeScript error but chart container renders empty.

### Pitfall 2: Zustand v5 Object Selector Maximum Update Depth
**What goes wrong:** `const { prices, connectionStatus } = usePriceStore(s => ({ prices: s.prices, connectionStatus: s.connectionStatus }))` causes React to throw "Maximum update depth exceeded" in v5.
**Why it happens:** v5 uses `useSyncExternalStore` with strict reference equality. A new object `{}` is created on every render, triggering an infinite re-render loop.
**How to avoid:** Use separate selectors per atom: `const prices = usePriceStore(s => s.prices)` and `const status = usePriceStore(s => s.connectionStatus)`. Or use `useShallow` if you need multiple values in one call.
**Warning signs:** Browser console shows "Maximum update depth exceeded" immediately after the page loads.

### Pitfall 3: EventSource `window is not defined` in Static Export
**What goes wrong:** `const es = new EventSource(...)` at module or component render level throws `ReferenceError: window is not defined` during build.
**Why it happens:** Next.js pre-renders pages at build time (even for static export); `EventSource` is not available in Node.js.
**How to avoid:** All `EventSource` usage MUST be inside `useEffect(() => { ... }, [])`. `useEffect` is never called during pre-render.
**Warning signs:** Build fails with `ReferenceError: EventSource is not defined`.

### Pitfall 4: Sparkline Chart Instances Not Cleaned Up
**What goes wrong:** `chart.remove()` is not called in the `useEffect` cleanup. Over time (or on hot-reload), multiple chart canvases stack up in the DOM container.
**Why it happens:** Each `useEffect` creates a new chart. Without cleanup, the old chart is orphaned but still attached to the DOM element.
**How to avoid:** Always return `() => { chart.remove(); }` from the mount `useEffect`. Store the chart instance in `useRef`, not `useState`.
**Warning signs:** DOM inspector shows multiple `<canvas>` elements inside the sparkline container div.

### Pitfall 5: Flash Animation Not Re-triggering on Rapid Updates
**What goes wrong:** Price updates arrive faster than 500ms; the flash class is still present when the next update arrives, so no visual transition occurs for the second update.
**Why it happens:** Adding a class that's already present does nothing; the CSS transition only fires when the class is added anew.
**How to avoid:** Remove the class, force a reflow (`void element.offsetWidth`), then re-add it. Also clear the previous `setTimeout` before setting a new one.
**Warning signs:** Rapid price changes in the simulator produce no flash after the first tick.

### Pitfall 6: `output: 'export'` Breaks if `next.config.js` Uses `next.config.ts`
**What goes wrong:** Recent `create-next-app` versions generate `next.config.ts` (TypeScript). The `module.exports = nextConfig` pattern does not work in `.ts` files; it must use `export default`.
**Why it happens:** TypeScript config files use ES modules syntax.
**How to avoid:** Use `next.config.js` with `module.exports` OR `next.config.ts` with `export default`. Do not mix CommonJS and ESM syntax.
**Warning signs:** Build error: `SyntaxError: The requested module is not a module`.

### Pitfall 7: Tailwind v4 Generated by `create-next-app --tailwind` 
**What goes wrong:** `create-next-app@16+` with `--tailwind` flag may scaffold Tailwind v4 (which uses `globals.css` `@theme` directive, not `tailwind.config.js`). Custom colors added to `tailwind.config.js` have no effect.
**Why it happens:** Tailwind defaulted to v4 in recent Next.js scaffolding.
**How to avoid:** After scaffold, check `package.json` for `tailwindcss` version. If it is v4 (4.x.x), remove and reinstall with `npm install -D tailwindcss@^3 postcss autoprefixer`. Or pass `--no-tailwind` and install v3 manually.
**Warning signs:** `tailwind.config.js` is absent or empty after scaffold; `globals.css` contains `@import "tailwindcss"` instead of `@tailwind base; @tailwind components; @tailwind utilities`.

---

## Code Examples

### Static Export Configuration
```javascript
// Source: nextjs.org/docs/pages/guides/static-exports [VERIFIED: nextjs.org]
// next.config.js
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export',
  // images.unoptimized is required if using next/image without custom loader
  // For this project: avoid next/image entirely, use <img> directly
  images: { unoptimized: true },
};
module.exports = nextConfig;
```

### Tailwind v3 Install Command (verified exact)
```bash
# Source: nextjs.org/docs/pages/guides/tailwind-v3-css [VERIFIED: nextjs.org]
npm install -D tailwindcss@^3 postcss autoprefixer
npx tailwindcss init -p
```

### Scaffold Command (Pages Router)
```bash
# Source: nextjs.org/docs/pages/api-reference/cli/create-next-app [VERIFIED: nextjs.org]
# --no-app means Pages Router; --no-turbopack forces webpack (stable)
npx create-next-app@latest frontend \
  --ts \
  --eslint \
  --no-tailwind \
  --src-dir \
  --no-app \
  --no-turbopack \
  --no-react-compiler \
  --import-alias "@/*" \
  --use-npm
# Then manually install Tailwind v3 as shown above
```

Note: `--no-tailwind` is used because the flag may scaffold Tailwind v4; we install v3 explicitly afterward.

### `create-next-app` Pitfall 7 Check
```bash
# After scaffold, verify Tailwind version
cat frontend/package.json | grep tailwindcss
# Should show "tailwindcss": "^3.x.x" after manual install
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `chart.addLineSeries(options)` | `chart.addSeries(LineSeries, options)` with explicit import | v5.0.0 (Jan 2025) | All existing v4 code examples are wrong for v5; import `LineSeries` from package |
| Zustand object selectors | Zustand `useShallow` for multi-value subscriptions | v5.0.0 (Oct 2024) | Without `useShallow`, object selectors cause infinite re-render loop |
| `next export` CLI command | `output: 'export'` in `next.config.js` | v14.0.0 (Oct 2023) | Old tutorials using `next export` will fail; the config key is now required |
| Tailwind config in `tailwind.config.js` | Tailwind v4: `@theme` in CSS file | v4.0 (Jan 2025) | Next.js 16 docs guide installs v3; v4 has different config pattern |

**Deprecated/outdated:**
- `chart.addLineSeries()`, `chart.addAreaSeries()`, etc.: Removed in v5. Use `chart.addSeries(LineSeries)`, `chart.addSeries(AreaSeries)`.
- `chart.addSeriesMarkers()`: Extracted to `createSeriesMarkers()` as a separate primitive import.
- Tailwind v3 `tailwind.config.js` `theme.extend.animation` keyframes: Still valid in v3; do NOT use v4 `@theme` CSS directive with a v3 install.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | swr is the better choice over plain `fetch` + `useEffect` for portfolio/watchlist REST calls | Standard Stack | Low — either works; SWR adds loading state which simplifies Header component |
| A2 | jest 30.x is compatible with Next.js 16 | Package Audit | Low — if incompatible, jest 29.x is the fallback |
| A3 | `--no-app` flag in create-next-app prevents App Router scaffold | Code Examples | Medium — if flag behavior changed, agent must manually delete `app/` directory after scaffold |
| A4 | `CrosshairMode.Hidden` corresponds to numeric value `0` in v5 | Code Examples / Sparkline | Medium — if wrong, crosshair still appears over sparklines; use `crosshair: { mode: CrosshairMode.Hidden }` with explicit import as fallback |

---

## Open Questions (RESOLVED)

1. **Lightweight Charts time format for SSE timestamps** — RESOLVED: use `time: Math.floor(priceUpdate.timestamp) as UTCTimestamp` (cast with the `UTCTimestamp` type import from `lightweight-charts`); call `series.update()` per tick. If "time not in order" errors surface at runtime, fall back to accumulating points in an array and calling `series.setData(points)`. This is the approach implemented in Plan 04 Task 1 (SparklineChart).
   - What we know: Backend sends `timestamp` as Unix epoch seconds (float); Lightweight Charts v5 `LineSeries` accepts `{ time, value }` where `time` can be a UTC timestamp (seconds) or a date string
   - What was unclear: Whether `Math.floor(priceUpdate.timestamp)` produces a valid UTCTimestamp or requires a cast — resolved: a cast to `UTCTimestamp` is required; `Math.floor` yields integer seconds which is the accepted UTCTimestamp form
   - Resolution: Use `time: Math.floor(priceUpdate.timestamp) as UTCTimestamp`; `setData` fallback only if time-ordering errors appear

2. **useShallow import path in zustand v5** — RESOLVED: not needed for this phase. The plans use separate single-atom selectors (`usePriceStore((s) => s.connectionStatus)`, `usePriceStore((s) => s.setPrices)`, etc.) per Pitfall 2 guidance, so no object selector and therefore no `useShallow` import is required anywhere in Phase 3. If a future component needs multiple atoms in one call, import `useShallow` from `'zustand/react/shallow'` (path confirmed unchanged from v4 to v5).
   - What we know: `useShallow` exists in zustand v5 from `'zustand/react/shallow'`
   - What was unclear: Whether the path changed between zustand 4 and 5 — resolved: the path is unchanged (`'zustand/react/shallow'`)
   - Resolution: Separate atom selectors are used throughout Phase 3 — `useShallow` is not needed

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Node.js | npm install, next build | Yes | v25.8.0 | — |
| npm | Package install | Yes | 11.11.0 | — |
| Docker | Phase 5 (not this phase) | Yes | 29.2.1 | — |
| `create-next-app` | Phase 3 scaffold | Available via npx | via next@16 | Manual scaffold |

**Missing dependencies with no fallback:** None.

**Missing dependencies with fallback:** None — all Phase 3 tools are npm-resolvable.

---

## Validation Architecture

> `workflow.nyquist_validation: true` in `.planning/config.json` — validation section included.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | Jest 30.4.2 + React Testing Library 16.3.2 |
| Config file | `frontend/jest.config.js` — Wave 0 gap |
| Quick run command | `npm test -- --testPathPattern=<file> --watchAll=false` |
| Full suite command | `npm test -- --watchAll=false` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| FE-01 | `next build` produces `out/` with static HTML | build smoke | `cd frontend && npm run build` | ❌ Wave 0: `next.config.js` with `output: 'export'` |
| FE-02 | Header renders connection dot + portfolio value + cash | unit | `npm test -- --testPathPattern=Header` | ❌ Wave 0 |
| FE-03 | Dark background color applied to root element | unit | `npm test -- --testPathPattern=index` | ❌ Wave 0 |
| FE-04 | EventSource is created on mount, closed on unmount | unit | `npm test -- --testPathPattern=usePriceStream` | ❌ Wave 0 |
| FE-05 | Watchlist panel renders ticker rows | unit | `npm test -- --testPathPattern=WatchlistPanel` | ❌ Wave 0 |
| FE-06 | Flash class added/removed on direction change | unit | `npm test -- --testPathPattern=WatchlistRow` | ❌ Wave 0 |
| FE-07 | Sparkline chart instance created and series updated | unit (canvas mock) | `npm test -- --testPathPattern=SparklineChart` | ❌ Wave 0 |
| FE-08 | Clicking row selects ticker; selected row has accent bar | unit | `npm test -- --testPathPattern=WatchlistRow` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `cd frontend && npm test -- --watchAll=false --testPathPattern=<changed-component>`
- **Per wave merge:** `cd frontend && npm test -- --watchAll=false`
- **Phase gate:** Full suite green + `npm run build` succeeds before `/gsd:verify-work`

### Wave 0 Gaps (all new — frontend directory does not exist)
- [ ] `frontend/jest.config.js` — Jest + next/jest configuration with jsdom + canvas mock
- [ ] `frontend/jest.setup.ts` — `import '@testing-library/jest-dom'` + `import 'jest-canvas-mock'`
- [ ] `frontend/__tests__/index.test.tsx` — covers FE-03 (root element has bg-terminal-bg dark theme class)
- [ ] `frontend/__tests__/Header.test.tsx` — covers FE-02
- [ ] `frontend/__tests__/WatchlistPanel.test.tsx` — covers FE-05
- [ ] `frontend/__tests__/WatchlistRow.test.tsx` — covers FE-06, FE-08
- [ ] `frontend/__tests__/SparklineChart.test.tsx` — covers FE-07 (requires jest-canvas-mock)
- [ ] `frontend/__tests__/usePriceStream.test.tsx` — covers FE-04 (mock EventSource)
- [ ] `frontend/__mocks__/nextFontMock.js` — mock for `next/font/google` as required by next/jest [VERIFIED: nextjs.org]

**Testing note on Lightweight Charts:** The library renders to HTML5 canvas. Jest runs in jsdom which has no real canvas. `jest-canvas-mock` provides a mock canvas environment so `createChart()` does not throw. Tests for `SparklineChart` verify that the chart is created and `series.update()` is called — they do not assert pixel output.

**Testing note on EventSource:** jsdom does not implement `EventSource`. Tests for `usePriceStream` must mock `EventSource` with `jest.fn()` or a manual mock class. Verify mount/unmount behavior by checking `es.close()` was called.

---

## Security Domain

> `security_enforcement` not explicitly set in config — treating as enabled.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | No auth in this app (by design) |
| V3 Session Management | No | No sessions |
| V4 Access Control | No | Single-user, no ACL |
| V5 Input Validation | Minimal | SSE data is parsed with `JSON.parse` in a try/catch; no user-controlled input in Phase 3 |
| V6 Cryptography | No | No secrets handled client-side |

### Known Threat Patterns for Next.js Static Frontend

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| XSS via SSE event injection | Tampering | `JSON.parse` + React's JSX escaping; never use `dangerouslySetInnerHTML` with SSE data |
| Prototype pollution in JSON.parse | Tampering | Validate parsed shape before using; access only known keys from PriceUpdate interface |
| Memory leak via unclosed EventSource | Denial of Service (client) | Always call `es.close()` in useEffect cleanup |
| Canvas data exfiltration | Information Disclosure | Not applicable — canvas shows only simulated prices, no personal data |

**Overall risk:** Very low. Frontend has no user authentication, no form input in Phase 3, and no secrets. The SSE data stream is same-origin and trusted. Primary concern is defensive coding around `JSON.parse`.

---

## Sources

### Primary (HIGH confidence)
- [nextjs.org/docs/pages/guides/static-exports](https://nextjs.org/docs/pages/guides/static-exports) — static export config, `output: 'export'`, output directory `out/`
- [nextjs.org/docs/pages/api-reference/cli/create-next-app](https://nextjs.org/docs/pages/api-reference/cli/create-next-app) — all scaffold flags including `--no-app`, `--src-dir`, `--ts`
- [nextjs.org/docs/pages/guides/tailwind-v3-css](https://nextjs.org/docs/pages/guides/tailwind-v3-css) — exact v3 install command; `tailwind.config.js` content paths
- [nextjs.org/docs/pages/api-reference/components/font](https://nextjs.org/docs/pages/api-reference/components/font) — `JetBrains_Mono` from `next/font/google`, `variable`, `weight` array, CSS variable Tailwind integration
- [nextjs.org/docs/pages/guides/testing/jest](https://nextjs.org/docs/pages/guides/testing/jest) — Jest setup with `next/jest`, `nextFontMock.js`, required packages
- [tradingview.github.io/lightweight-charts/docs/migrations/from-v4-to-v5](https://tradingview.github.io/lightweight-charts/docs/migrations/from-v4-to-v5) — `addSeries(LineSeries, options)` breaking change; explicit import requirement
- [tradingview.github.io/lightweight-charts/tutorials/react/simple](https://tradingview.github.io/lightweight-charts/tutorials/react/simple) — useRef + useEffect + chart.remove() cleanup pattern
- [github.com/pmndrs/zustand README](https://github.com/pmndrs/zustand/blob/main/README.md) — `create<State>()()` TypeScript pattern; `useShallow` from `'zustand/react/shallow'`

### Secondary (MEDIUM confidence)
- [v3.tailwindcss.com/docs/theme](https://v3.tailwindcss.com/docs/theme) — `theme.extend.keyframes` and `theme.extend.animation` syntax (verified correct for v3)
- [tradingview.github.io/lightweight-charts/tutorials/react/advanced](https://tradingview.github.io/lightweight-charts/tutorials/react/advanced) — useLayoutEffect, isRemoved flag, resize handling patterns
- npm registry `npm view` for all package versions — confirmed published on correct ecosystem

### Tertiary (LOW confidence — training data, not independently verified)
- `CrosshairMode.Hidden` numeric value being `0` — documented in training data, not verified against v5 source; use the enum import as failsafe

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all packages verified via `npm view` and official docs
- Architecture: HIGH — locked decisions from CONTEXT.md; EventSource + Zustand patterns verified
- Pitfalls: HIGH — v5 breaking changes verified against official migration guide; v4→v5 addSeries change is definitive
- Test framework: HIGH — Next.js official Jest setup guide followed

**Research date:** 2026-06-06  
**Valid until:** 2026-07-06 (stable ecosystem — Lightweight Charts and Next.js change slowly)
