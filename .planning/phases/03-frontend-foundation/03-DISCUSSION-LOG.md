# Phase 3: Frontend Foundation - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-06
**Phase:** 3-Frontend Foundation
**Areas discussed:** Next.js Router, State management, Charting library, Watchlist style

---

## Next.js Router

| Option | Description | Selected |
|--------|-------------|----------|
| Pages Router | Simpler `pages/` structure, battle-tested with `output: 'export'`, no dynamic-route constraints, no RSC complexity | ✓ |
| App Router | Modern default from Next.js 13+, works with static export but requires `generateStaticParams`, adds RSC/streaming concepts not needed here | |

**User's choice:** Pages Router

| Option | Description | Selected |
|--------|-------------|----------|
| `pages/index.tsx` as full app | All panels on the index page; `_app.tsx` stays minimal (global styles, font) | ✓ |
| You decide | Leave folder structure to implementing agent | |

**User's choice:** `pages/index.tsx` as the full dashboard layout

---

## State management

| Option | Description | Selected |
|--------|-------------|----------|
| Zustand store | Lightweight external store; components subscribe to specific slices (e.g., one ticker's price); only affected components re-render | ✓ |
| React Context | Built-in, zero dependencies; but every price update re-renders all consumers without careful memoization; problematic at 500ms update frequency | |
| Custom hook only | `useSSEPrices` at root, props passed down; simple but prop-drilling gets tedious across deeply-nested panels | |

**User's choice:** Zustand store

| Option | Description | Selected |
|--------|-------------|----------|
| SSE data in Zustand; REST data via SWR/fetch on demand | Clean separation: streaming vs request-response | ✓ |
| Everything in Zustand | Single store for all data; simpler mental model but large store and many mutations | |
| You decide | Leave the REST-vs-store split to the agent | |

**User's choice:** SSE data in Zustand; REST data via SWR/fetch on demand

---

## Charting library

| Option | Description | Selected |
|--------|-------------|----------|
| TradingView Lightweight Charts | Canvas-based, ~40KB, financial time-series specific, PLAN.md calls it out as preferred | ✓ |
| Recharts | SVG-based, React-native API, larger bundle (~150KB), good DX but may lag at 500ms update frequency | |
| Recharts for sparklines + Lightweight Charts for main chart | Best of both but doubles bundle size and introduces inconsistency | |

**User's choice:** TradingView Lightweight Charts for all charts

| Option | Description | Selected |
|--------|-------------|----------|
| Lightweight Charts mini instances | One per ticker row; handles live updates natively, consistent with main chart | ✓ |
| SVG path from raw prices | Map price array to SVG polyline; zero extra library but requires manual path math and no interactivity | |

**User's choice:** Lightweight Charts mini instances for sparklines

| Option | Description | Selected |
|--------|-------------|----------|
| CSS class toggle | Add `.flash-up`/`.flash-down` class on price update, remove after 500ms via setTimeout; Tailwind transition handles the fade | ✓ |
| Inline style + useEffect | Set backgroundColor inline, clear after 500ms; mixes visual logic into component state | |

**User's choice:** CSS class toggle for price flash animation

---

## Watchlist style

| Option | Description | Selected |
|--------|-------------|----------|
| Compact table rows | Bloomberg-style; each ticker is one dense horizontal row; all 10 visible without scrolling | ✓ |
| Card grid | Each ticker is a card, 2-3 columns; more whitespace, modern; requires scrolling for 10+ tickers | |

**User's choice:** Compact table rows

| Option | Description | Selected |
|--------|-------------|----------|
| Symbol \| Price \| Change% \| Sparkline | Standard financial table layout; left-aligned symbol, right-aligned price/change, sparkline on right edge | ✓ |
| Symbol \| Sparkline \| Price \| Change% | Sparkline in middle breaks scan pattern | |
| You decide | Let agent follow standard financial UI conventions | |

**User's choice:** Symbol | Price | Change% | Sparkline

| Option | Description | Selected |
|--------|-------------|----------|
| Left accent bar + subtle background | 2px vertical bar in `#ecad0a` on left edge + slightly lighter background; standard trading terminal selection | ✓ |
| Full row background change | Selected row gets a distinct background color; simple but loses accent-bar elegance | |
| You decide | Leave selection styling to the agent | |

**User's choice:** Left accent bar (2px `#ecad0a`) + subtle background highlight

---

## Claude's Discretion

- Project scaffold method (create-next-app flags, ESLint setup, TypeScript strictness)
- SWR vs plain fetch for REST endpoints
- Internal folder structure within `frontend/src/`
- `next.config.ts` specifics beyond `output: 'export'`
- Font choice (system stack vs monospace)
- Tailwind plugin/preset choices (custom colors are locked)

## Deferred Ideas

None — discussion stayed within Phase 3 scope.
