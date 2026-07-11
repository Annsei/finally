"""Backtest API route for FinAlly (M5 — strategy backtester).

Provides:
- POST /api/backtest — validate a buy-entry strategy config and run the
  backtest engine (``app.backtest``) synchronously. ``source`` selects the
  data (D1 §3): omitted/"synthetic" is the legacy deterministic GBM path
  (byte-identical responses); "history" replays the user-synced daily bars
  (trading-day horizon, T+1 open fills, runs must be 1).

Stateless compute on the synthetic path (never touches the database);
history mode performs one read of the ``daily_bars`` table to load the
evaluated window. All validation failures return HTTP 400 ``{"error": msg}``
via the shared ``normalize_backtest_config`` helper — the same source of
truth the chat auto-execution pipeline uses, so HTTP and chat report
identical errors.
"""

from __future__ import annotations

import asyncio
import os
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, FiniteFloat

from app.backtest import (
    STARTING_CASH,
    attach_history_bars,
    normalize_backtest_config,
    run_backtest,
)
from app.db.connection import get_conn
from app.market.cache import PriceCache
from app.market.profiles import MarketProfile


class BacktestRequest(BaseModel):
    ticker: str
    trigger_type: str
    threshold: FiniteFloat
    quantity: FiniteFloat
    side: str | None = None  # default "buy" — the only supported side
    take_profit_pct: FiniteFloat | None = None  # exit: percent above entry (> 0)
    stop_loss_pct: FiniteFloat | None = None  # exit: percent below entry (> 0)
    days: int | None = None  # default 30 (5-120; history: trading days 20-750)
    runs: int | None = None  # default 1 (1-50 Monte Carlo re-runs; history: 1)
    seed: int | None = None  # omitted -> drawn randomly (history: ignored, null)
    source: str | None = None  # D1 §3: "synthetic" (default) | "history"


def create_backtest_router(
    price_cache: PriceCache,
    commission_bps: float = 0.0,
    profile: MarketProfile | None = None,
    db_path: str | None = None,
) -> APIRouter:
    """Factory: build the backtest APIRouter with injected dependencies.

    Args:
        price_cache: Shared in-memory price cache — preferred anchor-price
            source (tickers without a live quote fall back to SEED_PRICES).
        commission_bps: Commission in basis points of notional applied to
            each simulated fill (FINALLY_COMMISSION_BPS, read once at app
            startup in main.py) — backtests price the same friction as live
            trades.
        profile: Optional market profile (CN-1, resolved once in main.py).
            Supplies the anchor/params universe and the starting cash; None
            keeps the US constants and $10,000 (the pre-CN-1 behavior).
        db_path: SQLite path backing history-mode bar loads (D1 §3). main.py
            passes it explicitly; when omitted (legacy wiring) history
            requests resolve the DB_PATH environment variable at request
            time — the routes/market.py archive-endpoint convention.

    Returns:
        A configured FastAPI APIRouter ready to be registered with ``app.include_router``.
    """
    universe = profile.universe if profile is not None else None
    starting_cash = profile.seed_cash if profile is not None else STARTING_CASH
    market = profile.key if profile is not None else "us"
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
            universe=universe,
            profile=profile,
            source=body.source,
        )
        if outcome["status"] == "failed":
            return JSONResponse(status_code=400, content={"error": outcome["error"]})
        config = outcome["config"]
        # D1 §3: history mode loads the stored daily-bar window (one read).
        if config.get("data_source") == "history":
            resolved_db_path = (
                db_path if db_path is not None else os.getenv("DB_PATH", "db/finally.db")
            )
            conn = get_conn(resolved_db_path)
            try:
                error = attach_history_bars(config, conn, market=market)
            finally:
                conn.close()
            if error is not None:
                return JSONResponse(status_code=400, content={"error": error})
        return await asyncio.to_thread(
            run_backtest,
            config,
            commission_bps=commission_bps,
            end_time=time.time(),
            starting_cash=starting_cash,
            profile=profile,
        )

    return router
