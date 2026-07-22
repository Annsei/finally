"""Factory for creating market data sources."""

from __future__ import annotations

import logging
import math
import os

from .akshare_live import (
    AKSHARE_MAX_POLL_SECONDS,
    AKSHARE_MIN_POLL_SECONDS,
    DEFAULT_AKSHARE_POLL_SECONDS,
    AkshareLiveSource,
)
from .cache import PriceCache
from .interface import MarketDataSource
from .massive_client import MassiveDataSource
from .profiles import resolve_market_profile
from .session import SessionClock
from .simulator import SimulatorDataSource
from .universe import MarketUniverse

logger = logging.getLogger(__name__)

LIVE_SOURCE_ENV = "FINALLY_LIVE_SOURCE"
LIVE_SOURCE_CHOICES = ("auto", "simulator", "massive", "akshare", "replay")
# Source kinds that stream real market data — main.py forces the session
# clock into 24/7 mode for these (the simulator's open/close cycle makes no
# sense against live quotes). Deliberately EXCLUDES 'replay' (D3 §2): the
# replay source needs the accelerated session clock to roll its days.
REAL_DATA_SOURCES = frozenset({"massive", "akshare"})


def resolve_live_source() -> str:
    """Resolve FINALLY_LIVE_SOURCE (D2 §1) to a concrete source kind.

    Returns ``'simulator' | 'massive' | 'akshare'``. The default ``auto``
    keeps the pre-D2 selection byte-for-byte: MASSIVE_API_KEY set and
    non-empty → ``massive``, otherwise ``simulator`` — the simulator stays
    the product default and every real source is explicit opt-in. An
    unrecognized value raises ValueError (RuntimeSettings.validate style:
    explicit misconfiguration fails startup, never degrades silently).

    Read at startup by BOTH :func:`create_market_data_source` and main.py's
    session-clock builder, so source selection and the forced-24/7 clock
    can never disagree.
    """
    raw = os.environ.get(LIVE_SOURCE_ENV, "").strip().lower()
    choice = raw or "auto"
    if choice not in LIVE_SOURCE_CHOICES:
        allowed = ", ".join(LIVE_SOURCE_CHOICES)
        raise ValueError(f"{LIVE_SOURCE_ENV} must be one of: {allowed} (got {raw!r})")
    if choice == "auto":
        return "massive" if os.environ.get("MASSIVE_API_KEY", "").strip() else "simulator"
    return choice


def _read_akshare_poll_seconds() -> float:
    """Parse FINALLY_AKSHARE_POLL_SECONDS (D2 §1).

    Default 15; finite numeric values are clamped into [5, 120] (the 东财
    endpoint is a full-market snapshot — poll gently). Unparsable or
    non-finite values log a warning and use the default.
    """
    raw = os.environ.get("FINALLY_AKSHARE_POLL_SECONDS", "").strip()
    if not raw:
        return DEFAULT_AKSHARE_POLL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "Invalid FINALLY_AKSHARE_POLL_SECONDS=%r — using %.0fs",
            raw,
            DEFAULT_AKSHARE_POLL_SECONDS,
        )
        return DEFAULT_AKSHARE_POLL_SECONDS
    if not math.isfinite(value):
        logger.warning(
            "Invalid FINALLY_AKSHARE_POLL_SECONDS=%r — using %.0fs",
            raw,
            DEFAULT_AKSHARE_POLL_SECONDS,
        )
        return DEFAULT_AKSHARE_POLL_SECONDS
    return min(max(value, AKSHARE_MIN_POLL_SECONDS), AKSHARE_MAX_POLL_SECONDS)


def create_market_data_source(
    price_cache: PriceCache,
    session_clock: SessionClock | None = None,
    universe: MarketUniverse | None = None,
    db_path: str | None = None,
) -> MarketDataSource:
    """Create the appropriate market data source based on environment variables.

    Selection is driven by FINALLY_LIVE_SOURCE ∈ auto|simulator|massive|
    akshare|replay (D2 §1 / D3 §2):

    - ``auto`` (default, byte-identical to the pre-D2 behavior):
      MASSIVE_API_KEY set and non-empty → MassiveDataSource (real market
      data), otherwise → SimulatorDataSource (GBM simulation).
    - ``simulator`` / ``massive``: explicit choice; ``massive`` without a
      MASSIVE_API_KEY is an explicit misconfiguration and fails startup.
    - ``akshare``: real A-share spot quotes (AkshareLiveSource) — only legal
      with FINALLY_MARKET=cn; any other market profile fails startup.
    - ``replay``: historical daily-bar replay (ReplayDataSource, D3) —
      requires ``db_path`` (daily_bars lives in the app database); main.py
      validates/injects window coverage BEFORE creating the source.

    Returns an unstarted source. Caller must await source.start(tickers).

    Args:
        price_cache: Shared cache the source writes ticks into.
        session_clock: Optional session clock (M3.1). Only the simulator and
            the replay source use it (equities freeze while closed; replay
            days roll on reopen); the real sources stream real data and
            always run 24/7 — main.py forces a 24/7 clock whenever the
            resolved live source is massive or akshare.
        universe: Optional market universe (CN-1). Only the simulator uses it
            (seeds/params/correlations/asset classes); the replay source uses
            its default equity watchlist as the calendar base; the real
            sources stream real data. None keeps the US module-constant
            behavior.
        db_path: SQLite path backing the replay source's daily_bars reads
            (D3 §2). Only the replay branch reads it; main.py (the single
            call site) always passes it.
    """
    choice = resolve_live_source()

    if choice == "massive":
        api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                f"{LIVE_SOURCE_ENV}=massive requires MASSIVE_API_KEY to be set"
            )
        logger.info("Market data source: Massive API (real data)")
        return MassiveDataSource(api_key=api_key, price_cache=price_cache)
    elif choice == "akshare":
        market = resolve_market_profile().key
        if market != "cn":
            raise ValueError(
                f"{LIVE_SOURCE_ENV}=akshare supports only the CN market "
                "profile; set FINALLY_MARKET=cn (or pick another live source)"
            )
        poll_interval = _read_akshare_poll_seconds()
        logger.info(
            "Market data source: AKShare spot (real A-share data, %.1fs poll)",
            poll_interval,
        )
        return AkshareLiveSource(price_cache=price_cache, poll_interval=poll_interval)
    elif choice == "replay":
        if not db_path:
            raise ValueError(
                f"{LIVE_SOURCE_ENV}=replay requires a db_path (daily_bars "
                "lives in the application database — main.py passes it)"
            )
        from .replay_source import ReplayDataSource, read_replay_env

        profile = resolve_market_profile()
        replay_universe = universe if universe is not None else profile.universe
        config = read_replay_env()
        logger.info(
            "Market data source: Historical replay (%s, %.0fs/day, loop=%s)",
            f"{config.from_date}..{config.to_date}"
            if config.from_date is not None
            else "auto window",
            config.seconds_per_day,
            config.loop,
        )
        return ReplayDataSource(
            price_cache,
            db_path=db_path,
            market=profile.key,
            session_clock=session_clock,
            universe=replay_universe,
            config=config,
        )
    else:
        logger.info("Market data source: GBM Simulator")
        return SimulatorDataSource(
            price_cache=price_cache, session_clock=session_clock, universe=universe
        )
