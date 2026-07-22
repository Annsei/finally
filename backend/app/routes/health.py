"""Health check endpoint for FinAlly.

Used by Docker health checks and load balancers to verify the service is running.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Return service health status.

    Returns:
        JSON object with ``status`` key set to ``"ok"``.
    """
    return {"status": "ok"}


@router.get("/ready")
async def readiness_check(request: Request):
    """Return whether market-dependent APIs can safely serve new trades."""
    price_cache = getattr(request.app.state, "price_cache", None)
    source = getattr(request.app.state, "market_source", None)
    if price_cache is None or source is None:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "market_not_started"},
        )

    tracked = source.get_tickers()
    missing = sorted(t for t in tracked if price_cache.get(t) is None)
    session_clock = getattr(request.app.state, "session_clock", None)
    market_open = session_clock is None or session_clock.is_open
    stale = sorted(
        t for t in tracked if t not in missing and market_open and not price_cache.is_fresh(t)
    )
    if not tracked or missing or stale:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "reason": "market_data_unavailable",
                "tracked": len(tracked),
                "missing": missing,
                "stale": stale,
            },
        )
    return {
        "status": "ready",
        "tracked": len(tracked),
        "missing": [],
        "stale": [],
    }
