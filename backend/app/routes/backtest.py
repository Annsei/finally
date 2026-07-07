"""Backtest API route for FinAlly (M5 — strategy backtester).

Provides:
- POST /api/backtest — validate a buy-entry strategy config and run the
  deterministic GBM backtest engine (``app.backtest``) synchronously.

Stateless compute: the endpoint never reads or writes the database. All
validation failures return HTTP 400 ``{"error": msg}`` via the shared
``normalize_backtest_config`` helper — the same source of truth the chat
auto-execution pipeline uses, so HTTP and chat report identical errors.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.backtest import normalize_backtest_config, run_backtest
from app.market.cache import PriceCache


class BacktestRequest(BaseModel):
    ticker: str
    trigger_type: str
    threshold: float
    quantity: float
    side: str | None = None  # default "buy" — the only supported side
    take_profit_pct: float | None = None  # exit: percent above entry (> 0)
    stop_loss_pct: float | None = None  # exit: percent below entry (> 0)
    days: int | None = None  # default 30 (5-120)
    runs: int | None = None  # default 1 (1-50 Monte Carlo re-runs)
    seed: int | None = None  # omitted -> drawn randomly, always echoed back


def create_backtest_router(price_cache: PriceCache, commission_bps: float = 0.0) -> APIRouter:
    """Factory: build the backtest APIRouter with injected dependencies.

    Args:
        price_cache: Shared in-memory price cache — preferred anchor-price
            source (tickers without a live quote fall back to SEED_PRICES).
        commission_bps: Commission in basis points of notional applied to
            each simulated fill (FINALLY_COMMISSION_BPS, read once at app
            startup in main.py) — backtests price the same friction as live
            trades.

    Returns:
        A configured FastAPI APIRouter ready to be registered with ``app.include_router``.
    """
    router = APIRouter(prefix="/api/backtest", tags=["backtest"])

    @router.post("")
    async def run_backtest_endpoint(body: BacktestRequest) -> dict:
        """Run one backtest and return the full result (contract §3).

        Validates via ``normalize_backtest_config`` (400 ``{"error": msg}``
        on any failure), then runs the engine off the event loop — a
        120-day x 50-run Monte Carlo must not stall the SSE streams. The
        echoed ``config`` always carries the seed (drawn when omitted) so
        every result is reproducible.
        """
        outcome = normalize_backtest_config(
            price_cache,
            ticker=body.ticker,
            trigger_type=body.trigger_type,
            threshold=body.threshold,
            quantity=body.quantity,
            side=body.side,
            take_profit_pct=body.take_profit_pct,
            stop_loss_pct=body.stop_loss_pct,
            days=body.days,
            runs=body.runs,
            seed=body.seed,
        )
        if outcome["status"] == "failed":
            return JSONResponse(status_code=400, content={"error": outcome["error"]})
        return await asyncio.to_thread(
            run_backtest,
            outcome["config"],
            commission_bps=commission_bps,
            end_time=time.time(),
        )

    return router
