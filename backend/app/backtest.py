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
- ``normalize_strategy_backtest_config(price_cache, *, ...)`` — the P2
  strategy sibling: validates a declarative entry condition group + exits +
  sizing (from explicit fields or a ``strategies`` table row) into a config
  marked ``source: "strategy"``. Same never-raises contract.
- ``run_backtest(config, *, commission_bps, end_time)`` — run the engine on
  a normalized config and return the full response payload (config echo,
  stats, downsampled curves, trades, runs_summary).

P2 (§4): the loop evaluates ONE entry path — a condition group via
``app.indicators`` (the same functions the live strategy engine calls).
Legacy trigger_type/threshold configs are adapted to an equivalent
single-condition group at the top of ``_simulate``; their numeric output
(bars, RNG order, fees, fills, curves, echo shape) is byte-for-byte
unchanged and pinned by tests/test_backtest_golden.py. Strategy configs
additionally get indicator fields (evaluated on the completed synthetic
bars, warm-up shortfall -> False), a trailing stop (priority stop_loss ->
trailing_stop -> take_profit -> max_holding_days, matching the live
engine), a synthetic-day holding limit, and cash_pct sizing (whole shares,
floored to board lots on CN).

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

import json
import math
import random
import time

import numpy as np

from app.indicators import (
    build_series_context,
    evaluate_group_at,
    validate_condition_group,
    validate_exits,
    validate_sizing,
)
from app.market.cache import PriceCache
from app.market.profiles import MarketProfile
from app.market.seed_prices import DEFAULT_PARAMS, SEED_PRICES, TICKER_PARAMS
from app.market.simulator import GBMSimulator, spread_bps_for
from app.market.universe import MarketUniverse
from app.mechanics import lot_size_error
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
    universe: MarketUniverse | None = None,
    profile: MarketProfile | None = None,
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
        universe: Optional market universe (CN-1). When provided, the anchor
            fallback and GBM params come from it (the resolved params ride
            the config as a ``"params"`` key); None keeps the US constants
            and the exact pre-CN-1 config shape.
        profile: Optional market profile (CN-2). Enforces the 整手 buy-lot
            check (buy-entry only, so the same zh message as §3); None/us is a
            no-op. Fee/T+1 semantics ride ``run_backtest``'s ``profile``.

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

    # Anchor price: live cache quote first, then the market's seed price.
    seeds = SEED_PRICES if universe is None else universe.seed_prices
    anchor_price = price_cache.get_price(ticker)
    if anchor_price is None:
        anchor_price = seeds.get(ticker)
    if anchor_price is None:
        return failed("Ticker not found")
    if side != "buy":
        return failed(
            "Backtest supports buy-entry strategies only — model exits with "
            "take_profit_pct/stop_loss_pct"
        )
    if quantity <= 0:
        return failed("Quantity must be greater than 0")
    # CN-2 §3/§7: a buy-entry backtest must size in whole board lots (整手).
    # No-op for us/None (lot_size <= 1). side is always "buy" here.
    lot_error = lot_size_error(profile, "buy", quantity)
    if lot_error is not None:
        return failed(lot_error)
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

    config = {
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
    }
    # Universe-injected runs resolve GBM params here (CN-1) so the engine
    # never needs the universe itself; universe=None keeps the legacy config
    # shape byte-for-byte and _generate_bars falls back to the US constants.
    if universe is not None:
        config["params"] = dict(
            universe.ticker_params.get(ticker, universe.default_params)
        )
    return {"status": "ok", "config": config}


def _load_json_field(value):
    """Parse a strategy-row JSON TEXT column; dicts pass through unchanged."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def normalize_strategy_backtest_config(
    price_cache: PriceCache,
    *,
    strategy_row=None,
    ticker: str | None = None,
    entry: dict | None = None,
    exits: dict | None = None,
    sizing: dict | None = None,
    days: int | None = None,
    runs: int | None = None,
    seed: int | None = None,
    universe: MarketUniverse | None = None,
    profile: MarketProfile | None = None,
) -> dict:
    """Validate and normalize a strategy-backtest config (P2 contract §4).

    The strategy sibling of :func:`normalize_backtest_config` — same
    contract (never raises; returns ``{"status": "ok", "config": {...}}`` or
    ``{"status": "failed", "ticker": T, "error": msg}``), but the strategy is
    a declarative entry condition group + exits object + sizing instead of a
    single trigger/threshold/quantity. The returned config carries
    ``"source": "strategy"`` — ``run_backtest`` selects the strategy
    evaluation path (and the strategy-shaped config echo) on that marker;
    legacy configs stay byte-for-byte on the old path.

    Args:
        price_cache: Live price cache — preferred anchor price source;
            tickers without a cached quote fall back to the seed prices.
        strategy_row: A ``strategies`` table row (sqlite3.Row or mapping)
            whose ``ticker``/``entry``/``exits``/``sizing`` columns supply
            the strategy config (entry/exits/sizing are stored JSON TEXT).
            When given, the individual keyword fields below are ignored.
        ticker: Ticker symbol (used when ``strategy_row`` is None).
        entry: Declarative condition group ``{"all"|"any": [COND, ...]}`` —
            validated against the ``FIELD_SPECS`` whitelist (contract §2).
        exits: Optional exits object ``{take_profit_pct?, stop_loss_pct?,
            trailing_stop_pct?, max_holding_days?}``; None means no exits
            (positions close at the horizon end only). The deploy-time
            "at least one exit" rule is the CRUD state machine's — a
            backtest may run without exits.
        sizing: ``{"mode": "fixed_qty", "qty" > 0}`` or ``{"mode":
            "cash_pct", "pct" 1..100}``. fixed_qty on a lot-sized profile
            (CN) must be whole board lots; cash_pct floors to whole lots at
            entry time inside the engine.
        days: Sessions to simulate (default 30, range 5-120).
        runs: Monte Carlo re-runs with consecutive seeds (default 1, 1-50).
        seed: RNG seed; omitted draws a random one (always echoed back).
        universe: Optional market universe (CN) — anchor fallback and GBM
            params source, exactly as in the legacy normalizer.
        profile: Optional market profile (CN) — enforces the 整手 buy-lot
            check on fixed_qty sizing here; fee/T+1 semantics ride
            ``run_backtest``'s ``profile``.

    Returns:
        ``{"status": "ok", "config": {ticker, entry, exits, sizing, days,
        runs, seed, anchor_price, source: "strategy"[, params]}}`` or
        ``{"status": "failed", "ticker": T, "error": msg}``.
    """
    if strategy_row is not None:
        ticker = strategy_row["ticker"]
        entry = _load_json_field(strategy_row["entry"])
        exits = _load_json_field(strategy_row["exits"])
        sizing = _load_json_field(strategy_row["sizing"])

    ticker = (ticker or "").strip().upper()

    def failed(error: str) -> dict:
        return {"status": "failed", "ticker": ticker, "error": error}

    # Anchor price: live cache quote first, then the market's seed price.
    seeds = SEED_PRICES if universe is None else universe.seed_prices
    anchor_price = price_cache.get_price(ticker)
    if anchor_price is None:
        anchor_price = seeds.get(ticker)
    if anchor_price is None:
        return failed("Ticker not found")

    error = validate_condition_group(entry)
    if error is not None:
        return failed(f"entry: {error}")
    error = validate_exits(exits)
    if error is not None:
        return failed(f"exits: {error}")
    error = validate_sizing(sizing)
    if error is not None:
        return failed(f"sizing: {error}")
    # CN §9: a fixed-quantity buy entry must size in whole board lots (整手) —
    # the same profile-aware check (and zh message) as the legacy normalizer.
    # cash_pct needs no upfront check: the engine floors to whole lots at
    # entry time.
    if sizing["mode"] == "fixed_qty":
        lot_error = lot_size_error(profile, "buy", float(sizing["qty"]))
        if lot_error is not None:
            return failed(lot_error)

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

    # Normalized copies: drop unset exit keys (engine reads .get), coerce
    # sizing numbers to float.
    exits = {} if exits is None else {k: v for k, v in exits.items() if v is not None}
    if sizing["mode"] == "fixed_qty":
        sizing = {"mode": "fixed_qty", "qty": float(sizing["qty"])}
    else:
        sizing = {"mode": "cash_pct", "pct": float(sizing["pct"])}

    config = {
        "ticker": ticker,
        "entry": entry,
        "exits": exits,
        "sizing": sizing,
        "days": days,
        "runs": runs,
        "seed": seed,
        "anchor_price": float(anchor_price),
        "source": "strategy",
    }
    # Universe-injected runs resolve GBM params exactly like the legacy
    # normalizer; None keeps the US constants inside _generate_bars.
    if universe is not None:
        config["params"] = dict(
            universe.ticker_params.get(ticker, universe.default_params)
        )
    return {"status": "ok", "config": config}


def _generate_bars(
    ticker: str,
    anchor_price: float,
    days: int,
    seed: int,
    end_time: float,
    params: dict[str, float] | None = None,
) -> dict:
    """Synthetic per-minute GBM history: ``days`` x 390 bars (contract §2).

    Returns numpy arrays ``times``/``opens``/``highs``/``lows``/``closes``
    plus ``prev_closes`` (one reference close per day for day_change_pct
    triggers: the anchor for day 0, then each prior day's final close).
    Deterministic for a given (ticker, anchor, days, seed): the draw order is
    fixed — the close path first, then the high/low widening noise.

    ``params`` overrides the GBM {"mu", "sigma"} lookup (CN-1: resolved from
    the injected universe at normalize time); None uses the US constants.
    """
    if params is None:
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
    """Rules-engine trigger semantics (``rules._rule_triggered``) on a bar close.

    P2: the simulation loop now routes ALL entries through the condition-group
    evaluator (legacy triggers ride :func:`_legacy_entry_group`); this function
    is kept as the executable statement of the legacy semantics the adapter
    must reproduce (tests assert the equivalence, goldens pin the output).
    """
    if trigger_type == "price_above":
        return close >= threshold
    if trigger_type == "price_below":
        return close <= threshold
    if trigger_type == "day_change_pct_above":
        return _day_change_pct(close, day_prev_close) >= threshold
    if trigger_type == "day_change_pct_below":
        return _day_change_pct(close, day_prev_close) <= threshold
    return False  # Unknown trigger_type (bad data) — never fires.


def _legacy_entry_group(trigger_type: str, threshold: float) -> dict | None:
    """Adapt a legacy trigger to an equivalent single-condition group (P2 §4).

    The condition fields' inclusive >=/<= semantics and the 4dp day-change
    rounding match :func:`_trigger_fires` exactly, so evaluation through the
    shared ``app.indicators`` evaluator is value-for-value identical (the
    golden-sample tests pin it byte-for-byte). Unknown trigger types return
    None — the loop treats that as "never fires", the legacy behavior.
    """
    if trigger_type in {"price_above", "price_below"}:
        op = "above" if trigger_type == "price_above" else "below"
        return {"all": [{"field": "price", "op": op, "value": threshold}]}
    if trigger_type in {"day_change_pct_above", "day_change_pct_below"}:
        op = "above" if trigger_type == "day_change_pct_above" else "below"
        return {"all": [{"field": "day_change_pct", "op": op, "value": threshold}]}
    return None


def _simulate(
    config: dict,
    seed: int,
    commission_bps: float,
    end_time: float,
    starting_cash: float = STARTING_CASH,
    profile: MarketProfile | None = None,
) -> dict:
    """One full account simulation for a single seed (contract §2).

    Returns the ``stats`` block per contract §3 plus raw per-bar series
    (``times``/``equity``/``baseline`` — point formatting and downsampling
    happen in ``run_backtest``) and the trade log. ``starting_cash`` is the
    account's opening cash and the return-percent baseline (CN-1).

    ``profile`` (CN-2 §7) applies the §1 fee formula (commission floor + a
    sell-only stamp tax) and the T+1 rule (a position entered on day D cannot
    exit until day D+1 — TP/SL and the horizon-end close skip the entry day).
    None or a neutral (us) profile keeps the pre-CN-2 math value-for-value: the
    fee reduces to ``notional * commission_bps / 1e4`` (unrounded, as before)
    and exits are unrestricted.
    """
    ticker = config["ticker"]
    # P2 §4: one evaluation core, two config shapes. Strategy configs
    # (source == "strategy") carry a declarative entry group, an exits
    # object, and a sizing mode; legacy trigger configs are adapted to an
    # equivalent single-condition group so the loop below is shared. Every
    # numeric step (bar generation, RNG order, fees, fills, downsampling)
    # is untouched — the golden-sample tests pin the legacy output
    # byte-for-byte.
    if config.get("source") == "strategy":
        exits = config["exits"] or {}
        sizing = config["sizing"]
        entry_group = config["entry"]
        take_profit_pct = exits.get("take_profit_pct")
        stop_loss_pct = exits.get("stop_loss_pct")
        trailing_stop_pct = exits.get("trailing_stop_pct")
        max_holding_days = exits.get("max_holding_days")
        fixed_qty = float(sizing["qty"]) if sizing["mode"] == "fixed_qty" else None
        cash_pct = float(sizing["pct"]) if sizing["mode"] == "cash_pct" else None
    else:
        entry_group = _legacy_entry_group(config["trigger_type"], config["threshold"])
        take_profit_pct = config["take_profit_pct"]
        stop_loss_pct = config["stop_loss_pct"]
        trailing_stop_pct = None
        max_holding_days = None
        fixed_qty = config["quantity"]  # As-is: crafted int configs stay int
        cash_pct = None

    # T+1 exit deferral (CN-2 §7): active with a positive t_plus. Synthetic days
    # are real day boundaries here, so no session clock is consulted.
    t1_active = profile is not None and profile.t_plus > 0
    # cash_pct sizing floors to whole board lots on lot-sized profiles (CN).
    lot_size = profile.lot_size if profile is not None else 1
    # Per-fill fee — the §1 formula WITHOUT compute_fee's cent rounding, so the
    # None/us path stays value-for-value identical to the legacy backtest
    # (which accumulated commission unrounded and rounded only in stats).
    min_commission = profile.min_commission if profile is not None else 0.0
    stamp_bps = profile.stamp_tax_bps_sell if profile is not None else 0.0

    def _fee(notional: float, trade_side: str) -> float:
        fee = notional * commission_rate
        if fee < min_commission:
            fee = min_commission
        if trade_side == "sell" and stamp_bps:
            fee += notional * stamp_bps / 10_000.0
        return fee

    # Pass params only when the config carries them (universe-injected runs,
    # CN-1) — legacy configs keep the exact original _generate_bars call so
    # tests that monkeypatch it with the old signature are unaffected.
    params = config.get("params")
    if params is not None:
        bars = _generate_bars(
            ticker, config["anchor_price"], config["days"], seed, end_time, params=params
        )
    else:
        bars = _generate_bars(ticker, config["anchor_price"], config["days"], seed, end_time)
    times = bars["times"]
    closes = bars["closes"]
    n = len(closes)
    first_close = float(closes[0])

    half_spread = spread_bps_for(ticker) / 2.0 / 10_000.0
    commission_rate = commission_bps / 10_000.0

    # Indicator series are precomputed ONCE per simulation with the same
    # app.indicators series functions the live engine's point indicators are
    # defined by (single source of truth). Legacy price/day_change groups
    # need no series — build_series_context returns {} without touching the
    # bars. At bar g the loop evaluates with idx = g - 1: bars strictly
    # before g are the COMPLETED minutes; bar g is "now", read via the quote
    # (its close + the day-change vs the current day's reference close).
    entry_ctx = (
        build_series_context(
            entry_group,
            {"closes": closes, "highs": bars["highs"], "lows": bars["lows"]},
        )
        if entry_group is not None
        else {}
    )

    cash = starting_cash
    qty = 0.0
    entry_cost = 0.0  # Full cost of the open position (notional + commission)
    tp_price: float | None = None
    sl_price: float | None = None
    high_water = 0.0  # Trailing-stop reference — reset to buy_px on entry
    fired_today = False
    entry_day = -1  # Day index the open position was entered on (T+1 gate)
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
        commission = _fee(proceeds, "sell")
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
        current_day = g // BARS_PER_DAY
        if g % BARS_PER_DAY == 0:
            fired_today = False  # Re-arm daily (max one fire per day)
            day_prev_close = bars["prev_closes"][current_day]
        close = float(closes[g])
        bar_time = int(times[g])

        # 1) Intrabar exits, priority stop_loss -> trailing_stop ->
        # take_profit -> max_holding_days (P2 §4 — the live engine's order;
        # a same-bar double hit is a stop). Legacy configs carry no
        # trailing/max-holding, so this reduces exactly to the original
        # SL-before-TP pair. CN-2 §7: under T+1 a position cannot exit on
        # its entry day — skip the exit checks while current_day ==
        # entry_day.
        if qty > 0:
            if not t1_active or current_day > entry_day:
                # Trailing stop level from the high-water mark of PRIOR bars
                # (seeded at the entry fill) — conservative: the current
                # bar's own high/low intrabar ordering is unknowable.
                trail_price = (
                    high_water * (1.0 - trailing_stop_pct / 100.0)
                    if trailing_stop_pct
                    else None
                )
                if sl_price is not None and float(bars["lows"][g]) <= sl_price:
                    sell(sl_price, bar_time, "stop_loss")
                elif trail_price is not None and float(bars["lows"][g]) <= trail_price:
                    sell(trail_price, bar_time, "trailing_stop")
                elif tp_price is not None and float(bars["highs"][g]) >= tp_price:
                    sell(tp_price, bar_time, "take_profit")
                elif (
                    max_holding_days is not None
                    and current_day - entry_day >= max_holding_days
                ):
                    # Synthetic-day holding limit: close at the first bar of
                    # the day the limit is reached, at the bar close.
                    sell(close, bar_time, "max_holding_days")
            # High-water rises with the bar high AFTER the exit checks and
            # never on the entry bar (entry fills at the close below; qty
            # was still 0 here). Tracks through T+1 entry-day bars too —
            # the live engine raises it every pass.
            if qty > 0 and trailing_stop_pct:
                bar_high = float(bars["highs"][g])
                if bar_high > high_water:
                    high_water = bar_high

        # 2) Flat and not yet fired today -> evaluate the entry condition
        # group on the bar close (legacy triggers ride the single-condition
        # adapter — rules-engine semantics preserved value-for-value).
        if (
            qty == 0.0
            and not fired_today
            and entry_group is not None
            and evaluate_group_at(
                entry_group,
                entry_ctx,
                g - 1,
                {
                    "price": close,
                    "day_change_percent": _day_change_pct(close, day_prev_close),
                },
            )
        ):
            fired_today = True  # Consumed even when the buy is rejected
            buy_px = close * (1.0 + half_spread)
            if cash_pct is not None:
                # cash_pct sizing (P2 §4): whole shares of the current cash
                # at the ask, floored to whole board lots on CN. A zero-
                # share result consumes the day's fire and counts as an
                # insufficient-cash rejection (the live engine's skip).
                entry_qty = float(math.floor(cash * cash_pct / 100.0 / buy_px))
                if lot_size > 1:
                    entry_qty = float(math.floor(entry_qty / lot_size) * lot_size)
            else:
                entry_qty = fixed_qty
            cost = entry_qty * buy_px
            commission = _fee(cost, "buy")
            if entry_qty <= 0 or cash < cost + commission:
                insufficient_cash += 1
            else:
                cash -= cost + commission
                commission_paid += commission
                qty = entry_qty
                entry_cost = cost + commission
                entry_day = current_day  # T+1: no exit before the next day
                tp_price = buy_px * (1.0 + take_profit_pct / 100.0) if take_profit_pct else None
                sl_price = buy_px * (1.0 - stop_loss_pct / 100.0) if stop_loss_pct else None
                high_water = buy_px  # Trailing reference = entry fill (live parity)
                fires += 1
                trades.append(
                    {
                        "time": bar_time,
                        "side": "buy",
                        "price": round(buy_px, 2),
                        "quantity": entry_qty,
                        "reason": "trigger",
                        "pnl": None,
                    }
                )

        # 3) Mark to market.
        equity.append(cash + qty * close)
        baseline.append(starting_cash * close / first_close)

    # Horizon end: close any open position at the final bar close and land
    # the equity curve on the realized final cash (sell friction included).
    # CN-2 §7: under T+1, a position entered on the final day cannot be sold
    # that day — it stays open and the curve ends marked-to-market. Whenever a
    # close does happen (or no position is held) equity[-1] already equals
    # cash, so this stays value-for-value identical for None/us.
    last_day = (n - 1) // BARS_PER_DAY
    if qty > 0 and (not t1_active or last_day > entry_day):
        sell(float(closes[-1]), int(times[-1]), "horizon_end")
        equity[-1] = cash

    final_equity = equity[-1]
    eq = np.asarray(equity)
    peaks = np.maximum.accumulate(eq)
    max_drawdown_pct = float(np.max((peaks - eq) / peaks)) * 100.0

    wins = [p for p in round_trip_pnls if p > 0]
    losses = [p for p in round_trip_pnls if p < 0]
    round_trips = len(round_trip_pnls)
    gross_losses = -sum(losses)

    stats = {
        "total_return_pct": round((final_equity - starting_cash) / starting_cash * 100.0, 2),
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
    config: dict,
    *,
    commission_bps: float = 0.0,
    end_time: float | None = None,
    starting_cash: float = STARTING_CASH,
    profile: MarketProfile | None = None,
) -> dict:
    """Run a normalized backtest config and build the full response (contract §3).

    Args:
        config: A normalized config from ``normalize_backtest_config``.
        commission_bps: Commission in basis points of notional on each leg
            (main.py's startup value — injected, never read from the env here).
        end_time: Unix timestamp of the final bar (the route passes
            ``time.time()``); None uses the current time. Fixing it makes the
            whole payload — timestamps included — reproducible.
        starting_cash: Account opening cash (CN-1: the active market
            profile's seed cash). The stats math is unchanged — return% is
            relative to this amount; the default keeps the US $10,000.
        profile: Active market profile (CN-2 §7) — applies the §1 fee formula
            and the T+1 exit deferral inside ``_simulate``. None/us keeps the
            pre-CN-2 math value-for-value.

    Returns:
        ``{"config", "stats", "equity_curve", "baseline_curve", "trades",
        "runs_summary"}`` per contract §3; ``runs_summary`` is None when
        runs == 1. Curves are downsampled to <= 400 points, last point kept.
    """
    end = float(end_time) if end_time is not None else time.time()
    runs = config["runs"]
    base_seed = config["seed"]

    if runs == 1:
        representative = _simulate(
            config, base_seed, commission_bps, end, starting_cash, profile
        )
        runs_summary = None
    else:
        all_stats = [
            _simulate(config, base_seed + i, commission_bps, end, starting_cash, profile)[
                "stats"
            ]
            for i in range(runs)
        ]
        returns = [s["total_return_pct"] for s in all_stats]
        # Representative = lower-middle-median return; re-running its seed is
        # free of drift because _simulate is deterministic.
        order = sorted(range(runs), key=lambda i: returns[i])
        rep_offset = order[(runs - 1) // 2]
        representative = _simulate(
            config, base_seed + rep_offset, commission_bps, end, starting_cash, profile
        )
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

    # Config echo: strategy configs (P2 §4) echo their own shape; legacy
    # requests keep the OLD shape byte-for-byte — entry/exits/sizing keys
    # never leak into a legacy response (golden-sample tests pin it).
    if config.get("source") == "strategy":
        config_echo = {
            "ticker": config["ticker"],
            "entry": config["entry"],
            "exits": config["exits"],
            "sizing": config["sizing"],
            "days": config["days"],
            "runs": runs,
            "seed": base_seed,
            "commission_bps": commission_bps,
            "anchor_price": round(config["anchor_price"], 2),
            "source": "strategy",
        }
    else:
        config_echo = {
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
        }
    return {
        "config": config_echo,
        "stats": representative["stats"],
        "equity_curve": equity_curve,
        "baseline_curve": baseline_curve,
        "trades": representative["trades"],
        "runs_summary": runs_summary,
    }
