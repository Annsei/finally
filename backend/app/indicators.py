"""Indicator math and declarative strategy conditions for FinAlly (P2 §2).

Pure functions, no IO — the SINGLE source of truth for strategy condition
semantics. The live strategy engine (1-second ring buffer aggregated to
one-minute bars) and the backtest engine (synthetic one-minute bars) both
call these functions, so live and backtest read the market with the same
math by construction.

Provides three layers:

1. **Bar aggregation** — ``aggregate_minute_bars(bars_1s)`` groups 1-second
   samples into completed one-minute OHLCV bars (the still-forming newest
   minute is always dropped so values never jitter).
2. **Indicators** — ``sma`` / ``ema`` / ``rsi`` / ``window_high`` /
   ``window_low`` point functions plus their ``*_series`` forms. The point
   functions are defined as the last element of the series functions, so a
   backtest that precomputes a series once and indexes into it (performance)
   is provably identical to the live engine calling the point function.
   All return ``float`` or ``None`` when there is not enough data.
3. **Declarative conditions** — the ``FIELD_SPECS`` whitelist registry,
   ``validate_condition_group`` / ``validate_exits`` / ``validate_sizing``
   (return ``None`` or an English error message; callers map messages to
   HTTP 400 or failed outcomes), and the evaluators
   ``evaluate_condition_group(entry, bars_1m, quote_like)`` /
   ``build_series_context`` + ``evaluate_group_at`` (the two-step form the
   backtest uses to precompute series once per simulation).

Condition groups are declarative JSON — ``{"all": [COND, ...]}`` or
``{"any": [COND, ...]}`` (exactly one key, 1..5 conditions) where ``COND``
is ``{"field", "op": "above"|"below", "value"?, "params"?}``. Only fields
registered in ``FIELD_SPECS`` are legal; validation is strict (unknown
fields/ops/params/extra keys are all errors) because groups may be authored
by the LLM — nothing here ever executes generated code (contract §0).

Evaluation conventions:
- ``bars_1m`` is the COMPLETED minute-bar series — live passes the
  aggregated ring buffer (forming minute already dropped); the backtest, at
  bar ``g``, passes bars strictly before ``g`` (bar ``g`` is "now",
  evaluated at its close). Cross/MA/RSI/window fields read these completed
  bars; ``price`` / ``day_change_pct`` read the real-time quote.
- ``quote_like`` provides ``price`` and ``day_change_percent`` — either an
  object with those attributes (live ``PriceUpdate``) or a plain dict (the
  backtest's bar close + synthetic day change).
- Insufficient warm-up data makes a condition ``False`` — evaluation never
  raises (contract §0).
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Condition-group shape limits (contract §2)
# --------------------------------------------------------------------------

GROUP_KEYS = ("all", "any")
OPS = ("above", "below")
MAX_GROUP_CONDITIONS = 5
CONDITION_KEYS = {"field", "op", "value", "params"}

# Exit params (contract §2) — all optional here; "deploy requires at least
# one" is the strategy CRUD's state-machine rule, not a shape rule.
EXIT_KEYS = ("take_profit_pct", "stop_loss_pct", "trailing_stop_pct", "max_holding_days")
MAX_HOLDING_DAYS_MIN, MAX_HOLDING_DAYS_MAX = 1, 120

SIZING_MODES = ("fixed_qty", "cash_pct")


# --------------------------------------------------------------------------
# Bar aggregation
# --------------------------------------------------------------------------


def aggregate_minute_bars(bars_1s: Sequence[Mapping]) -> list[dict]:
    """Aggregate 1-second samples into COMPLETED one-minute OHLCV bars.

    Samples are grouped into 60-second buckets keyed by ``time`` floored to
    the whole minute. Each input item needs a numeric ``time`` plus either
    OHLC keys (``open``/``high``/``low``/``close``, optional ``volume``) or
    a plain ``price`` (tick samples — price stands in for all four legs).
    Items are processed in ascending time order (sorted here; already-sorted
    ring-buffer input makes the sort a no-op).

    The newest bucket is ALWAYS dropped: from 1-second samples alone there
    is no way to know the current minute has ended, and a still-forming bar
    would make indicator values jitter (contract §2). The backtest's
    synthetic one-minute bars never pass through here — they are completed
    minutes by construction and feed the evaluators directly.

    Returns:
        ``[{"time", "open", "high", "low", "close", "volume"}, ...]`` in
        ascending time order; empty when fewer than two buckets exist.
    """
    if not bars_1s:
        return []
    samples = sorted(bars_1s, key=lambda s: s["time"])
    buckets: dict[int, dict] = {}
    order: list[int] = []
    for s in samples:
        minute = int(s["time"]) // 60 * 60
        if "close" in s:
            o = s.get("open", s["close"])
            h = s.get("high", s["close"])
            lo = s.get("low", s["close"])
            c = s["close"]
        else:
            o = h = lo = c = s["price"]
        v = float(s.get("volume") or 0.0)
        bar = buckets.get(minute)
        if bar is None:
            buckets[minute] = {
                "time": minute,
                "open": float(o),
                "high": float(h),
                "low": float(lo),
                "close": float(c),
                "volume": v,
            }
            order.append(minute)
        else:
            bar["high"] = max(bar["high"], float(h))
            bar["low"] = min(bar["low"], float(lo))
            bar["close"] = float(c)
            bar["volume"] += v
    order.pop()  # ascending order — the last bucket is the forming minute
    return [buckets[m] for m in order]


# --------------------------------------------------------------------------
# Indicator series (each point function below is series[-1] by definition)
# --------------------------------------------------------------------------


def sma_series(closes: Sequence[float], n: int) -> list[float | None]:
    """Simple moving average over the trailing ``n`` closes, per index.

    ``out[i]`` is ``mean(closes[i-n+1 .. i])`` for ``i >= n-1``; ``None``
    while warming up (fewer than ``n`` closes seen).
    """
    m = len(closes)
    out: list[float | None] = [None] * m
    if n <= 0 or m < n:
        return out
    window_sum = 0.0
    for i in range(m):
        window_sum += float(closes[i])
        if i >= n:
            window_sum -= float(closes[i - n])
        if i >= n - 1:
            out[i] = window_sum / n
    return out


def ema_series(closes: Sequence[float], n: int) -> list[float | None]:
    """Exponential moving average, standard ``alpha = 2/(n+1)`` recursion.

    The first value (at index ``n-1``) is the SMA of the first ``n`` closes
    (the conventional seed); earlier indexes are ``None``.
    """
    m = len(closes)
    out: list[float | None] = [None] * m
    if n <= 0 or m < n:
        return out
    value = sum(float(c) for c in closes[:n]) / n
    out[n - 1] = value
    alpha = 2.0 / (n + 1.0)
    for i in range(n, m):
        value = alpha * float(closes[i]) + (1.0 - alpha) * value
        out[i] = value
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    """RSI from smoothed averages. Loss-free -> 100; fully flat -> 50."""
    if avg_loss <= 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def rsi_series(closes: Sequence[float], n: int = 14) -> list[float | None]:
    """Relative Strength Index with Wilder smoothing.

    The first value lands at index ``n`` (needs ``n`` close-to-close
    changes): seed averages are the simple means of the first ``n`` gains
    and losses, then Wilder's recursion
    ``avg = (prev_avg * (n-1) + current) / n`` takes over.
    """
    m = len(closes)
    out: list[float | None] = [None] * m
    if n <= 0 or m < n + 1:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, n + 1):
        change = float(closes[i]) - float(closes[i - 1])
        if change > 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / n
    avg_loss = losses / n
    out[n] = _rsi_value(avg_gain, avg_loss)
    for i in range(n + 1, m):
        change = float(closes[i]) - float(closes[i - 1])
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (n - 1) + gain) / n
        avg_loss = (avg_loss * (n - 1) + loss) / n
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def _rolling_extreme_series(
    values: Sequence[float], window: int, is_max: bool
) -> list[float | None]:
    """Rolling max/min over the trailing ``window`` values (monotonic deque)."""
    m = len(values)
    out: list[float | None] = [None] * m
    if window <= 0 or m < window:
        return out
    dq: deque[int] = deque()  # indices; values monotonic from the front
    for i in range(m):
        v = float(values[i])
        while dq and (
            (float(values[dq[-1]]) <= v) if is_max else (float(values[dq[-1]]) >= v)
        ):
            dq.pop()
        dq.append(i)
        if dq[0] <= i - window:
            dq.popleft()
        if i >= window - 1:
            out[i] = float(values[dq[0]])
    return out


def rolling_max_series(values: Sequence[float], window: int) -> list[float | None]:
    """Rolling maximum over the trailing ``window`` values, per index."""
    return _rolling_extreme_series(values, window, is_max=True)


def rolling_min_series(values: Sequence[float], window: int) -> list[float | None]:
    """Rolling minimum over the trailing ``window`` values, per index."""
    return _rolling_extreme_series(values, window, is_max=False)


# --------------------------------------------------------------------------
# Point indicators (the live-engine entry points)
# --------------------------------------------------------------------------


def sma(closes: Sequence[float], n: int) -> float | None:
    """SMA of the last ``n`` closes; ``None`` with fewer than ``n`` closes."""
    series = sma_series(closes, n)
    return series[-1] if series else None


def ema(closes: Sequence[float], n: int) -> float | None:
    """EMA (SMA-seeded, ``2/(n+1)``) as of the last close; ``None`` when short."""
    series = ema_series(closes, n)
    return series[-1] if series else None


def rsi(closes: Sequence[float], n: int = 14) -> float | None:
    """Wilder RSI as of the last close; ``None`` with fewer than ``n+1`` closes."""
    series = rsi_series(closes, n)
    return series[-1] if series else None


def window_high(bars: Sequence[Mapping], minutes: int) -> float | None:
    """Highest ``high`` across the last ``minutes`` completed minute bars."""
    series = rolling_max_series([b["high"] for b in bars], minutes)
    return series[-1] if series else None


def window_low(bars: Sequence[Mapping], minutes: int) -> float | None:
    """Lowest ``low`` across the last ``minutes`` completed minute bars."""
    series = rolling_min_series([b["low"] for b in bars], minutes)
    return series[-1] if series else None


# --------------------------------------------------------------------------
# Field registry
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ParamSpec:
    """One integer parameter: inclusive bounds; ``default=None`` -> required."""

    lo: int
    hi: int
    default: int | None = None


@dataclass(frozen=True)
class FieldSpec:
    """Declarative spec for one whitelisted condition field (contract §2).

    ``value_rule`` is one of:
    - ``"required"``          — numeric value required (any sign)
    - ``"required_positive"`` — numeric value required, > 0
    - ``"required_0_100"``    — numeric value required, 0..100
    - ``"optional_zero"``     — numeric value optional, defaults to 0
    - ``"forbidden"``         — the condition takes no value
    ``evaluator`` is ``f(op, value, params, ctx, idx, quote) -> bool`` where
    ``ctx``/``idx`` come from ``build_series_context`` (``idx`` = index of
    the last completed bar) and ``quote`` is the real-time quote_like.
    """

    params: dict[str, ParamSpec] = field(default_factory=dict)
    value_rule: str = "required"
    ops: tuple[str, ...] = OPS
    evaluator: Callable[..., bool] = None  # type: ignore[assignment]
    extra_check: Callable[[dict], str | None] | None = None


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_int_like(v: object) -> bool:
    return _is_number(v) and float(v).is_integer()


def _quote_num(quote: object, name: str) -> float | None:
    """Read a numeric quote field from a dict or an attribute object."""
    v = quote.get(name) if isinstance(quote, Mapping) else getattr(quote, name, None)
    if v is None or not isinstance(v, (int, float)) or isinstance(v, bool):
        return None
    return float(v)


def _cmp(op: str, lhs: float, rhs: float) -> bool:
    """Inclusive threshold compare — >= for 'above', <= for 'below' (rules parity)."""
    return lhs >= rhs if op == "above" else lhs <= rhs


def _series_at(ctx: Mapping, key: tuple, idx: int) -> float | None:
    series = ctx.get(key)
    if series is None or idx < 0 or idx >= len(series):
        return None
    return series[idx]


def _eval_price(op, value, params, ctx, idx, quote) -> bool:
    price = _quote_num(quote, "price")
    return price is not None and _cmp(op, price, value)


def _eval_day_change_pct(op, value, params, ctx, idx, quote) -> bool:
    day_change = _quote_num(quote, "day_change_percent")
    return day_change is not None and _cmp(op, day_change, value)


def _eval_ma(op, value, params, ctx, idx, quote) -> bool:
    price = _quote_num(quote, "price")
    level = _series_at(ctx, ("sma", params["period"]), idx)
    if price is None or level is None:
        return False
    if op == "above":
        return price >= level * (1.0 + value / 100.0)
    return price <= level * (1.0 - value / 100.0)


def _make_eval_cross(kind: str) -> Callable[..., bool]:
    """Cross detector on the completed-bar series ('sma' or 'ema').

    ``above`` = golden cross THIS minute (previous bar fast <= slow, current
    fast > slow); ``below`` = death cross (mirrored, strict).
    """

    def _eval_cross(op, value, params, ctx, idx, quote) -> bool:
        fast_now = _series_at(ctx, (kind, params["fast"]), idx)
        slow_now = _series_at(ctx, (kind, params["slow"]), idx)
        fast_prev = _series_at(ctx, (kind, params["fast"]), idx - 1)
        slow_prev = _series_at(ctx, (kind, params["slow"]), idx - 1)
        if None in (fast_now, slow_now, fast_prev, slow_prev):
            return False
        if op == "above":
            return fast_prev <= slow_prev and fast_now > slow_now
        return fast_prev >= slow_prev and fast_now < slow_now

    return _eval_cross


def _eval_rsi(op, value, params, ctx, idx, quote) -> bool:
    level = _series_at(ctx, ("rsi", params["period"]), idx)
    return level is not None and _cmp(op, level, value)


def _eval_window_high(op, value, params, ctx, idx, quote) -> bool:
    # op is always "above" (validated): breakout over the rolling high.
    price = _quote_num(quote, "price")
    level = _series_at(ctx, ("whigh", params["minutes"]), idx)
    return price is not None and level is not None and price >= level


def _eval_window_low(op, value, params, ctx, idx, quote) -> bool:
    # op is always "below" (validated): breakdown under the rolling low.
    price = _quote_num(quote, "price")
    level = _series_at(ctx, ("wlow", params["minutes"]), idx)
    return price is not None and level is not None and price <= level


def _eval_pullback_from_high_pct(op, value, params, ctx, idx, quote) -> bool:
    price = _quote_num(quote, "price")
    high = _series_at(ctx, ("whigh", params["minutes"]), idx)
    if price is None or high is None or high <= 0:
        return False
    pullback = (high - price) / high * 100.0
    return _cmp(op, pullback, value)


def _check_fast_slow(params: dict) -> str | None:
    if params["fast"] >= params["slow"]:
        return "param 'fast' must be less than 'slow'"
    return None


FIELD_SPECS: dict[str, FieldSpec] = {
    "price": FieldSpec(value_rule="required_positive", evaluator=_eval_price),
    "day_change_pct": FieldSpec(value_rule="required", evaluator=_eval_day_change_pct),
    "ma": FieldSpec(
        params={"period": ParamSpec(2, 120)},
        value_rule="optional_zero",
        evaluator=_eval_ma,
    ),
    "ma_cross": FieldSpec(
        params={"fast": ParamSpec(2, 120), "slow": ParamSpec(2, 120)},
        value_rule="forbidden",
        evaluator=_make_eval_cross("sma"),
        extra_check=_check_fast_slow,
    ),
    "ema_cross": FieldSpec(
        params={"fast": ParamSpec(2, 120), "slow": ParamSpec(2, 120)},
        value_rule="forbidden",
        evaluator=_make_eval_cross("ema"),
        extra_check=_check_fast_slow,
    ),
    "rsi": FieldSpec(
        params={"period": ParamSpec(2, 50, default=14)},
        value_rule="required_0_100",
        evaluator=_eval_rsi,
    ),
    "window_high": FieldSpec(
        params={"minutes": ParamSpec(5, 240)},
        value_rule="forbidden",
        ops=("above",),
        evaluator=_eval_window_high,
    ),
    "window_low": FieldSpec(
        params={"minutes": ParamSpec(5, 240)},
        value_rule="forbidden",
        ops=("below",),
        evaluator=_eval_window_low,
    ),
    "pullback_from_high_pct": FieldSpec(
        params={"minutes": ParamSpec(5, 240)},
        value_rule="required_positive",
        evaluator=_eval_pullback_from_high_pct,
    ),
}


def max_condition_warmup_bars() -> int:
    """Most completed minute bars any legal condition can require.

    Derived from the FIELD_SPECS parameter upper bounds so it can never
    drift from validation: window/pullback fields need ``minutes`` bars and
    ``ma`` needs ``period``, while cross fields also read the previous bar
    (``slow + 1``) and RSI's first value needs ``period + 1`` closes.
    """
    most = 0
    for name, spec in FIELD_SPECS.items():
        extra = 1 if name in ("ma_cross", "ema_cross", "rsi") else 0
        for ps in spec.params.values():
            most = max(most, ps.hi + extra)
    return most


def required_history_seconds() -> int:
    """1-second ring-buffer capacity live strategy evaluation needs.

    Seconds of 1-second bar history the LIVE PriceCache must retain so a
    full ring buffer always aggregates to at least
    :func:`max_condition_warmup_bars` completed minute bars: one extra
    minute for the forming bucket ``aggregate_minute_bars`` always drops,
    and one more for the (possibly partial) oldest bucket the ring trim
    leaves behind. main.py sizes the app's PriceCache with this, so every
    config that passes validation is also satisfiable live — warm-up False
    stays transient and never becomes a permanent live/backtest divergence
    (contract §0: 实盘与回测口径一致).
    """
    return (max_condition_warmup_bars() + 2) * 60


# --------------------------------------------------------------------------
# Validation (returns None or an English error message; callers map to 400)
# --------------------------------------------------------------------------


def _validate_condition(cond: object) -> str | None:
    if not isinstance(cond, Mapping):
        return "condition must be an object"
    extra = set(cond) - CONDITION_KEYS
    if extra:
        return f"unknown condition keys: {sorted(extra)}"
    field_name = cond.get("field")
    spec = FIELD_SPECS.get(field_name) if isinstance(field_name, str) else None
    if spec is None:
        return f"unknown field {field_name!r}"
    op = cond.get("op")
    if op not in OPS:
        return "op must be 'above' or 'below'"
    if op not in spec.ops:
        return f"op '{op}' is not supported for field '{field_name}'"

    value = cond.get("value")
    if spec.value_rule == "forbidden":
        if "value" in cond and value is not None:
            return f"field '{field_name}' takes no value"
    elif value is None:
        if spec.value_rule != "optional_zero":
            return f"field '{field_name}' requires a numeric value"
    elif not _is_number(value):
        return "value must be a number"
    elif spec.value_rule == "required_positive" and value <= 0:
        return f"value must be greater than 0 for field '{field_name}'"
    elif spec.value_rule == "required_0_100" and not 0 <= value <= 100:
        return f"value must be between 0 and 100 for field '{field_name}'"

    raw_params = cond.get("params")
    if raw_params is None:
        raw_params = {}
    if not isinstance(raw_params, Mapping):
        return "params must be an object"
    extra = set(raw_params) - set(spec.params)
    if extra:
        return f"unknown params for field '{field_name}': {sorted(extra)}"
    for name, ps in spec.params.items():
        if name not in raw_params:
            if ps.default is None:
                return f"param '{name}' is required for field '{field_name}'"
            continue
        v = raw_params[name]
        if not _is_int_like(v):
            return f"param '{name}' must be an integer"
        if not ps.lo <= int(v) <= ps.hi:
            return f"param '{name}' must be between {ps.lo} and {ps.hi}"
    if spec.extra_check is not None:
        return spec.extra_check(_condition_params(cond, spec))
    return None


def validate_condition_group(entry: object) -> str | None:
    """Validate a declarative condition group; None when valid.

    Strict whitelist validation (contract §0/§2): exactly one of 'all'/'any',
    1..5 conditions, registered fields only, per-field op/value/params rules,
    and no extra keys anywhere. Returns an English error message on the first
    violation — callers map it to HTTP 400 or a failed outcome.
    """
    if not isinstance(entry, Mapping):
        return "entry must be an object with exactly one of 'all' or 'any'"
    keys = set(entry)
    if len(keys) != 1 or not keys <= set(GROUP_KEYS):
        return "entry must have exactly one of 'all' or 'any'"
    mode = next(iter(keys))
    conds = entry[mode]
    if not isinstance(conds, list) or not 1 <= len(conds) <= MAX_GROUP_CONDITIONS:
        return f"'{mode}' must be a list of 1 to {MAX_GROUP_CONDITIONS} conditions"
    for i, cond in enumerate(conds):
        err = _validate_condition(cond)
        if err is not None:
            return f"condition {i + 1}: {err}"
    return None


def validate_exits(exits: object) -> str | None:
    """Validate an exits object; None when valid. All exit params optional.

    ``take_profit_pct`` / ``stop_loss_pct`` / ``trailing_stop_pct`` must be
    > 0 when given; ``max_holding_days`` an integer 1..120. "Deploy requires
    at least one exit" is the CRUD state machine's rule — use
    :func:`has_any_exit` there. ``None`` is accepted as an empty object.
    """
    if exits is None:
        return None
    if not isinstance(exits, Mapping):
        return "exits must be an object"
    extra = set(exits) - set(EXIT_KEYS)
    if extra:
        return f"unknown exit keys: {sorted(extra)}"
    for key in ("take_profit_pct", "stop_loss_pct", "trailing_stop_pct"):
        v = exits.get(key)
        if v is not None and (not _is_number(v) or v <= 0):
            return f"{key} must be greater than 0"
    v = exits.get("max_holding_days")
    if v is not None:
        if not _is_int_like(v):
            return "max_holding_days must be an integer"
        if not MAX_HOLDING_DAYS_MIN <= int(v) <= MAX_HOLDING_DAYS_MAX:
            return (
                f"max_holding_days must be between {MAX_HOLDING_DAYS_MIN} "
                f"and {MAX_HOLDING_DAYS_MAX}"
            )
    return None


def has_any_exit(exits: Mapping | None) -> bool:
    """True when at least one exit param is set (deploy gate, contract §2)."""
    return exits is not None and any(exits.get(k) is not None for k in EXIT_KEYS)


def validate_sizing(sizing: object) -> str | None:
    """Validate a sizing object; None when valid.

    ``{"mode": "fixed_qty", "qty": > 0}`` or ``{"mode": "cash_pct",
    "pct": 1..100}``. Board-lot (整手) rules for fixed_qty on CN are the
    caller's profile-aware check (``mechanics.lot_size_error``) — sizing
    shape is market-agnostic here.
    """
    if not isinstance(sizing, Mapping):
        return "sizing must be an object"
    mode = sizing.get("mode")
    if mode == "fixed_qty":
        extra = set(sizing) - {"mode", "qty"}
        if extra:
            return f"unknown sizing keys: {sorted(extra)}"
        qty = sizing.get("qty")
        if not _is_number(qty) or qty <= 0:
            return "sizing qty must be greater than 0"
    elif mode == "cash_pct":
        extra = set(sizing) - {"mode", "pct"}
        if extra:
            return f"unknown sizing keys: {sorted(extra)}"
        pct = sizing.get("pct")
        if not _is_number(pct) or not 1 <= pct <= 100:
            return "sizing pct must be between 1 and 100"
    else:
        return "sizing mode must be 'fixed_qty' or 'cash_pct'"
    return None


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


def _conditions(entry: Mapping) -> list:
    for key in GROUP_KEYS:
        if key in entry:
            conds = entry[key]
            return conds if isinstance(conds, list) else []
    return []


def _condition_params(cond: Mapping, spec: FieldSpec) -> dict[str, int]:
    """Resolved integer params (defaults applied). Assumes validated input."""
    raw = cond.get("params") or {}
    return {name: int(raw.get(name, ps.default)) for name, ps in spec.params.items()}


def _condition_value(cond: Mapping, spec: FieldSpec) -> float | None:
    if spec.value_rule == "forbidden":
        return None
    v = cond.get("value")
    return 0.0 if v is None else float(v)  # None only under optional_zero


def _extract_columns(bars_1m) -> tuple[list[float], list[float], list[float]]:
    """(closes, highs, lows) from a bar-dict list or a columns mapping.

    The columns form (``{"closes": seq, "highs": seq, "lows": seq}``) is the
    backtest fast path — its per-bar data already lives in arrays.
    """
    if isinstance(bars_1m, Mapping):
        return (
            [float(v) for v in bars_1m["closes"]],
            [float(v) for v in bars_1m["highs"]],
            [float(v) for v in bars_1m["lows"]],
        )
    closes = [float(b["close"]) for b in bars_1m]
    highs = [float(b["high"]) for b in bars_1m]
    lows = [float(b["low"]) for b in bars_1m]
    return closes, highs, lows


def _bars_len(bars_1m) -> int:
    return len(bars_1m["closes"]) if isinstance(bars_1m, Mapping) else len(bars_1m)


def build_series_context(entry: Mapping, bars_1m) -> dict:
    """Precompute every indicator series the entry's conditions read.

    Returns ``{(kind, n): series}`` keyed by indicator kind ('sma' | 'ema' |
    'rsi' | 'whigh' | 'wlow') and parameter. Entries whose conditions need
    no series (price/day_change_pct only) return ``{}`` without touching the
    bars at all. Computed with the same series functions the point
    indicators are defined by — a context evaluated at its last index is
    identical to calling the point functions on the same bars.
    """
    keys: set[tuple[str, int]] = set()
    for cond in _conditions(entry):
        field_name = cond.get("field") if isinstance(cond, Mapping) else None
        spec = FIELD_SPECS.get(field_name)
        if spec is None or not spec.params:
            continue
        params = _condition_params(cond, spec)
        if field_name == "ma":
            keys.add(("sma", params["period"]))
        elif field_name == "ma_cross":
            keys.add(("sma", params["fast"]))
            keys.add(("sma", params["slow"]))
        elif field_name == "ema_cross":
            keys.add(("ema", params["fast"]))
            keys.add(("ema", params["slow"]))
        elif field_name == "rsi":
            keys.add(("rsi", params["period"]))
        elif field_name == "window_high" or field_name == "pullback_from_high_pct":
            keys.add(("whigh", params["minutes"]))
        elif field_name == "window_low":
            keys.add(("wlow", params["minutes"]))
    if not keys:
        return {}
    closes, highs, lows = _extract_columns(bars_1m)
    ctx: dict[tuple[str, int], list[float | None]] = {}
    for kind, n in keys:
        if kind == "sma":
            ctx[(kind, n)] = sma_series(closes, n)
        elif kind == "ema":
            ctx[(kind, n)] = ema_series(closes, n)
        elif kind == "rsi":
            ctx[(kind, n)] = rsi_series(closes, n)
        elif kind == "whigh":
            ctx[(kind, n)] = rolling_max_series(highs, n)
        elif kind == "wlow":
            ctx[(kind, n)] = rolling_min_series(lows, n)
    return ctx


def _evaluate_condition_at(cond: Mapping, ctx: Mapping, idx: int, quote) -> bool:
    spec = FIELD_SPECS.get(cond.get("field")) if isinstance(cond, Mapping) else None
    if spec is None:
        return False
    op = cond.get("op")
    if op not in spec.ops:
        return False
    try:
        value = _condition_value(cond, spec)
        params = _condition_params(cond, spec)
        return bool(spec.evaluator(op, value, params, ctx, idx, quote))
    except (TypeError, ValueError, KeyError):
        return False  # Malformed data never raises out of evaluation


def evaluate_group_at(entry: Mapping, ctx: Mapping, idx: int, quote) -> bool:
    """Evaluate a validated group against a precomputed series context.

    ``idx`` is the index of the LAST COMPLETED bar inside the context's
    series (the backtest passes ``g - 1`` at bar ``g``; live passes
    ``len(bars) - 1``). ``idx < 0`` (no completed bars) makes every
    indicator condition False. Never raises on missing/short data.
    """
    conds = _conditions(entry)
    if not conds:
        return False
    results = (_evaluate_condition_at(cond, ctx, idx, quote) for cond in conds)
    return all(results) if "all" in entry else any(results)


def evaluate_condition_group(entry: Mapping, bars_1m, quote_like) -> bool:
    """Evaluate a validated condition group (contract §2).

    Args:
        entry: ``{"all": [...]}`` or ``{"any": [...]}`` — must already have
            passed :func:`validate_condition_group`.
        bars_1m: Completed one-minute bars — the live engine's aggregated
            ring buffer or a backtest's synthetic bars (list of bar dicts,
            or the ``{"closes", "highs", "lows"}`` columns form).
        quote_like: Real-time quote — anything exposing ``price`` and
            ``day_change_percent`` as attributes (``PriceUpdate``) or
            mapping keys (backtest bar close + synthetic day change).

    Returns:
        True when the group fires. Insufficient warm-up data (short or
        empty bars) evaluates to False — never raises.
    """
    ctx = build_series_context(entry, bars_1m)
    return evaluate_group_at(entry, ctx, _bars_len(bars_1m) - 1, quote_like)
