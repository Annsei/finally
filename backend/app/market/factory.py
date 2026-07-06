"""Factory for creating market data sources."""

from __future__ import annotations

import logging
import os

from .cache import PriceCache
from .interface import MarketDataSource
from .massive_client import MassiveDataSource
from .session import SessionClock
from .simulator import SimulatorDataSource

logger = logging.getLogger(__name__)


def create_market_data_source(
    price_cache: PriceCache, session_clock: SessionClock | None = None
) -> MarketDataSource:
    """Create the appropriate market data source based on environment variables.

    - MASSIVE_API_KEY set and non-empty → MassiveDataSource (real market data)
    - Otherwise → SimulatorDataSource (GBM simulation)

    Returns an unstarted source. Caller must await source.start(tickers).

    Args:
        price_cache: Shared cache the source writes ticks into.
        session_clock: Optional session clock (M3.1). Only the simulator uses
            it (equities freeze while closed); the Massive source streams real
            data and always runs 24/7 — main.py forces a 24/7 clock when
            MASSIVE_API_KEY is active.
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()

    if api_key:
        logger.info("Market data source: Massive API (real data)")
        return MassiveDataSource(api_key=api_key, price_cache=price_cache)
    else:
        logger.info("Market data source: GBM Simulator")
        return SimulatorDataSource(price_cache=price_cache, session_clock=session_clock)
