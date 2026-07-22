# M5 — Strategy Backtester: API Contract (fixed upfront)

Inspired by tickflow-stock-panel's hand-rolled daily state machine
(`backtest/engine.py simulate_portfolio`): a dependency-free account
simulator with conservative intrabar risk exits and explicit rejection
counters. FinAlly's version closes the M2 loop: **AI proposes a rule →
backtest validates it against simulated history → user arms it live.**

Both workstreams (backend agent, frontend) build against THIS document.
Nothing here reads or writes the database — the endpoint is stateless
compute. No schema migration in M5.

---

## 1. Endpoint

`POST /api/backtest` — synchronous, stateless. Registered via factory
`create_backtest_router(price_cache, commission_bps)` (same injection
pattern as the other routers; `commission_bps` is main.py's startup value).

### Request

```json
{
  "ticker": "NVDA",
  "trigger_type": "day_change_pct_below",
  "threshold": -3.0,
  "side": "buy",
  "quantity": 5,
  "take_profit_pct": 5.0,
  "stop_loss_pct": 3.0,
  "days": 30,
  "runs": 1,
  "seed": 42
}
```

| Field | Rules |
|---|---|
| `ticker` | Required. Normalized strip+upper. Must have a live cache quote OR an entry in `SEED_PRICES` → else 400 "Ticker not found". |
| `trigger_type` | Required. Exactly the rules-engine set: `price_above` \| `price_below` \| `day_change_pct_above` \| `day_change_pct_below`. |
| `threshold` | Required. Must be > 0 for `price_*` triggers (same validation as rules). |
| `side` | Optional, default `"buy"`. MUST be `"buy"` — 400 `"Backtest supports buy-entry strategies only — model exits with take_profit_pct/stop_loss_pct"`. |
| `quantity` | Required, > 0 (fractional ok). Shares bought per fire. |
| `take_profit_pct` | Optional. > 0 when given. Exit when price rises this % above entry. |
| `stop_loss_pct` | Optional. > 0 when given. Exit when price falls this % below entry. |
| `days` | Optional int, default 30, range 5–120. |
| `runs` | Optional int, default 1, range 1–50. Monte Carlo re-runs with consecutive seeds. |
| `seed` | Optional int. Omitted → backend draws a random one; ALWAYS echoed in `config` for reproducibility. |

All validation failures → `400 {"error": "<message>"}`.

## 2. Engine semantics (`backend/app/backtest.py`)

- **Synthetic history**: `days` sessions × 390 one-minute bars (6.5h).
  Per-minute GBM using the ticker's `TICKER_PARAMS` (fallback
  `DEFAULT_PARAMS`), `numpy.random.default_rng(seed)`. dt = 60 /
  (252 × 6.5 × 3600) years.
- **Bars**: open = previous close; close = GBM step; high/low =
  max/min(open, close) widened by a small non-negative noise draw.
  `prev_close` chain: day 0 = anchor price (live cache price if present,
  else seed price); day d = day d−1's final close.
- **Timestamps**: day d's bar i = `end_time − (days − d)·86400 + i·60`
  (router passes `end_time = time.time()`), floored to int — strictly
  ascending, realistic day grouping on chart time axes.
- **Account**: starts with $10,000 cash, no position.
- **Per-bar order** (tickflow lesson — conservative intrabar exits):
  1. Position open → check exits intrabar, **stop-loss first**: if
     `low <= sl_price` exit at `sl_price`; elif `high >= tp_price` exit at
     `tp_price`. (Same-bar双杀按止损算 — conservative.)
  2. Flat AND rule not yet fired today → evaluate trigger on bar close
     (identical semantics to `rules._rule_triggered`, with
     `day_change_pct` computed vs the current day's prev_close). On
     trigger: buy `quantity` at `close × (1 + half_spread)` + commission.
     Insufficient cash → `rejections.insufficient_cash += 1` and the
     day's fire is consumed (mirrors live one-shot fire-on-failure).
  3. Mark equity = cash + qty × close.
- **Re-arm daily**: unlike live one-shot rules, the backtest re-arms the
  trigger each day (max 1 fire/day) — it answers "如果这条规则每天有效，
  历史表现如何". Fires only when flat (no pyramiding).
- **Fills**: `half_spread = spread_bps_for(ticker) / 2 / 10000` (reuse
  simulator helper). Buys at `px × (1 + half_spread)`, sells at
  `px × (1 − half_spread)`; commission = `notional × commission_bps / 10000`
  on both legs.
- **Horizon end**: any open position closes at the final bar close
  (sell-side fill math), reason `horizon_end` — counts as a round trip.
- **Baseline**: buy & hold — the same $10,000 fully invested at the first
  bar close, frictionless (reference only).
- **Curves**: both downsampled to ≤ 400 evenly-strided points, always
  keeping the final point. Times strictly ascending (lightweight-charts
  requirement).
- **runs > 1**: seeds `seed .. seed+runs−1`. Representative run = median
  `total_return_pct` (lower-middle for even N); `stats`/curves/`trades`
  come from it; `runs_summary` aggregates all runs. `runs = 1` →
  `runs_summary: null`.

## 3. Response (200)

```json
{
  "config": {
    "ticker": "NVDA", "trigger_type": "day_change_pct_below",
    "threshold": -3.0, "side": "buy", "quantity": 5,
    "take_profit_pct": 5.0, "stop_loss_pct": 3.0,
    "days": 30, "runs": 1, "seed": 42,
    "commission_bps": 0.0, "anchor_price": 812.44
  },
  "stats": {
    "total_return_pct": 4.31,
    "buy_hold_return_pct": 6.02,
    "max_drawdown_pct": 3.87,
    "final_equity": 10431.22,
    "fires": 6,
    "round_trips": 6,
    "win_rate": 0.67,
    "avg_win": 141.02,
    "avg_loss": -80.55,
    "profit_factor": 2.33,
    "commission_paid": 0.0,
    "rejections": {"insufficient_cash": 0}
  },
  "equity_curve":   [{"time": 1751000000, "value": 10000.0}],
  "baseline_curve": [{"time": 1751000000, "value": 10000.0}],
  "trades": [
    {"time": 1751003600, "side": "buy",  "price": 790.12, "quantity": 5, "reason": "trigger",     "pnl": null},
    {"time": 1751010000, "side": "sell", "price": 829.63, "quantity": 5, "reason": "take_profit", "pnl": 197.55}
  ],
  "runs_summary": null
}
```

- `win_rate` null when `round_trips` = 0; `avg_win`/`avg_loss` null when
  no wins/losses; `profit_factor` = gross wins / gross losses, null when
  gross losses = 0. `max_drawdown_pct` ≥ 0 (peak-to-trough on the
  strategy equity curve). Dollar/pct values rounded to 2dp.
- `trades[].reason` ∈ `trigger | take_profit | stop_loss | horizon_end`;
  buys carry `pnl: null`, sells carry the round trip's realized P&L.
- `runs_summary` when runs > 1:

```json
{
  "runs": 30,
  "median_return_pct": 3.1,
  "p05_return_pct": -6.2,
  "p95_return_pct": 14.8,
  "positive_share": 0.7,
  "median_max_drawdown_pct": 4.4
}
```

## 4. Chat integration (M2 pattern)

- `ChatResponse` gains `backtests: list[BacktestInstruction] = []`:
  `{ticker, trigger_type, threshold, quantity, take_profit_pct?,
  stop_loss_pct?, days?, runs?}` (no side — always buy-entry; None
  days/runs → defaults).
- Pipeline Step 6d (after rules): each instruction runs through a shared
  helper and yields an outcome — success
  `{"status": "completed", "ticker": T, "config": {...}, "stats": {...}}`
  (NO curves/trades — chat stores compact stats only) or failure
  `{"status": "failed", "ticker": T, "error": msg}`. Failures never abort
  the batch. Backtests are stateless — they join the response and the
  stored actions JSON but touch no tables.
- Stored/returned `actions` gains a `"backtests"` key ONLY when the turn
  contained instructions (LLM_MOCK default response stays byte-identical).
- `SYSTEM_PROMPT` gains a `'backtests'` bullet: describe the fields,
  buy-entry-only (exits via take_profit_pct/stop_loss_pct), and when to
  use it ("backtest", "回测", "how would X have performed").
- **LLM_MOCK branch**: when the user message contains `"backtest"`
  (case-insensitive) return a deterministic
  `ChatResponse(message="[MOCK] Backtest complete: NVDA dip-buy strategy tested over 20 simulated days.",
  backtests=[BacktestInstruction(ticker="NVDA",
  trigger_type="day_change_pct_below", threshold=-3, quantity=5,
  take_profit_pct=5, stop_loss_pct=3, days=20, runs=1)])`; otherwise the
  existing PYPL/AAPL mock unchanged.
- `POST /api/chat/` response payload gains `"backtests"` when present.

## 5. Frontend consumption (for reference)

- New Backtest tab in PortfolioTabs; form → `POST /api/backtest`; results:
  stat cards, equity-vs-baseline chart (BaselineSeries @10000 + LineSeries),
  trades list, runs-summary strip.
- RulesTable per-rule "test" button (buy rules only) prefills the form.
- ChatPanel renders `actions.backtests` outcomes as badges
  (`data-testid="backtest-badge-completed" / "backtest-badge-failed"`).

## 6. Division of labor

| Workstream | Owns |
|---|---|
| Backend agent | `app/backtest.py` engine, `app/routes/backtest.py`, main.py wiring, chat.py schema/prompt/mock/pipeline, pytest suites, ruff clean |
| Frontend (inline) | types, uiStore, BacktestPanel, PortfolioTabs, RulesTable, ChatPanel, jest, E2E spec |
