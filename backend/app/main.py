"""FinAlly FastAPI application entry point.

Wires together:
- Market data source (simulator or Massive API) with PriceCache
- SQLite database initialization
- All API routers (SSE streaming, health, portfolio, watchlist)
- Static file serving (Next.js export — conditional on build existing)
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db.connection import get_conn, init_db
from app.market import (
    PriceCache,
    SessionClock,
    create_market_data_source,
    create_stream_router,
    session_clock_loop,
)
from app.market.seed_prices import DEFAULT_WATCHLIST

logger = logging.getLogger(__name__)

# Session clock defaults (M3.1): 30-minute open sessions, 2-minute breaks.
DEFAULT_SESSION_OPEN_SECONDS = 1800.0
DEFAULT_SESSION_BREAK_SECONDS = 120.0


def _read_commission_bps() -> float:
    """Parse FINALLY_COMMISSION_BPS from the environment (default 0.0).

    Read ONCE at app startup and passed down to routers and the fill loop like
    the other config (db_path, price_cache) — helpers never read the env
    themselves. Invalid or negative values log a warning and fall back to 0.0
    (commission-free, the pre-M1 behavior).
    """
    raw = os.getenv("FINALLY_COMMISSION_BPS", "").strip()
    if not raw:
        return 0.0
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid FINALLY_COMMISSION_BPS=%r — using 0.0", raw)
        return 0.0
    if value < 0:
        logger.warning("Negative FINALLY_COMMISSION_BPS=%r — using 0.0", raw)
        return 0.0
    return value


def _read_session_config() -> tuple[float, float] | None:
    """Parse the session-clock env config (M3.1). Read ONCE at app startup.

    Reads ``FINALLY_SESSION_OPEN_SECONDS`` (default 1800) and
    ``FINALLY_SESSION_BREAK_SECONDS`` (default 120). If either value is
    unparsable or <= 0 the market runs in 24/7 mode (always open, no
    transitions) — returns None in that case, otherwise
    ``(open_seconds, break_seconds)``.
    """
    values: list[float] = []
    for name, default in (
        ("FINALLY_SESSION_OPEN_SECONDS", DEFAULT_SESSION_OPEN_SECONDS),
        ("FINALLY_SESSION_BREAK_SECONDS", DEFAULT_SESSION_BREAK_SECONDS),
    ):
        raw = os.getenv(name, "").strip()
        if not raw:
            values.append(default)
            continue
        try:
            value = float(raw)
        except ValueError:
            logger.warning("Invalid %s=%r — using 24/7 market mode", name, raw)
            return None
        if value <= 0:
            logger.info("%s=%r is <= 0 — using 24/7 market mode", name, raw)
            return None
        values.append(value)
    return values[0], values[1]


def _create_session_clock() -> SessionClock:
    """Build the app's SessionClock from the environment (M3.1).

    Real market data (MASSIVE_API_KEY active) forces 24/7 mode regardless of
    the session env vars — the simulator's session cycle makes no sense
    against live quotes. Otherwise the env config decides (see
    ``_read_session_config``).
    """
    massive_active = bool(os.getenv("MASSIVE_API_KEY", "").strip())
    session_config = None if massive_active else _read_session_config()
    if session_config is None:
        reason = "Massive API active" if massive_active else "env config"
        logger.info("FinAlly startup: session clock in 24/7 mode (%s)", reason)
        return SessionClock()
    logger.info(
        "FinAlly startup: session clock enabled (open=%ss, break=%ss)",
        session_config[0],
        session_config[1],
    )
    return SessionClock(*session_config)


def _mount_static_files(app: FastAPI) -> None:
    """Mount the static frontend after all API routers are registered."""
    static_dir = Path(__file__).parent.parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
        logger.info("Serving static files from %s", static_dir)


async def _snapshot_loop(price_cache: PriceCache, db_path: str, interval: int = 30) -> None:
    """Background task: record a portfolio snapshot PER USER every ``interval``
    seconds (M4 — one row per users_profile row, single commit per cycle).

    Runs indefinitely until cancelled via ``asyncio.CancelledError``.
    """
    while True:
        try:
            conn = get_conn(db_path)
            try:
                from app.routes.portfolio import record_snapshots_for_all_users
                record_snapshots_for_all_users(conn, price_cache)
                # The helper does not commit (caller owns the transaction) —
                # commit this cycle's snapshots here.
                conn.commit()
            finally:
                conn.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Snapshot loop error — will retry in %ds", interval)
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle: start/stop market data and initialize DB."""
    db_path = os.getenv("DB_PATH", "db/finally.db")
    commission_bps = _read_commission_bps()
    if commission_bps:
        logger.info("FinAlly startup: commission enabled at %s bps", commission_bps)

    logger.info("FinAlly startup: initializing database at %s", db_path)
    init_db(db_path)

    # Session clock (M3.1): open/close cycle from env, 24/7 when disabled or
    # when real market data is active. Starts OPEN with session_id 1.
    session_clock = _create_session_clock()

    logger.info("FinAlly startup: creating price cache and market data source")
    price_cache = PriceCache()
    source = create_market_data_source(price_cache, session_clock)

    # Start market data with the UNION of every user's watchlist (M4 — the
    # source tracks all users' tickers), falling back to the default
    # watchlist (the 10 equities — crypto joins via watchlist adds).
    conn = get_conn(db_path)
    rows = conn.execute("SELECT DISTINCT ticker FROM watchlist").fetchall()
    tickers = [row["ticker"] for row in rows]
    conn.close()
    if not tickers:
        tickers = list(DEFAULT_WATCHLIST)
    await source.start(tickers)
    logger.info("FinAlly startup: market data source started with %d tickers", len(tickers))

    # Store on app.state for access in request handlers
    app.state.price_cache = price_cache
    app.state.market_source = source
    app.state.session_clock = session_clock

    # Register routers that depend on price_cache inside lifespan (factory pattern)
    app.include_router(create_stream_router(price_cache))

    # Portfolio router
    from app.routes.portfolio import create_portfolio_router
    portfolio_router = create_portfolio_router(
        price_cache, db_path, commission_bps, session_clock
    )
    app.include_router(portfolio_router)

    # Orders router (limit / stop / stop_limit)
    from app.routes.orders import create_orders_router, orders_fill_loop
    orders_router = create_orders_router(price_cache, db_path, commission_bps)
    app.include_router(orders_router)

    # Rules router (standing rules engine, M2.2)
    from app.routes.rules import create_rules_router, rules_eval_loop
    rules_router = create_rules_router(price_cache, db_path)
    app.include_router(rules_router)

    # Watchlist router
    from app.routes.watchlist import create_watchlist_router
    watchlist_router = create_watchlist_router(price_cache, db_path)
    app.include_router(watchlist_router)

    # Chat router
    from app.routes.chat import create_chat_router
    chat_router = create_chat_router(price_cache, db_path, commission_bps, session_clock)
    app.include_router(chat_router)

    # Market data router (history backfill, events, session state)
    from app.routes.market import create_market_router
    market_router = create_market_router(price_cache, session_clock)
    app.include_router(market_router)

    # Auth router (M4.1 — name-only login, cookie session)
    from app.routes.auth import create_auth_router
    app.include_router(create_auth_router(db_path))

    # Leaderboard router (M4.2)
    from app.routes.leaderboard import create_leaderboard_router
    app.include_router(create_leaderboard_router(price_cache, db_path))

    # Seasons router (M4.3 — reset + archive)
    from app.routes.seasons import create_seasons_router
    app.include_router(create_seasons_router(price_cache, db_path))

    # Mount static files LAST — must not shadow /api/* routes.
    _mount_static_files(app)

    # Start background portfolio snapshot task (every 30 seconds)
    snapshot_task = asyncio.create_task(_snapshot_loop(price_cache, db_path))
    app.state.snapshot_task = snapshot_task
    logger.info("FinAlly startup: portfolio snapshot background task started")

    # Start background order fill task (every ~1 second)
    orders_fill_task = asyncio.create_task(
        orders_fill_loop(price_cache, db_path, commission_bps=commission_bps)
    )
    app.state.orders_fill_task = orders_fill_task
    logger.info("FinAlly startup: order fill background task started")

    # Start background rules evaluator task (every ~1 second, M2.2)
    rules_eval_task = asyncio.create_task(
        rules_eval_loop(price_cache, db_path, commission_bps=commission_bps)
    )
    app.state.rules_eval_task = rules_eval_task
    logger.info("FinAlly startup: rules evaluator background task started")

    # Start background AI briefs watcher task (every ~2 seconds, M2.3)
    from app.briefs import briefs_watch_loop
    briefs_watch_task = asyncio.create_task(briefs_watch_loop(price_cache, db_path))
    app.state.briefs_watch_task = briefs_watch_task
    logger.info("FinAlly startup: AI briefs watcher background task started")

    background_tasks = [snapshot_task, orders_fill_task, rules_eval_task, briefs_watch_task]

    # Start the session clock driver (every ~1 second, M3.1) — settlement at
    # close (stamp closes + expire equity DAY orders), day-state roll at open.
    # Skipped entirely in 24/7 mode (the clock never transitions).
    if not session_clock.always_open:
        from app.settlement import roll_session_open, settle_session_close
        session_clock_task = asyncio.create_task(
            session_clock_loop(
                session_clock,
                on_close=lambda: settle_session_close(price_cache, db_path),
                on_open=lambda: roll_session_open(price_cache),
            ),
            name="session-clock-loop",
        )
        app.state.session_clock_task = session_clock_task
        background_tasks.append(session_clock_task)
        logger.info("FinAlly startup: session clock background task started")

    yield

    logger.info("FinAlly shutdown: cancelling background tasks")
    for task in background_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    logger.info("FinAlly shutdown: stopping market data source")
    await source.stop()
    logger.info("FinAlly shutdown: complete")


# Create FastAPI application
app = FastAPI(
    title="FinAlly",
    description="AI-powered trading workstation",
    version="0.1.0",
    lifespan=lifespan,
)

# Register routers that have no dependencies (can be registered at import time)
from app.routes.health import router as health_router  # noqa: E402

app.include_router(health_router)
