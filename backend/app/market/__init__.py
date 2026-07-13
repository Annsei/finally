"""Market data subsystem for FinAlly.

Public API:
    PriceUpdate         - Immutable price snapshot dataclass
    MarketEvent         - Immutable sudden-move event record (news feed)
    PriceCache          - Thread-safe in-memory price store
    MarketDataSource    - Abstract interface for data providers
    SessionClock        - Open/closed trading-session state machine (M3.1)
    session_clock_loop  - Background task driving session transitions
    asset_class_for     - 'equity' | 'crypto' classification helper (M3.3)
    create_market_data_source - Factory that selects simulator, Massive,
                          AKShare live quotes, or historical replay
                          (FINALLY_LIVE_SOURCE, D2 §1 / D3 §2)
    create_stream_router - FastAPI router factory for SSE endpoint
"""

from .cache import PriceCache
from .factory import create_market_data_source
from .interface import MarketDataSource
from .models import MarketEvent, PriceUpdate
from .seed_prices import asset_class_for
from .session import SessionClock, session_clock_loop
from .stream import create_stream_router

__all__ = [
    "PriceUpdate",
    "MarketEvent",
    "PriceCache",
    "MarketDataSource",
    "SessionClock",
    "session_clock_loop",
    "asset_class_for",
    "create_market_data_source",
    "create_stream_router",
]
