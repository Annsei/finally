"""Strategy backtest engine for FinAlly (M5 — deterministic, stateless compute).

Simulates a buy-entry trigger strategy against synthetic per-minute GBM
history and reports equity/baseline curves, a trade log, and summary stats
(contract: planning/M5_BACKTEST_CONTRACT.md). Nothing here reads or writes
the database or the live market loop — given the same config, seed, and
end_time the result is bit-identical, so chat-run and route-run backtests
are directly comparable.

Provides:
- ``normalize_backtest_config(price_cache, *, ...)`` — shared validation and
  normalization for the POST /api/backtest route (failures map to HTTP 400)
  and the chat auto-execution pipeline (failures become status='failed'
  outcomes). Returns ``{"status": "ok", "config": {...}}`` or
  ``{"status": "failed", "ticker": T, "error": msg}`` and never raises on
  bad input. Resolves the anchor price — live cache quote first, then
  SEED_PRICES — and draws a random seed when none is given (always echoed
  back for reproducibility).
- ``run_backtest(config, *, commission_bps, end_time)`` — run the engine on
  a normalized config and return the full response payload (config echo,
  stats, downsampled curves, trades, runs_summary).

Engine semantics (contract §2):
- ``days`` sessions x 390 one-minute bars; per-bar GBM with the ticker's
  TICKER_PARAMS (DEFAULT_PARAMS fallback) via ``numpy.random.default_rng``.
  open = previous close (day 0 opens at the anchor price); high/low widen
  the bar body by a small non-negative noise draw. Day d's prev_close
  reference is day d-1's final close (the anchor for day 0).
- Per-bar order: (1) open position -> conservative intrabar exits, stop-loss
  BEFORE take-profit (a bar that touches both counts as a stop); (2) flat
  and not yet fired today -> evaluate the trigger on the bar close with the
  rules engine's semantics (day_change_pct vs the current day's prev_close);
  (3) mark equity = cash + qty x close.
- The trigger re-arms daily (max one fire per day) and fires only when flat
  (no pyramiding). ``fires`` counts executed entries; an entry rejected for
  insufficient cash consumes the day's fire and lands in
  ``rejections.insufficient_cash`` instead.
- Fills pay the ticker's half spread (``spread_bps_for``) on both legs plus
  ``commission_bps`` of notional per leg; round-trip pnl is net of both. Any
  position still open at the final bar closes there (reason 'horizon_end')
  and the equity curve lands on the realized final cash.
- Baseline: frictionless buy & hold of the same $10,000 from the first bar
  close (reference only).
- runs > 1: consecutive seeds ``seed .. seed+runs-1``. The representative
  run (stats/curves/trades) has the lower-middle-median ``total_return_pct``;
  ``runs_summary`` aggregates all runs with ``numpy.percentile`` (so
  p05 <= median <= p95 by construction). runs == 1 -> runs_summary is None.
"""

from __future__ import annotations

import math
import random
import time

import numpy as np

from app.market.cache import PriceCache
from app.market.seed_prices import DEFAULT_PARAMS, SEED_PRICES, TICKER_PARAMS
from app.market.simulator import GBMSimulator, spread_bps_for
from app.routes.rules import TRIGGER_TYPES

STARTING_CASH = 10_000.0
BARS_PER_DAY = 390  # 6.5 trading hours of one-minute bars
BAR_SECONDS = 60
DAY_SECONDS = 86_400
MAX_CURVE_POINTS = 400

DEFAULT_DAYS = 30
MIN_DAYS, MAX_DAYS = 5, 120
DEFAULT_RUNS = 1
MIN_RUNS, MAX_RUNS = 1, 50

# One minute as a fraction of a trading year — same convention as the live
# simulator's 500ms tick dt.
BAR_DT = BAR_SECONDS / GBMSimulator.TRADING_SECONDS_PER_YEAR


def normalize_backtest_config(
    price_cache: PriceCache,
    *,
    ticker: str,
    trigger_type: str,
    threshold: float,
    quantity: float,
    side: str | None = "buy",
    take_profit_pct: float | None = None,
    stop_loss_pct: float | None = None,
    days: int | None = None,
    runs: int | None = None,
    seed: int | None = None,
) -> dict:
    """Validate and normalize raw backtest fields (contract §1).

    The single source of truth for the POST /api/backtest route and the chat
    auto-execution pipeline, so both report identical error messages. Never
    raises on bad input.

    Args:
        price_cache: Live price cache — preferred anchor price source; tickers
            without a cached quote fall back to SEED_PRICES.
        ticker: Ticker symbol (normalized with .strip().upper() internally).
        trigger_type: One of TRIGGER_TYPES (normalized to lowercase).
        threshold: Trigger threshold. Must be > 0 for price_* triggers
            (dollar prices); day_change_pct_* thresholds may be negative.
        quantity: Shares bought per fire (fractional ok, must be > 0).
        side: Optional, defaults to "buy" — the only supported side.
        take_profit_pct: Optional exit, percent above entry (> 0 when given).
        stop_loss_pct: Optional exit, percent below entry (> 0 when given).
        days: Sessions to simulate (default 30, range 5-120).
        runs: Monte Carlo re-runs with consecutive seeds (default 1, 1-50).
        seed: RNG seed; omitted draws a random one (always echoed back).

    Returns:
        ``{"status": "ok", "config": {...}}`` with the normalized config
        (including the resolved ``anchor_price``), or
        ``{"status": "failed", "ticker": T, "error": msg}``.
    """
    ticker = ticker.strip().upper()
    side = (side or "buy").strip().lower()
    trigger_type = trigger_type.strip().lower()

    def failed(error: str) -> dict:
        return {"status": "failed", "ticker": ticker, "error": error}

    # Anchor price: live cache quote first, then the simulator's seed price.
    anchor_price = price_cache.get_price(ticker)
    if anchor_price is None:
        anchor_price = SEED_PRICES.get(ticker)
    if anchor_price is None:
        return failed("Ticker not found")
    if side != "buy":
        return failed(
            "Backtest supports buy-entry strategies only — model exits with "
            "take_profit_pct/stop_loss_pct"
        )
    if quantity <= 0:
        return failed("Quantity must be greater than 0")
    if trigger_type not in TRIGGER_TYPES:
        return failed(
            "trigger_type must be one of 'price_above', 'price_below', "
            "'day_change_pct_above', 'day_change_pct_below'"
        )
    if trigger_type in {"price_above", "price_below"} and threshold <= 0:
        return failed("Threshold must be greater than 0 for price triggers")
    if take_profit_pct is not None and take_profit_pct <= 0:
        return failed("take_profit_pct must be greater than 0")
    if stop_loss_pct is not None and stop_loss_pct <= 0:
        return failed("stop_loss_pct must be greater than 0")

    days = DEFAULT_DAYS if days is None else int(days)
    if not MIN_DAYS <= days <= MAX_DAYS:
        return failed(f"days must be between {MIN_DAYS} and {MAX_DAYS}")
    runs = DEFAULT_RUNS if runs is None else int(runs)
    if not MIN_RUNS <= runs <= MAX_RUNS:
        return failed(f"runs must be between {MIN_RUNS} and {MAX_RUNS}")
    # numpy's default_rng requires a non-negative seed.
    seed = random.randint(0, 2**31 - 1) if seed is None else int(seed)
    if seed < 0:
        return failed("seed must be a non-negative integer")

    return {
        "status": "ok",
        "config": {
            "ticker": ticker,
            "trigger_type": trigger_type,
            "threshold": float(threshold),
            "side": side,
            "quantity": float(quantity),
            "take_profit_pct": float(take_profit_pct) if take_profit_pct is not None else None,
            "stop_loss_pct": float(stop_loss_pct) if stop_loss_pct is not None else None,
            "days": days,
            "runs": runs,
            "seed": seed,
            "anchor_price": float(anchor_price),
        },
    }


def _generate_bars(
    ticker: str, anchor_price: float, days: int, seed: int, end_time: float
) -> dict:
    """Synthetic per-minute GBM history: ``days`` x 390 bars (contract §2).

    Returns numpy arrays ``times``/``opens``/``highs``/``lows``/``closes``
    plus ``prev_closes`` (one reference close per day for day_change_pct
    triggers: the anchor for day 0, then each prior day's final close).
    Deterministic for a given (ticker, anchor, days, seed): the draw order is
    fixed — the close path first, then the high/low widening noise.
    """
    params = TICKER_PARAMS.get(ticker, DEFAULT_PARAMS)
    mu, sigma = params["mu"], params["sigma"]
    n = days * BARS_PER_DAY
    rng = np.random.default_rng(seed)

    steps = (mu - 0.5 * sigma**2) * BAR_DT + sigma * math.sqrt(BAR_DT) * rng.standard_normal(n)
    closes = anchor_price * np.exp(np.cumsum(steps))
    opens = np.concatenate(([anchor_price], closes[:-1]))

    # Wicks: widen the bar body by a small non-negative draw (about half a
    # per-bar sigma on average) so intrabar exits can fill between closes.
    wick = 0.5 * sigma * math.sqrt(BAR_DT)
    highs = np.maximum(opens, closes) * (1.0 + np.abs(rng.standard_normal(n)) * wick)
    lows = np.minimum(opens, closes) * (1.0 - np.abs(rng.standard_normal(n)) * wick)

    # Bar i of day d sits at end_time - (days - d)*86400 + i*60, floored to
    # int — strictly ascending, grouped into realistic sessions on chart axes.
    day_idx = np.repeat(np.arange(days), BARS_PER_DAY)
    bar_idx = np.tile(np.arange(BARS_PER_DAY), days)
    times = (int(end_time) - (days - day_idx) * DAY_SECONDS + bar_idx * BAR_SECONDS).astype(
        np.int64
    )

    prev_closes = [float(anchor_price)] + [
        float(closes[d * BARS_PER_DAY - 1]) for d in range(1, days)
    ]
    return {
        "times": times,
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "closes": closes,
        "prev_closes": prev_closes,
    }


def _day_change_pct(close: float, day_prev_close: float) -> float:
    """Day change percent vs the current day's reference close.

    Rounded to 4dp to mirror ``PriceUpdate.day_change_percent`` — identical
    boundary behavior to the live rules engine.
    """
    if day_prev_close <= 0:
        return 0.0
    return round((close - day_prev_close) / day_prev_close * 100, 4)


def _trigger_fires(
    close: float, day_prev_close: float, trigger_type: str, threshold: float
) -> bool:
    """Rules-engine trigger semantics (``rules._rule_triggered``) on a bar close."""
    if trigger_type == "price_above":
        return close >= threshold
    if trigger_type == "price_below":
        return close <= threshold
    if trigger_type == "day_change_pct_above":
        return _day_change_pct(close, day_prev_close) >= threshold
    if trigger_type == "day_change_pct_below":
        return _day_change_pct(close, day_prev_close) <= threshold
    return False  # Unknown trigger_type (bad data) — never fires.


def _simulate(config: dict, seed: int, commission_bps: float, end_time: float) -> dict:
    """One full account simulation for a single seed (contract §2).

    Returns the ``stats`` block per contract §3 plus raw per-bar series
    (``times``/``equity``/``baseline`` — point formatting and downsampling
    happen in ``run_backtest``) and the trade log.
    """
    ticker = config["ticker"]
    quantity = config["quantity"]
    trigger_type = config["trigger_type"]
    threshold = config["threshold"]
    take_profit_pct = config["take_profit_pct"]
    stop_loss_pct = config["stop_loss_pct"]

    bars = _generate_bars(ticker, config["anchor_price"], config["days"], seed, end_time)
    times = bars["times"]
    closes = bars["closes"]
    n = len(closes)
    first_close = float(closes[0])

    half_spread = spread_bps_for(ticker) / 2.0 / 10_000.0
    commission_rate = commission_bps / 10_000.0

    cash = STARTING_CASH
    qty = 0.0
    entry_cost = 0.0  # Full cost of the open position (notional + commission)
    tp_price: float | None = None
    sl_price: float | None = None
    fired_today = False
    day_prev_close = float(config["anchor_price"])

    fires = 0
    insufficient_cash = 0
    commission_paid = 0.0
    trades: list[dict] = []
    round_trip_pnls: list[float] = []
    equity: list[float] = []
    baseline: list[float] = []

    def sell(level: float, bar_time: int, reason: str) -> None:
        """Close the open position at ``level`` with sell-side fill math."""
        nonlocal cash, qty, commission_paid
        sell_px = level * (1.0 - half_spread)
        proceeds = qty * sell_px
        commission = proceeds * commission_rate
        cash += proceeds - commission
        commission_paid += commission
        # Round-trip pnl is net of the spread and both legs' commissions.
        pnl = (proceeds - commission) - entry_cost
        round_trip_pnls.append(pnl)
        trades.append(
            {
                "time": bar_time,
                "side": "sell",
                "price": round(sell_px, 2),
                "quantity": qty,
                "reason": reason,
                "pnl": round(pnl, 2),
            }
        )
        qty = 0.0

    for g in range(n):
        if g % BARS_PER_DAY == 0:
            fired_today = False  # Re-arm daily (max one fire per day)
            day_prev_close = bars["prev_closes"][g // BARS_PER_DAY]
        close = float(closes[g])
        bar_time = int(times[g])

        # 1) Intrabar exits, stop-loss first (a same-bar double hit is a stop).
        if qty > 0:
            if sl_price is not None and float(bars["lows"][g]) <= sl_price:
                sell(sl_price, bar_time, "stop_loss")
            elif tp_price is not None and float(bars["highs"][g]) >= tp_price:
                sell(tp_price, bar_time, "take_profit")

        # 2) Flat and not yet fired today -> evaluate the trigger on the close.
        if (
            qty == 0.0
            and not fired_today
            and _trigger_fires(close, day_prev_close, trigger_type, threshold)
        ):
            fired_today = True  # Consumed even when the buy is rejected
            buy_px = close * (1.0 + half_spread)
            cost = quantity * buy_px
            commission = cost * commission_rate
            if cash < cost + commission:
                insufficient_cash += 1
            else:
                cash -= cost + commission
                commission_paid += commission
                qty = quantity
                entry_cost = cost + commission
                tp_price = buy_px * (1.0 + take_profit_pct / 100.0) if take_profit_pct else None
                sl_price = buy_px * (1.0 - stop_loss_pct / 100.0) if stop_loss_pct else None
                fires += 1
                trades.append(
                    {
                        "time": bar_time,
                        "side": "buy",
                        "price": round(buy_px, 2),
                        "quantity": quantity,
                        "reason": "trigger",
                        "pnl": None,
                    }
                )

        # 3) Mark to market.
        equity.append(cash + qty * close)
        baseline.append(STARTING_CASH * close / first_close)

    # Horizon end: close any open position at the final bar close and land
    # the equity curve on the realized final cash (sell friction included).
    if qty > 0:
        sell(float(closes[-1]), int(times[-1]), "horizon_end")
        equity[-1] = cash

    final_equity = cash
    eq = np.asarray(equity)
    peaks = np.maximum.accumulate(eq)
    max_drawdown_pct = float(np.max((peaks - eq) / peaks)) * 100.0

    wins = [p for p in round_trip_pnls if p > 0]
    losses = [p for p in round_trip_pnls if p < 0]
    round_trips = len(round_trip_pnls)
    gross_losses = -sum(losses)

    stats = {
        "total_return_pct": round((final_equity - STARTING_CASH) / STARTING_CASH * 100.0, 2),
        "buy_hold_return_pct": round((float(closes[-1]) / first_close - 1.0) * 100.0, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "final_equity": round(final_equity, 2),
        "fires": fires,
        "round_trips": round_trips,
        "win_rate": round(len(wins) / round_trips, 2) if round_trips else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "profit_factor": round(sum(wins) / gross_losses, 2) if gross_losses > 0 else None,
        "commission_paid": round(commission_paid, 2),
        "rejections": {"insufficient_cash": insufficient_cash},
    }
    return {
        "stats": stats,
        "times": times,
        "equity": equity,
        "baseline": baseline,
        "trades": trades,
    }


def _downsample_indices(n: int, max_points: int = MAX_CURVE_POINTS) -> list[int]:
    """Evenly-strided sample of ``range(n)`` capped at ``max_points``.

    Always includes the last index (lightweight-charts needs the curve to end
    on the final bar); when appending it would exceed the cap it replaces the
    last strided index instead, so indices stay strictly ascending.
    """
    if n <= max_points:
        return list(range(n))
    stride = math.ceil(n / max_points)
    idxs = list(range(0, n, stride))
    if idxs[-1] != n - 1:
        if len(idxs) < max_points:
            idxs.append(n - 1)
        else:
            idxs[-1] = n - 1
    return idxs


def run_backtest(
    config: dict, *, commission_bps: float = 0.0, end_time: float | None = None
) -> dict:
    """Run a normalized backtest config and build the full response (contract §3).

    Args:
        config: A normalized config from ``normalize_backtest_config``.
        commission_bps: Commission in basis points of notional on each leg
            (main.py's startup value — injected, never read from the env here).
        end_time: Unix timestamp of the final bar (the route passes
            ``time.time()``); None uses the current time. Fixing it makes the
            whole payload — timestamps included — reproducible.

    Returns:
        ``{"config", "stats", "equity_curve", "baseline_curve", "trades",
        "runs_summary"}`` per contract §3; ``runs_summary`` is None when
        runs == 1. Curves are downsampled to <= 400 points, last point kept.
    """
    end = float(end_time) if end_time is not None else time.time()
    runs = config["runs"]
    base_seed = config["seed"]

    if runs == 1:
        representative = _simulate(config, base_seed, commission_bps, end)
        runs_summary = None
    else:
        all_stats = [
            _simulate(config, base_seed + i, commission_bps, end)["stats"] for i in range(runs)
        ]
        returns = [s["total_return_pct"] for s in all_stats]
        # Representative = lower-middle-median return; re-running its seed is
        # free of drift because _simulate is deterministic.
        order = sorted(range(runs), key=lambda i: returns[i])
        rep_offset = order[(runs - 1) // 2]
        representative = _simulate(config, base_seed + rep_offset, commission_bps, end)
        drawdowns = [s["max_drawdown_pct"] for s in all_stats]
        runs_summary = {
            "runs": runs,
            "median_return_pct": round(float(np.percentile(returns, 50)), 2),
            "p05_return_pct": round(float(np.percentile(returns, 5)), 2),
            "p95_return_pct": round(float(np.percentile(returns, 95)), 2),
            "positive_share": round(sum(1 for r in returns if r > 0) / runs, 2),
            "median_max_drawdown_pct": round(float(np.percentile(drawdowns, 50)), 2),
        }

    times = representative["times"]
    idxs = _downsample_indices(len(times))
    equity_curve = [
        {"time": int(times[i]), "value": round(representative["equity"][i], 2)} for i in idxs
    ]
    baseline_curve = [
        {"time": int(times[i]), "value": round(representative["baseline"][i], 2)} for i in idxs
    ]

    return {
        "config": {
            "ticker": config["ticker"],
            "trigger_type": config["trigger_type"],
            "threshold": config["threshold"],
            "side": config["side"],
            "quantity": config["quantity"],
            "take_profit_pct": config["take_profit_pct"],
            "stop_loss_pct": config["stop_loss_pct"],
            "days": config["days"],
            "runs": runs,
            "seed": base_seed,
            "commission_bps": commission_bps,
            "anchor_price": round(config["anchor_price"], 2),
        },
        "stats": representative["stats"],
        "equity_curve": equity_curve,
        "baseline_curve": baseline_curve,
        "trades": representative["trades"],
        "runs_summary": runs_summary,
    }
