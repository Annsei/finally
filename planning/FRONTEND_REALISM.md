# FinAlly — Frontend Realism Roadmap

Goal: make FinAlly look and behave like a real trading platform. The core
insight from the 2026-07-05 audit: the gap is **data semantics**, not visuals.
The terminal aesthetic is already right; the numbers don't mean what they mean
on a real platform.

Batches are ordered by realism-gained ÷ effort. Each batch is one phase.

---

## Batch 1 — Data semantics (IN PROGRESS)

### 1.1 Day change vs previous close (the single most important item)
Today `change_percent` is tick-over-tick (±0.0x%, meaningless). Real platforms
quote change vs the previous session close.

**Contract** — `PriceUpdate.to_dict()` gains (all optional in frontend types,
always sent by backend):
- `prev_close` — previous session close. Simulator: the seed price captured at
  source start / ticker add. Massive: snapshot `prevDay.c`, fallback first price.
- `day_change` — `price − prev_close`
- `day_change_percent` — `(price − prev_close) / prev_close × 100`
- `day_high` / `day_low` — running session extremes.

Existing per-tick `change`/`change_percent`/`direction` stay (flash animation
uses them). `GET /api/watchlist` entries also gain `day_change_percent`.

**Frontend**: WatchlistRow shows day % with ▲▼ arrows and day-direction price
coloring plus a day-range bar (low—price—high); MainChart title shows live
price + colored day change.

### 1.2 Order ticket: cost preview + fill feedback
- TradeBar shows live estimated notional (`qty × price`), max-buyable quantity
  (cash ÷ price, clickable to fill), held quantity (clickable, for sells).
- Toast on fill: "Bought 5 AAPL @ $190.02", auto-dismiss.

### 1.3 Trade blotter
- New endpoint `GET /api/portfolio/trades?limit=50` → `{trades: [{id, ticker,
  side, quantity, price, executed_at}]}`, newest first, limit capped at 500.
- Frontend: Positions | Orders tabs in the positions area; Orders lists time,
  side, qty, price, notional; revalidates after every trade (manual or AI).

### 1.4 Direction affordances
▲▼ arrows and day-change coloring throughout the watchlist (done as part of 1.1).

---

## Batch 2 — Chart professionalization

- **2.1 History backfill**: backend keeps an in-memory ring buffer per ticker
  (e.g. last 2h of 1s aggregates); `GET /api/market/history?ticker=X`.
  Frontend `setData`s the backfill then splices the SSE stream (time axes are
  already real timestamps, so this is seamless).
- **2.2 Candlesticks + volume + timeframes**: simulator emits per-tick volume
  (lognormal); frontend aggregates ticks → 1s/5s/1m OHLC via lightweight-charts
  `CandlestickSeries` + `HistogramSeries` volume pane; timeframe button group;
  crosshair legend with OHLC readout.
- **2.3 Bid/ask spread**: simulator quotes a 1–5 bp spread; `PriceUpdate` gains
  bid/ask; buys fill at ask, sells at bid; TradeBar shows `Bid × Ask`.
- **2.4 P&L chart upgrade**: `BaselineSeries` anchored at $10,000 (green above,
  red below); Header splits Day P&L vs Total P&L (needs 1.1); range selector
  (1H / Today / All).

## Batch 3 — Depth and atmosphere

- **3.1 Market event news feed**: the simulator already generates random 2–5%
  "events" — surface them (SSE event type or endpoint) as a scrolling news
  ticker ("14:32 · NVDA surges +3.4%"); the AI chat can reference them.
- **3.2 Limit orders** (flagship, own phase): order-type selector, pending
  orders panel, cancel; backend orders table + fill loop on price cross + fill
  events.
- **3.3 Interaction polish**: keyboard (`/` focus ticker, B/S trade, ↑↓ watchlist
  navigation), ticker autocomplete with company names, resizable panels,
  status bar (market clock, SSE latency).

## Explicitly out of scope
- L2 order-book depth (no matching engine — fake data has no teaching value)
- Options chains, margin/shorting (upends portfolio math)
- WebSocket migration (SSE is sufficient and spec-mandated)
