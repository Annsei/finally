"""Replay status endpoint for FinAlly (D3 contract §3).

GET /api/market/replay — the two-state replay indicator, no auth (market-
level data, like the other /api/market routes):

- Non-replay mode (simulator/massive/akshare): ``{"active": false}`` — the
  endpoint exists in EVERY mode so the frontend can poll it unconditionally.
- Replay mode: ``{"active": true, "from", "to", "current_date",
  "day_index", "total_days", "seconds_per_day", "loop", "finished",
  "source_hint"}`` — a thread-safe snapshot read from the source.

The session snapshot endpoint (GET /api/market/session) is deliberately
untouched: replay state lives on this independent endpoint instead of
conditional extra keys, so the session payload's exact shape stays pinned.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.market.interface import MarketDataSource
from app.market.replay_source import ReplayDataSource


def create_replay_router(market_source: MarketDataSource | None) -> APIRouter:
    """Factory: build the /api/market/replay APIRouter (D3 §3).

    Args:
        market_source: The app's active market data source. Only a
            :class:`ReplayDataSource` reports an active replay; every other
            source (or None, for legacy wiring) reports ``{"active": false}``.
    """
    router = APIRouter(prefix="/api/market", tags=["market"])

    @router.get("/replay")
    async def get_replay() -> dict:
        """Return the replay status (two states — see module docstring)."""
        if isinstance(market_source, ReplayDataSource):
            return market_source.snapshot()
        return {"active": False}

    return router
