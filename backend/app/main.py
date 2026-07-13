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
import math
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db.connection import get_conn, init_db
from app.indicators import required_history_seconds
from app.market import (
    PriceCache,
    SessionClock,
    create_market_data_source,
    create_stream_router,
    session_clock_loop,
)
from app.market.factory import REAL_DATA_SOURCES, resolve_live_source
from app.market.profiles import MarketProfile, resolve_market_profile
from app.settings import RuntimeSettings

logger = logging.getLogger(__name__)

# Session clock defaults (M3.1): 30-minute open sessions, 2-minute breaks.
DEFAULT_SESSION_OPEN_SECONDS = 1800.0
DEFAULT_SESSION_BREAK_SECONDS = 120.0


def _read_commission_bps(profile: MarketProfile | None = None) -> float:
    """Parse FINALLY_COMMISSION_BPS from the environment (CN-2 §1).

    Read ONCE at app startup and passed down to routers and the fill loop like
    the other config (db_path, price_cache) — helpers never read the env
    themselves. An explicit env value always wins. When the env is unset/empty,
    the active profile's ``default_commission_bps`` is used (cn=2.5) so A-share
    commission applies by default; with no profile (or the us profile, whose
    default is 0.0) this stays the pre-M1 commission-free behavior. Invalid or
    negative values log a warning and fall back to 0.0.
    """
    raw = os.getenv("FINALLY_COMMISSION_BPS", "").strip()
    if not raw:
        return profile.default_commission_bps if profile is not None else 0.0
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid FINALLY_COMMISSION_BPS=%r — using 0.0", raw)
        return 0.0
    if not math.isfinite(value) or value < 0:
        logger.warning("Invalid FINALLY_COMMISSION_BPS=%r — using 0.0", raw)
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
        if not math.isfinite(value) or value <= 0:
            logger.info("%s=%r is <= 0 — using 24/7 market mode", name, raw)
            return None
        values.append(value)
    return values[0], values[1]


def _create_session_clock(profile: MarketProfile | None = None) -> SessionClock:
    """Build the app's SessionClock from the environment (M3.1 / CN-2 §5 / D2 §1).

    Real market data (a resolved ``massive`` OR ``akshare`` live source)
    forces 24/7 mode regardless of the session env vars — the simulator's
    session cycle makes no sense against live quotes. With the default
    ``FINALLY_LIVE_SOURCE`` (auto) this is byte-identical to the pre-D2
    MASSIVE_API_KEY check. Otherwise the env config decides (see
    ``_read_session_config``).

    CN-2 §5: when the active profile has ``midday_break`` and the session clock
    is enabled, the lunch break length equals the parsed
    ``FINALLY_SESSION_BREAK_SECONDS`` (default 120), turning the day into the
    four-phase am -> midday -> pm -> closed cycle.

    D3 §2: the replay live source builds the clock from
    FINALLY_REPLAY_SECONDS_PER_DAY / FINALLY_REPLAY_BREAK_SECONDS instead of
    the regular session env vars (one replay day == one session). The CN
    midday break is preserved per profile (am+pm each half of the day, the
    break_seconds-long lunch pause in between) — identical shape to the
    simulator's four-phase day.
    """
    live_source = resolve_live_source()
    if live_source == "replay":
        from app.market.replay_source import read_replay_env

        replay_config = read_replay_env()
        midday_break_seconds = (
            replay_config.break_seconds
            if profile is not None and profile.midday_break
            else 0.0
        )
        logger.info(
            "FinAlly startup: session clock in replay mode "
            "(day=%ss, break=%ss, midday=%ss)",
            replay_config.seconds_per_day,
            replay_config.break_seconds,
            midday_break_seconds,
        )
        return SessionClock(
            replay_config.seconds_per_day,
            replay_config.break_seconds,
            midday_break_seconds=midday_break_seconds,
        )
    real_data_active = live_source in REAL_DATA_SOURCES
    session_config = None if real_data_active else _read_session_config()
    if session_config is None:
        if live_source == "massive":
            reason = "Massive API active"
        elif live_source == "akshare":
            reason = "AKShare live data active"
        else:
            reason = "env config"
        logger.info("FinAlly startup: session clock in 24/7 mode (%s)", reason)
        return SessionClock()
    open_seconds, break_seconds = session_config
    midday_break_seconds = (
        break_seconds if profile is not None and profile.midday_break else 0.0
    )
    logger.info(
        "FinAlly startup: session clock enabled (open=%ss, break=%ss, midday=%ss)",
        open_seconds,
        break_seconds,
        midday_break_seconds,
    )
    return SessionClock(
        open_seconds, break_seconds, midday_break_seconds=midday_break_seconds
    )


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
    settings = RuntimeSettings.from_env().validate(db_path=db_path)
    app.state.settings = settings
    logger.info("FinAlly effective config: %s", settings.effective_config())

    # Market profile (CN-1): FINALLY_MARKET read ONCE here (default 'us') and
    # injected everywhere — universe into the data source, seed cash into DB
    # seeding and the seasons/leaderboard/backtest factories, and (CN-2) the
    # 整手/涨跌停/T+1/fee/午休 mechanics into every trade-executing factory/loop.
    profile = resolve_market_profile()
    logger.info("FinAlly startup: market profile '%s' active", profile.key)
    # D2 §1: scoped to the RESOLVED live source (byte-identical with the
    # default FINALLY_LIVE_SOURCE, where 'massive' resolves iff the key is
    # set) so an explicit akshare/simulator choice on cn is not blocked by a
    # stray MASSIVE_API_KEY. An invalid FINALLY_LIVE_SOURCE raises here —
    # explicit misconfiguration fails startup.
    if profile.key == "cn" and resolve_live_source() == "massive":
        raise ValueError(
            "MASSIVE_API_KEY currently supports only the US market profile; "
            "unset it to use the CN simulator"
        )

    # Commission (CN-2 §1): env wins, else the profile default (cn=2.5 万分).
    commission_bps = _read_commission_bps(profile)
    if commission_bps:
        logger.info("FinAlly startup: commission enabled at %s bps", commission_bps)

    logger.info("FinAlly startup: initializing database at %s", db_path)
    init_db(
        db_path,
        seed_cash=profile.seed_cash,
        default_watchlist=list(profile.universe.default_watchlist),
    )

    # Session clock (M3.1 / CN-2 §5): open/close cycle from env (four-phase with
    # a midday break for cn), 24/7 when disabled or real market data is active.
    session_clock = _create_session_clock(profile)

    # CN-2 §2: in 24/7 mode there is no "next trading day", so T+1 can never
    # unlock — disable it on the profile handed to the background loops (which
    # receive no session clock). The portfolio/chat routes keep the full profile
    # and rely on the session clock in ``t1_applies``; both agree in every mode.
    if session_clock.always_open and profile.t_plus > 0:
        trading_profile = replace(profile, t_plus=0)
        logger.info("FinAlly startup: 24/7 mode — T+1 disabled for background loops")
    else:
        trading_profile = profile

    # Replay startup data (D3 §2, BEFORE the source is created): verify the
    # default equity universe's daily-bar coverage over the replay window,
    # synchronously inject the sample provider for tickers lacking coverage
    # (zero network, zero optional imports), and fail startup with explicit
    # coverage/guidance when the window still cannot be replayed.
    if resolve_live_source() == "replay":
        from app.market.replay_source import ensure_replay_startup_data

        replay_days = ensure_replay_startup_data(db_path, profile)
        logger.info(
            "FinAlly startup: replay window resolved to %d trading days (%s..%s)",
            len(replay_days),
            replay_days[0],
            replay_days[-1],
        )

    logger.info("FinAlly startup: creating price cache and market data source")
    # CN-2 §4: the price-limit function funnels every tick through the clamp.
    # P2 §2/§3: the ring buffer is the live strategy engine's ONLY bar source,
    # so its capacity is derived from the FIELD_SPECS parameter upper bounds —
    # every condition that validates (e.g. window_high minutes=240) must be
    # satisfiable live once the buffer fills, never permanently warm-up-False.
    price_cache = PriceCache(
        history_capacity=required_history_seconds(),
        limit_pct_fn=profile.price_limit_pct,
        max_quote_age_seconds=settings.quote_max_age_seconds,
    )
    source = create_market_data_source(
        price_cache, session_clock, profile.universe, db_path=db_path
    )

    # Start market data with the UNION of every user's watchlist (M4 — the
    # source tracks all users' tickers), falling back to the profile's
    # default watchlist (us: the 10 equities — crypto joins via watchlist
    # adds; cn: the full 14-ticker A-share universe).
    conn = get_conn(db_path)
    from app.routes.watchlist import required_market_tickers
    tickers = required_market_tickers(conn)
    conn.close()
    if not tickers:
        tickers = list(profile.universe.default_watchlist)
    await source.start(tickers)
    logger.info("FinAlly startup: market data source started with %d tickers", len(tickers))

    # Store on app.state for access in request handlers
    app.state.price_cache = price_cache
    app.state.market_source = source
    app.state.session_clock = session_clock
    app.state.market_profile = profile

    # Register routers that depend on price_cache inside lifespan (factory pattern)
    app.include_router(create_stream_router(price_cache))

    # Portfolio router — full profile + session clock (t1_applies handles 24/7).
    from app.routes.portfolio import create_portfolio_router
    portfolio_router = create_portfolio_router(
        price_cache, db_path, commission_bps, session_clock, profile
    )
    app.include_router(portfolio_router)

    # Orders router (limit / stop / stop_limit) — the session clock scopes the
    # placement-time quote freshness gate to open markets (after-hours resting
    # orders stay legal); T+1 still uses the 24/7-neutralized trading_profile.
    from app.routes.orders import create_orders_router, orders_fill_loop
    orders_router = create_orders_router(
        price_cache, db_path, commission_bps, trading_profile, session_clock
    )
    app.include_router(orders_router)

    # Rules router (standing rules engine, M2.2) — profile drives the 整手 check.
    from app.routes.rules import create_rules_router, rules_eval_loop
    rules_router = create_rules_router(price_cache, db_path, trading_profile)
    app.include_router(rules_router)

    # Backtest router (M5 — stateless on the synthetic path; D1 history mode
    # reads the daily_bars table, hence the injected db_path)
    from app.routes.backtest import create_backtest_router
    app.include_router(
        create_backtest_router(price_cache, commission_bps, profile, db_path=db_path)
    )

    # Run Library router (P2 §5 — persisted backtest runs). Registered right
    # next to the stateless backtest route; its /api/backtest/runs prefix
    # never collides with POST /api/backtest.
    from app.routes.backtest_runs import create_backtest_runs_router
    app.include_router(
        create_backtest_runs_router(price_cache, db_path, commission_bps, profile)
    )

    # Strategies router (P2 §6 — CRUD + state machine + performance +
    # six-template registry).
    from app.routes.strategies import create_strategies_router
    app.include_router(create_strategies_router(price_cache, db_path, profile))

    # Watchlist router
    from app.routes.watchlist import create_watchlist_router
    watchlist_router = create_watchlist_router(price_cache, db_path)
    app.include_router(watchlist_router)

    # Chat router — full profile + session clock (AI trades gate on the clock
    # like manual ones; AI backtests use the full profile's T+1/fees).
    from app.routes.chat import create_chat_router
    chat_router = create_chat_router(
        price_cache, db_path, commission_bps, session_clock, profile
    )
    app.include_router(chat_router)

    # Market data router (history backfill, events + archive, quotes, session
    # state) — db_path backs /events/archive, universe supplies quote sectors.
    from app.routes.market import create_market_router
    market_router = create_market_router(
        price_cache, session_clock, db_path=db_path, universe=profile.universe
    )
    app.include_router(market_router)

    # Replay status router (D3 §3 — GET /api/market/replay). Registered in
    # EVERY mode: non-replay sources report {"active": false}; the session
    # snapshot endpoint keeps its exact shape untouched.
    from app.routes.replay import create_replay_router
    app.include_router(create_replay_router(source))

    # History data router (D1 §2 — daily-bar sync/query; sample source plus
    # lazily-imported yfinance/akshare, so registration never needs network
    # or the optional packages and cannot block startup).
    from app.routes.history import create_history_router
    app.include_router(create_history_router(db_path, profile=profile))

    # Market profile router (CN-1 — the frontend's runtime market config)
    from app.routes.profile import create_profile_router
    app.include_router(create_profile_router(profile))

    # Auth router (M4.1 — name-only login, cookie session)
    from app.routes.auth import create_auth_router
    app.include_router(create_auth_router(db_path, profile=profile, settings=settings))

    # API keys router (P3 §6 — programmatic access management). Cookie
    # identity ONLY: the gateway middleware 403s Bearer calls to /api/keys*
    # before they reach these routes (keys cannot manage keys). Settings gate
    # Guest key creation to local-demo (classroom-server requires a login).
    from app.routes.keys import create_keys_router
    app.include_router(create_keys_router(db_path, settings=settings))

    # Leaderboard router (M4.2)
    from app.routes.leaderboard import create_leaderboard_router
    app.include_router(
        create_leaderboard_router(price_cache, db_path, seed_cash=profile.seed_cash)
    )

    # Players router (P4 §4 — public player profiles + privacy toggle).
    # seed_cash keeps /player return% on the leaderboard's exact baseline.
    from app.routes.players import create_players_router
    app.include_router(
        create_players_router(price_cache, db_path, seed_cash=profile.seed_cash)
    )

    # Seasons router (M4.3 — reset + archive)
    from app.routes.seasons import create_seasons_router
    app.include_router(
        create_seasons_router(
            price_cache,
            db_path,
            seed_cash=profile.seed_cash,
            settings=settings,
        )
    )

    # Competitions router (D2 §3 — timed private competitions). Creation is
    # cookie-only (Bearer 403 in-route); join/read allow Bearer by design.
    from app.routes.competitions import create_competitions_router
    app.include_router(create_competitions_router(price_cache, db_path))

    # Mount static files LAST — must not shadow /api/* routes.
    _mount_static_files(app)

    # Start background portfolio snapshot task (every 30 seconds)
    snapshot_task = asyncio.create_task(_snapshot_loop(price_cache, db_path))
    app.state.snapshot_task = snapshot_task
    logger.info("FinAlly startup: portfolio snapshot background task started")

    # Start background order fill task (every ~1 second) — background fills obey
    # A-share fees/T+1 via the 24/7-neutralized trading_profile.
    orders_fill_task = asyncio.create_task(
        orders_fill_loop(
            price_cache, db_path, commission_bps=commission_bps, profile=trading_profile
        )
    )
    app.state.orders_fill_task = orders_fill_task
    logger.info("FinAlly startup: order fill background task started")

    # Start background rules evaluator task (every ~1 second, M2.2)
    rules_eval_task = asyncio.create_task(
        rules_eval_loop(
            price_cache, db_path, commission_bps=commission_bps, profile=trading_profile
        )
    )
    app.state.rules_eval_task = rules_eval_task
    logger.info("FinAlly startup: rules evaluator background task started")

    # Start background strategies evaluator task (every ~1 second, P2 §3) —
    # mirrors the rules loop: the 24/7-neutralized trading_profile drives
    # 整手/fees/T+1 and the session clock gates fills while the market is
    # closed.
    from app.strategy_engine import strategies_eval_loop
    strategies_eval_task = asyncio.create_task(
        strategies_eval_loop(
            price_cache,
            db_path,
            commission_bps=commission_bps,
            profile=trading_profile,
            session_clock=session_clock,
        )
    )
    app.state.strategies_eval_task = strategies_eval_task
    logger.info("FinAlly startup: strategies evaluator background task started")

    # Start background AI briefs watcher task (every ~2 seconds, M2.3). CN-3:
    # pass the active profile so briefs and narratives are written in the
    # market's language (locale is identical on trading_profile; briefs execute
    # no trades, so T+1 neutralization is irrelevant here).
    from app.briefs import briefs_watch_loop
    briefs_watch_task = asyncio.create_task(
        briefs_watch_loop(price_cache, db_path, profile=profile)
    )
    app.state.briefs_watch_task = briefs_watch_task
    logger.info("FinAlly startup: AI briefs watcher background task started")

    # Start background market-event archiver task (every ~5 seconds, P1 §3.2)
    # — upserts the in-memory event ring buffer into the market_events table
    # so the events archive endpoint survives eviction and restarts.
    from app.events_archive import events_persist_loop
    events_persist_task = asyncio.create_task(events_persist_loop(price_cache, db_path))
    app.state.events_persist_task = events_persist_task
    logger.info("FinAlly startup: market-event archiver background task started")

    background_tasks = [
        snapshot_task,
        orders_fill_task,
        rules_eval_task,
        strategies_eval_task,
        briefs_watch_task,
        events_persist_task,
    ]

    # Start the session clock driver (every ~1 second, M3.1) — settlement at
    # close (stamp closes + expire equity DAY orders), day-state roll at open.
    # Skipped entirely in 24/7 mode (the clock never transitions).
    if not session_clock.always_open:
        from app.settlement import roll_session_open, settle_session_close
        session_clock_task = asyncio.create_task(
            session_clock_loop(
                session_clock,
                on_close=lambda: settle_session_close(price_cache, db_path),
                # CN-2 §2: db_path lets the open hook release the T+1 lock.
                on_open=lambda: roll_session_open(price_cache, db_path),
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


# Create FastAPI application. P3 §7: the OpenAPI schema and Swagger UI live
# under /api/* (/api/openapi.json + /api/docs) so the static frontend export
# keeps the root paths; ReDoc is disabled. Note for API consumers: the SSE
# stream GET /api/stream/prices requires no authentication — sending a Bearer
# key is allowed and behaves identically (validated + rate limited, same
# stream).
app = FastAPI(
    title="FinAlly",
    description="AI-powered trading workstation",
    version="0.1.0",
    lifespan=lifespan,
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
    redoc_url=None,
)

# API-key gateway (P3 §2-§5): pure ASGI middleware — requests without an
# Authorization: Bearer header pass through untouched (SSE streaming and all
# cookie/anonymous traffic are byte-identical to the pre-P3 stack). With no
# explicit db_path it resolves the DB_PATH env var per request, the same
# source lifespan() reads at startup.
from app.api_gateway import ApiKeyGatewayMiddleware  # noqa: E402

app.add_middleware(ApiKeyGatewayMiddleware)

# Register routers that have no dependencies (can be registered at import time)
from app.routes.health import router as health_router  # noqa: E402

app.include_router(health_router)
