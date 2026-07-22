"""P2 fix pin — every FIELD_SPECS upper bound is satisfiable live (contract §0/§2).

The live strategy engine's ONLY bar source is the app PriceCache's 1-second
ring buffer aggregated to completed minute bars (strategy_engine.py →
``aggregate_minute_bars(price_cache.get_history(ticker))``). With the old
7200-slot default (~120 minutes) a legally-validated config such as
``window_high minutes=240`` backtested fine but could NEVER fire live — a
silent, permanent live/backtest divergence.

These tests pin the fix:

- ``required_history_seconds()`` is derived from the FIELD_SPECS parameter
  upper bounds (it can't drift from validation) and main.py sizes the app
  cache with it.
- A FULL ring buffer of that capacity aggregates to enough completed minute
  bars that the maximal legal parameters of EVERY registered field produce
  real (non-warm-up) indicator values — warm-up False is always transient.
"""

from __future__ import annotations

import pytest

from app.indicators import (
    FIELD_SPECS,
    aggregate_minute_bars,
    build_series_context,
    max_condition_warmup_bars,
    required_history_seconds,
    validate_condition_group,
)
from app.market.cache import DEFAULT_HISTORY_CAPACITY, PriceCache

# Deliberately NOT minute-aligned so the ring buffer's oldest bucket is a
# partial minute — the worst case the +2 minute slack must absorb.
BASE_TS = 1_700_000_000 - (1_700_000_000 % 60) + 37

PARAM_FIELDS = sorted(name for name, spec in FIELD_SPECS.items() if spec.params)


def _max_param_condition(field_name: str) -> dict:
    """A valid condition using the field's UPPER-BOUND parameters."""
    spec = FIELD_SPECS[field_name]
    params = {name: ps.hi for name, ps in spec.params.items()}
    if field_name in ("ma_cross", "ema_cross"):
        params["fast"] = spec.params["fast"].lo  # fast must stay < slow
    cond: dict = {"field": field_name, "op": spec.ops[0], "params": params}
    if spec.value_rule == "required_positive":
        cond["value"] = 1.0
    elif spec.value_rule == "required_0_100":
        cond["value"] = 50
    elif spec.value_rule in ("required", "optional_zero"):
        cond["value"] = 0
    return cond


def _fill_ring_buffer(capacity: int) -> list[dict]:
    """1-second ticks OVERfilling a ``capacity``-slot ring buffer (it wraps)."""
    cache = PriceCache(history_capacity=capacity)
    for i in range(capacity + 90):
        price = 100.0 + (i % 7) * 0.05  # gently varying — no event spam
        cache.update("PIN", price, timestamp=float(BASE_TS + i))
    bars = cache.get_history("PIN")
    assert len(bars) == capacity  # actually full
    return bars


@pytest.fixture(scope="module")
def full_buffer_bars_1m() -> list[dict]:
    return aggregate_minute_bars(_fill_ring_buffer(required_history_seconds()))


def test_capacity_derived_from_field_specs_upper_bounds():
    # window_high / window_low / pullback_from_high_pct minutes go to 240 —
    # the largest warm-up any legal condition can require.
    assert max_condition_warmup_bars() == 240
    # +1 forming minute (always dropped) +1 possibly-partial oldest bucket.
    assert required_history_seconds() == (240 + 2) * 60 == 14_520
    # The pre-fix default is the bug being pinned: it cannot hold the legal
    # maximum, so main.py must size the app cache explicitly.
    assert DEFAULT_HISTORY_CAPACITY < required_history_seconds()


def test_full_ring_buffer_yields_enough_completed_minute_bars(full_buffer_bars_1m):
    assert len(full_buffer_bars_1m) >= max_condition_warmup_bars()


@pytest.mark.parametrize("field_name", PARAM_FIELDS)
def test_field_upper_bound_satisfiable_from_full_ring_buffer(
    field_name, full_buffer_bars_1m
):
    """Max legal params for every field warm up on a full live ring buffer."""
    entry = {"all": [_max_param_condition(field_name)]}
    assert validate_condition_group(entry) is None

    ctx = build_series_context(entry, full_buffer_bars_1m)
    assert ctx  # every param field reads at least one indicator series
    idx = len(full_buffer_bars_1m) - 1
    # Cross detectors also read the previous bar (idx - 1).
    lookback = 2 if field_name in ("ma_cross", "ema_cross") else 1
    for series in ctx.values():
        for offset in range(lookback):
            assert series[idx - offset] is not None, (
                f"{field_name} upper-bound params still warming up on a full "
                "ring buffer — live can never satisfy a validated config"
            )


def test_old_default_capacity_never_warms_up_window_240():
    """Documents the fixed bug: a 7200-slot buffer stays warm-up-False forever."""
    bars_1m = aggregate_minute_bars(_fill_ring_buffer(DEFAULT_HISTORY_CAPACITY))
    entry = {"all": [_max_param_condition("window_high")]}
    ctx = build_series_context(entry, bars_1m)
    assert ctx[("whigh", 240)][-1] is None
