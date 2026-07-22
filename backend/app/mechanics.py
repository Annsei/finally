"""A-share trading mechanics for FinAlly (CN-2) — field-driven checks and fees.

Every helper takes an optional ``MarketProfile`` and derives its behavior
ONLY from the profile's field values (planning/CN2_MECHANICS_CONTRACT.md §0):

- ``profile=None`` reproduces the legacy (pre-CN-2) behavior exactly.
- ``US_PROFILE`` carries the neutral field values (lot_size=1, t_plus=0,
  min_commission=0, stamp_tax_bps_sell=0, locale en-US), so passing it is
  provably identical to passing None — tests/test_cn2_parity.py enforces it.
- ``CN_PROFILE`` activates board-lot buys (整手), the T+1 sell lock, the
  commission floor plus sell-side stamp tax, and Chinese error messages.

Error messages are locale-driven: ``profile.locale == 'zh-CN'`` selects the
Chinese wording fixed by the CN-2 contract; every other locale (and None)
keeps the existing English strings.
"""

from __future__ import annotations

from app.market.models import PriceUpdate
from app.market.profiles import MarketProfile
from app.market.session import SessionClock

# Closed-market rejection messages. The English string is the legacy M3.1
# contract (routes/portfolio.MARKET_CLOSED_ERROR equals it); the Chinese one
# is fixed by the CN-2 contract §5.
MARKET_CLOSED_EN = "Market closed"
MARKET_CLOSED_ZH = "休市中"

# Order price outside the daily price-limit band (CN-2 contract §4).
ORDER_BAND_ZH = "委托价超出当日涨跌停区间"
ORDER_BAND_EN = "Order price is outside today's price limit band"


def _is_zh(profile: MarketProfile | None) -> bool:
    """True when the profile selects Chinese error messages."""
    return profile is not None and profile.locale == "zh-CN"


def market_closed_message(profile: MarketProfile | None = None) -> str:
    """Closed-market rejection message for the profile's locale."""
    return MARKET_CLOSED_ZH if _is_zh(profile) else MARKET_CLOSED_EN


def lot_size_error(
    profile: MarketProfile | None, side: str, quantity: float
) -> str | None:
    """Board-lot validation for buys (CN-2 contract §3).

    Buys must be whole multiples of ``profile.lot_size``; sells of any
    positive quantity are always legal (odd lots may be closed in one order).
    Returns the localized error message, or None when the order is valid.

    ``lot_size <= 1`` (us/None) skips the check entirely — fractional-share
    buys remain legal, exactly the pre-CN-2 behavior.
    """
    if profile is None or side != "buy" or profile.lot_size <= 1:
        return None
    if quantity % profile.lot_size != 0:
        if _is_zh(profile):
            return f"A股买入须为 {profile.lot_size} 股的整数倍"
        return f"Buy quantity must be a multiple of {profile.lot_size} shares"
    return None


def compute_fee(
    notional: float,
    side: str,
    commission_bps: float,
    profile: MarketProfile | None = None,
) -> float:
    """Total fee for one fill, rounded to cents (CN-2 contract §1).

        commission = max(profile.min_commission, notional * bps / 10_000)
        stamp      = notional * profile.stamp_tax_bps_sell / 10_000  (sell only)
        fee        = commission + stamp

    ``profile=None`` reproduces the legacy commission math bit-for-bit
    (including the ``commission_bps == 0`` fast path returning exactly 0.0),
    and the us field values (min 0, stamp 0) reduce the formula to the same
    result — parity is enforced by tests.
    """
    if profile is None:
        return round(notional * commission_bps / 10000.0, 2) if commission_bps else 0.0
    commission = notional * commission_bps / 10000.0
    if commission < profile.min_commission:
        commission = profile.min_commission
    if side == "sell" and profile.stamp_tax_bps_sell:
        commission += notional * profile.stamp_tax_bps_sell / 10000.0
    return round(commission, 2)


def t1_applies(
    profile: MarketProfile | None, session_clock: SessionClock | None
) -> bool:
    """True when the T+1 buy lock / sell restriction is in force (contract §2).

    Requires ``profile.t_plus > 0`` AND a session clock that actually cycles:
    with a 24/7 clock there is no "next trading day", so locked shares would
    never unlock — T+1 is disabled outright. When no clock is passed (the
    background fill/rules loops), the profile decides alone; main.py wires
    those loops with a t_plus-neutralized profile in 24/7 mode so all paths
    agree.
    """
    if profile is None or profile.t_plus <= 0:
        return False
    if session_clock is not None and session_clock.always_open:
        return False
    return True


def t1_sell_error(profile: MarketProfile | None, sellable: float) -> str:
    """T+1 sell-rejection message with the currently sellable share count."""
    if _is_zh(profile):
        return f"T+1：今日买入股份下一交易日方可卖出（当前可卖 {sellable:g} 股）"
    return (
        "T+1: shares bought today can be sold from the next trading day "
        f"(sellable now: {sellable:g})"
    )


def order_band_error(
    profile: MarketProfile | None,
    quote: PriceUpdate | None,
    limit_price: float | None,
    stop_price: float | None,
) -> str | None:
    """Reject resting-order prices outside today's price-limit band (§4).

    Applies only when a profile is provided AND the quote carries board
    prices (``limit_up``/``limit_down`` set by the PriceCache funnel — the us
    profile has no ``price_limit_fn`` so its quotes never carry them). Market
    orders are never blocked here: they fill at the already-clamped price.
    Returns the localized error message, or None when the prices are in band.
    """
    if profile is None or quote is None:
        return None
    if quote.limit_up is None or quote.limit_down is None:
        return None
    for price in (limit_price, stop_price):
        if price is not None and not (quote.limit_down <= price <= quote.limit_up):
            return ORDER_BAND_ZH if _is_zh(profile) else ORDER_BAND_EN
    return None
