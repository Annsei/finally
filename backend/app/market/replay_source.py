"""Historical market replay data source (D3 contract §1/§2).

``ReplayDataSource`` "replays" stored ``daily_bars`` rows (sample/yfinance/
akshare — whatever a user previously synced) as platform-wide live market
data: one historical trading day is compressed into
``FINALLY_REPLAY_SECONDS_PER_DAY`` seconds of accelerated session, and the
previous close / CN price-limit band / intraday extremes all roll with the
replay calendar using the REAL historical values.

CORE INVARIANTS (contract §0):

- The settlement machinery is untouched: correctness comes from PATH
  CONSTRUCTION. Each replay day's last written tick is exactly that day's
  real close, so the existing ``settle_session_close`` stamp IS the real
  close and ``roll_session`` naturally rolls ``prev_close`` to the real
  previous close. Every tick additionally passes the day's real previous
  close explicitly (the AKShare real-data precedent), which also covers the
  loop-wrap day whose true prev_close is the PRE-window close rather than
  the settled last-day close.
- Determinism: intraday paths and volume jitter are seeded per
  ``(ticker, date)`` — the same window over the same data replays the same
  ticks, so tests can assert tick-by-tick.
- Zero network: startup injection uses only :class:`SampleProvider`
  (synchronous, no optional imports); real-data replays require the user to
  sync first — startup fails with explicit guidance instead of silently
  fetching.
- Session alignment mirrors ``SimulatorDataSource``'s polling pattern (no
  new hooks): the loop checks ``session_clock.is_open`` every interval
  (closed/midday → frozen, no writes) and detects day changes via
  ``session_id`` (each reopen advances the replay calendar). The intraday
  path advances by TICKS WRITTEN, not wall clock, so the CN four-phase
  am → midday → pm day splices seamlessly.

Only equity tickers with daily-bar coverage replay; crypto has no daily
bars and is simply absent from the cache in replay mode (documented).
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import random
import threading
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date as date_type

from app.db.connection import get_conn

from .interface import MarketDataSource
from .session import SessionClock
from .simulator import compute_quote
from .universe import MarketUniverse

logger = logging.getLogger(__name__)

# Environment variables (D3 §2) — read ONCE at startup by read_replay_env().
REPLAY_FROM_ENV = "FINALLY_REPLAY_FROM"
REPLAY_TO_ENV = "FINALLY_REPLAY_TO"
REPLAY_SECONDS_ENV = "FINALLY_REPLAY_SECONDS_PER_DAY"
REPLAY_BREAK_ENV = "FINALLY_REPLAY_BREAK_SECONDS"
REPLAY_LOOP_ENV = "FINALLY_REPLAY_LOOP"

# Defaults and clamp bounds (contract §2).
DEFAULT_REPLAY_SECONDS_PER_DAY = 120.0
MIN_REPLAY_SECONDS_PER_DAY = 30.0
MAX_REPLAY_SECONDS_PER_DAY = 600.0
DEFAULT_REPLAY_BREAK_SECONDS = 5.0
MIN_REPLAY_BREAK_SECONDS = 2.0
MAX_REPLAY_BREAK_SECONDS = 60.0
# Default window when FINALLY_REPLAY_FROM/TO are unset: the most recent
# common-coverage trading days across the market's default equity universe.
DEFAULT_REPLAY_WINDOW_DAYS = 20

# Intraday path synthesis (contract §1).
ACTIVE_PATH_FRACTION = 0.9  # tail 10% of the open window holds the close
NOISE_FRACTION = 0.001  # micro-noise amplitude: <= 0.1% of the local price
VOLUME_JITTER = 0.3  # per-tick volume jitter: uniform +/-30% before scaling

_FALSY = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class ReplayConfig:
    """Startup replay configuration (already parsed/clamped env values).

    ``from_date``/``to_date`` are ISO ``YYYY-MM-DD`` strings or both None
    (auto window: the most recent ``DEFAULT_REPLAY_WINDOW_DAYS`` common
    trading days).
    """

    from_date: str | None = None
    to_date: str | None = None
    seconds_per_day: float = DEFAULT_REPLAY_SECONDS_PER_DAY
    break_seconds: float = DEFAULT_REPLAY_BREAK_SECONDS
    loop: bool = True


def _read_clamped_seconds(name: str, default: float, low: float, high: float) -> float:
    """Parse a seconds env var: empty → default, numeric → clamped [low, high].

    Unparsable / non-finite values log a warning and use the default — the
    ``FINALLY_AKSHARE_POLL_SECONDS`` precedent (misconfigured *tuning* knobs
    degrade loudly; only structurally-invalid config like bad dates fails
    startup).
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r — using %.0fs", name, raw, default)
        return default
    if not math.isfinite(value):
        logger.warning("Invalid %s=%r — using %.0fs", name, raw, default)
        return default
    return min(max(value, low), high)


def read_replay_env() -> ReplayConfig:
    """Read the four FINALLY_REPLAY_* env vars ONCE at startup (D3 §2).

    - FINALLY_REPLAY_FROM / FINALLY_REPLAY_TO: ISO dates. Both set → explicit
      window; both unset → auto (most recent common-coverage days). Setting
      exactly one, an unparsable date, or from > to is explicit
      misconfiguration and raises ValueError (fails startup — the
      resolve_live_source style).
    - FINALLY_REPLAY_SECONDS_PER_DAY: default 120, clamped 30..600.
    - FINALLY_REPLAY_BREAK_SECONDS: default 5, clamped 2..60.
    - FINALLY_REPLAY_LOOP: default true; "0"/"false"/"no"/"off" (any case)
      disable looping.
    """
    from_raw = os.environ.get(REPLAY_FROM_ENV, "").strip()
    to_raw = os.environ.get(REPLAY_TO_ENV, "").strip()
    if bool(from_raw) != bool(to_raw):
        raise ValueError(
            f"{REPLAY_FROM_ENV} and {REPLAY_TO_ENV} must be set together "
            "(both ISO dates) or both left empty for the automatic window"
        )
    from_date: str | None = None
    to_date: str | None = None
    if from_raw:
        parsed: dict[str, str] = {}
        for name, raw in ((REPLAY_FROM_ENV, from_raw), (REPLAY_TO_ENV, to_raw)):
            try:
                parsed[name] = date_type.fromisoformat(raw).isoformat()
            except ValueError as exc:
                raise ValueError(
                    f"{name} must be an ISO date (YYYY-MM-DD), got {raw!r}"
                ) from exc
        from_date = parsed[REPLAY_FROM_ENV]
        to_date = parsed[REPLAY_TO_ENV]
        if from_date > to_date:
            raise ValueError(
                f"{REPLAY_FROM_ENV} ({from_date}) must not be after "
                f"{REPLAY_TO_ENV} ({to_date})"
            )
    seconds_per_day = _read_clamped_seconds(
        REPLAY_SECONDS_ENV,
        DEFAULT_REPLAY_SECONDS_PER_DAY,
        MIN_REPLAY_SECONDS_PER_DAY,
        MAX_REPLAY_SECONDS_PER_DAY,
    )
    break_seconds = _read_clamped_seconds(
        REPLAY_BREAK_ENV,
        DEFAULT_REPLAY_BREAK_SECONDS,
        MIN_REPLAY_BREAK_SECONDS,
        MAX_REPLAY_BREAK_SECONDS,
    )
    loop_raw = os.environ.get(REPLAY_LOOP_ENV, "").strip().lower()
    loop = loop_raw not in _FALSY
    return ReplayConfig(
        from_date=from_date,
        to_date=to_date,
        seconds_per_day=seconds_per_day,
        break_seconds=break_seconds,
        loop=loop,
    )


def replay_seed(ticker: str, date: str) -> int:
    """Deterministic RNG seed for one (ticker, date) replay day.

    CRC32 of a stable composite key — like ``spread_bps_for``, NOT the
    seed-salted built-in ``hash()``, so the same day replays identically
    across processes and runs.
    """
    return zlib.crc32(f"{ticker}:{date}".encode("utf-8"))


def build_day_path(bar: Mapping, n_points: int, rng: random.Random) -> list[float]:
    """Synthesize one day's intraday tick path from a daily OHLC bar (pure).

    Skeleton: a piecewise-linear walk through the day's anchor prices —
    bullish bars (close >= open) go O → L → H → C, bearish bars go
    O → H → L → C — plus per-point micro-noise drawn from ``rng`` (amplitude
    <= ``NOISE_FRACTION`` of the local price), every point clamped inside
    ``[low, high]``.

    Guarantees (the settlement machinery depends on the last one):

    - The returned path has exactly ``n_points`` points, all in
      ``[low, high]``.
    - The LAST point is EXACTLY ``close`` (so the existing session-close
      stamp is the real historical close).
    - ``high`` and ``low`` are each touched EXACTLY once for ``n_points >= 3``
      generic bars; when open/close themselves sit on an extreme, that
      end-point IS the single touch (no duplicate interior anchor).
    - Deterministic: same ``bar``/``n_points``/rng seed → same path.

    Degradations (documented, pinned by tests): anchors are dropped
    front-first when ``n_points`` is too small to hold them all (close and
    the extremes outrank the open), so ``n_points == 2`` on a generic bar
    yields ``[second_extreme, close]``. ``n_points == 1`` → ``[close]``;
    ``n_points <= 0`` → ``[]``. A zero-amplitude bar (high == low) returns a
    constant close-priced path. Inconsistent extremes are repaired to
    bracket open/close.
    """
    o = float(bar["open"])
    c = float(bar["close"])
    h = max(float(bar["high"]), o, c)
    low = min(float(bar["low"]), o, c)
    if n_points <= 0:
        return []
    if n_points == 1:
        return [c]
    span = h - low
    if span <= 0:
        return [c] * n_points

    bullish = c >= o
    first_extreme, second_extreme = (low, h) if bullish else (h, low)
    # Interior extreme anchors — dropped when the open or close already
    # touches that extreme (keeps the exactly-once guarantee).
    interior = [e for e in (first_extreme, second_extreme) if o != e and c != e]
    anchors = [o, *interior, c]
    while len(anchors) > n_points:  # tiny paths: drop from the front
        anchors.pop(0)

    k = len(anchors)
    idxs = [round(j * (n_points - 1) / (k - 1)) for j in range(k)]
    idxs[-1] = n_points - 1
    for j in range(1, k):  # enforce strictly increasing anchor slots
        if idxs[j] <= idxs[j - 1]:
            idxs[j] = idxs[j - 1] + 1
    for j in range(k - 2, -1, -1):
        if idxs[j] >= idxs[j + 1]:
            idxs[j] = idxs[j + 1] - 1

    path = [0.0] * n_points
    for j in range(k - 1):
        ia, ib = idxs[j], idxs[j + 1]
        pa, pb = anchors[j], anchors[j + 1]
        for i in range(ia, ib + 1):
            t = 0.0 if ib == ia else (i - ia) / (ib - ia)
            path[i] = pa + (pb - pa) * t
    # Stamp anchor slots with their exact values: interpolation at t=1.0 can
    # land 1 ulp off when a segment spans more than a 2x ratio, and settlement
    # rides on the day's final tick being EXACTLY the real close.
    for j, slot in enumerate(idxs):
        path[slot] = anchors[j]

    # Micro-noise on non-anchor points only, clamped STRICTLY inside
    # (low, high) so the extremes are touched only at their anchors.
    anchor_slots = set(idxs)
    eps = span * 1e-6
    for i in range(n_points):
        if i in anchor_slots:
            continue
        base = path[i]
        noisy = base + rng.uniform(-1.0, 1.0) * NOISE_FRACTION * base
        path[i] = min(max(noisy, low + eps), h - eps)
    return path


def build_day_volumes(total_volume: float, n_points: int, rng: random.Random) -> list[float]:
    """Distribute a day's total volume over ``n_points`` ticks (pure).

    Each tick gets an even share jittered by a seeded uniform ±30% draw,
    then the whole series is scaled and cumulatively rounded to whole units
    so the sum is EXACTLY ``round(total_volume)`` (conservation ± the
    rounding of the daily total). Non-positive totals yield all-zero ticks.
    The tail hold-at-close segment is NOT part of this series — the source
    writes volume 0 there.
    """
    if n_points <= 0:
        return []
    total = float(total_volume)
    if not total > 0:
        return [0.0] * n_points
    weights = [rng.uniform(1.0 - VOLUME_JITTER, 1.0 + VOLUME_JITTER) for _ in range(n_points)]
    weight_sum = sum(weights)
    target = round(total)
    out: list[float] = []
    cumulative = 0.0
    assigned = 0
    for j, weight in enumerate(weights):
        if j == n_points - 1:
            increment = target - assigned  # exact conservation
        else:
            cumulative += weight / weight_sum * target
            increment = round(cumulative) - assigned
        assigned += increment
        out.append(float(max(0, increment)))
    return out


# ---------------------------------------------------------------------------
# Window resolution + startup validation/injection (D3 §2)
# ---------------------------------------------------------------------------


def common_trading_days(
    conn,
    market: str,
    tickers: list[str],
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[str]:
    """Dates on which EVERY ticker has a stored daily bar, ascending.

    The replay calendar: 公共覆盖 across the market's default equity
    universe. Optional inclusive date bounds narrow the scan.
    """
    if not tickers:
        return []
    placeholders = ", ".join("?" for _ in tickers)
    sql = (
        f"SELECT date FROM daily_bars WHERE market = ? AND ticker IN ({placeholders})"
    )
    params: list[object] = [market, *tickers]
    if from_date is not None:
        sql += " AND date >= ?"
        params.append(from_date)
    if to_date is not None:
        sql += " AND date <= ?"
        params.append(to_date)
    sql += " GROUP BY date HAVING COUNT(DISTINCT ticker) = ? ORDER BY date"
    params.append(len(tickers))
    return [row["date"] for row in conn.execute(sql, params).fetchall()]


def union_trading_days(
    conn,
    market: str,
    tickers: list[str],
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[str]:
    """Dates on which AT LEAST ONE ticker has a stored daily bar, ascending.

    The source's day calendar inside a resolved window: a single ticker's
    missing day (停牌/suspension) freezes that ticker for the day instead of
    shrinking everyone's calendar.
    """
    if not tickers:
        return []
    placeholders = ", ".join("?" for _ in tickers)
    sql = (
        f"SELECT DISTINCT date FROM daily_bars "
        f"WHERE market = ? AND ticker IN ({placeholders})"
    )
    params: list[object] = [market, *tickers]
    if from_date is not None:
        sql += " AND date >= ?"
        params.append(from_date)
    if to_date is not None:
        sql += " AND date <= ?"
        params.append(to_date)
    sql += " ORDER BY date"
    return [row["date"] for row in conn.execute(sql, params).fetchall()]


def resolve_replay_window(
    conn, market: str, tickers: list[str], config: ReplayConfig
) -> list[str]:
    """The COMMON-coverage trading days for a config (startup validation).

    Explicit ``from_date``/``to_date`` → all common trading days inside the
    window. Auto (both None) → the most recent
    ``DEFAULT_REPLAY_WINDOW_DAYS`` common trading days. May return fewer
    than 2 days — callers decide whether that fails startup. The source's
    actual day calendar is the UNION of covered dates inside these bounds
    (see :func:`union_trading_days`), so partially-covered days replay with
    the uncovered tickers frozen.
    """
    if config.from_date is not None:
        return common_trading_days(conn, market, tickers, config.from_date, config.to_date)
    return common_trading_days(conn, market, tickers)[-DEFAULT_REPLAY_WINDOW_DAYS:]


def replay_universe_tickers(universe: MarketUniverse) -> list[str]:
    """The equity default-watchlist tickers the replay calendar is based on.

    Crypto has no daily bars (documented: replay covers equities with
    history only), so it never constrains the common-coverage calendar.
    """
    return [
        ticker
        for ticker in universe.default_watchlist
        if universe.asset_class_for(ticker) == "equity"
    ]


def _window_bar_count(conn, market: str, ticker: str, config: ReplayConfig) -> int:
    """Stored-bar count for one ticker inside the configured window."""
    sql = "SELECT COUNT(*) AS n FROM daily_bars WHERE market = ? AND ticker = ?"
    params: list[object] = [market, ticker]
    if config.from_date is not None:
        sql += " AND date >= ?"
        params.append(config.from_date)
    if config.to_date is not None:
        sql += " AND date <= ?"
        params.append(config.to_date)
    return int(conn.execute(sql, params).fetchone()["n"])


def _coverage_summary(conn, market: str, tickers: list[str]) -> str:
    """One-line-per-ticker coverage summary for the startup error message."""
    lines: list[str] = []
    for ticker in tickers:
        row = conn.execute(
            "SELECT MIN(date) AS min_date, MAX(date) AS max_date, COUNT(*) AS n "
            "FROM daily_bars WHERE market = ? AND ticker = ?",
            (market, ticker),
        ).fetchone()
        if row["n"]:
            lines.append(f"{ticker}: {row['min_date']}..{row['max_date']} ({row['n']} bars)")
        else:
            lines.append(f"{ticker}: no stored bars")
    return "; ".join(lines)


def ensure_replay_startup_data(
    db_path: str,
    profile,
    config: ReplayConfig | None = None,
    providers: dict | None = None,
) -> list[str]:
    """Validate (and if needed sample-inject) replay data at startup (D3 §2).

    Called by main.py's replay branch BEFORE the market source is created:

    1. Resolve the replay calendar (common trading days of the profile's
       default equity universe inside the configured window).
    2. If fewer than 2 common days, synchronously inject the SAMPLE provider
       (``sync_daily_bars`` with ``source="sample"`` — zero network, zero
       optional imports) for exactly the tickers lacking window coverage,
       then re-resolve. Tickers with existing (possibly real) data are never
       overwritten.
    3. Still fewer than 2 common days (window outside the sample range,
       mixed calendars, ...) → ValueError whose message carries the current
       per-ticker coverage and how to fix it (sync real history first or
       change FINALLY_REPLAY_FROM/TO). Never fetches from the network.

    Returns the resolved calendar (ascending ISO dates) for logging.
    ``config``/``providers`` are injectable for tests; defaults read the env
    and use the committed sample CSVs.
    """
    from .history import SampleProvider, sync_daily_bars

    replay_config = config if config is not None else read_replay_env()
    market = profile.key
    base_tickers = replay_universe_tickers(profile.universe)
    if not base_tickers:
        raise ValueError(
            "FINALLY_LIVE_SOURCE=replay requires at least one equity ticker "
            f"in the '{market}' default universe"
        )
    provider_map = providers if providers is not None else {"sample": SampleProvider(market)}

    conn = get_conn(db_path)
    try:
        days = resolve_replay_window(conn, market, base_tickers, replay_config)
        if len(days) < 2:
            lacking = [
                ticker
                for ticker in base_tickers
                if _window_bar_count(conn, market, ticker, replay_config) < 2
            ]
            if lacking:
                logger.info(
                    "Replay startup: injecting sample daily bars for %d tickers "
                    "lacking window coverage: %s",
                    len(lacking),
                    ", ".join(lacking),
                )
                sync_daily_bars(
                    conn,
                    market=market,
                    tickers=lacking,
                    source="sample",
                    years=10,  # the whole committed sample series
                    providers=provider_map,
                )
                days = resolve_replay_window(conn, market, base_tickers, replay_config)
        if len(days) < 2:
            window_text = (
                f"{replay_config.from_date}..{replay_config.to_date}"
                if replay_config.from_date is not None
                else "auto (most recent common coverage)"
            )
            raise ValueError(
                "FINALLY_LIVE_SOURCE=replay needs at least 2 common trading "
                f"days across the '{market}' default universe in the window "
                f"{window_text}, found {len(days)}. Current coverage — "
                f"{_coverage_summary(conn, market, base_tickers)}. Sync more "
                "history first (POST /api/market/history/sync) or adjust "
                f"{REPLAY_FROM_ENV}/{REPLAY_TO_ENV}."
            )
        return days
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The data source
# ---------------------------------------------------------------------------


class ReplayDataSource(MarketDataSource):
    """MarketDataSource that replays stored daily bars as live sessions.

    Shape mirrors :class:`SimulatorDataSource`: a background asyncio task
    runs one synchronous ``_step()`` per ``update_interval`` and writes
    ticks into the shared :class:`PriceCache` through ``cache.update`` —
    the same single funnel every other source uses, so SSE, bars, events,
    price limits, trading, and settlement all work unchanged.

    Session alignment (no new hooks): ``_step`` polls the session clock —
    closed/midday → frozen (no writes; the intraday path resumes exactly
    where it stopped, so the CN am+pm halves splice seamlessly); a
    ``session_id`` change → the next replay day (wrapping to the first day
    when ``config.loop``, else freezing ``finished``). Because the clock
    loop runs its settle/roll hooks synchronously with the transition, this
    source always observes a new session AFTER the roll has stamped the real
    close as prev_close.
    """

    def __init__(
        self,
        price_cache,
        *,
        db_path: str,
        market: str,
        session_clock: SessionClock | None = None,
        universe: MarketUniverse | None = None,
        update_interval: float = 0.5,
        config: ReplayConfig | None = None,
    ) -> None:
        self._cache = price_cache
        self._db_path = db_path
        self._market = market
        self._session_clock = session_clock
        self._universe = universe
        self._interval = update_interval
        self._config = config if config is not None else ReplayConfig()
        self._task: asyncio.Task | None = None
        # Replay state — the lock guards the fields the status snapshot
        # reads (day_index/finished) against concurrent readers.
        self._lock = threading.Lock()
        self._days: list[str] = []  # the replay calendar (ascending ISO dates)
        self._tickers: list[str] = []  # tickers with loaded coverage
        self._bars: dict[str, dict[str, dict]] = {}  # ticker -> {date: bar row}
        self._prev_closes: dict[str, dict[str, float]] = {}  # ticker -> {date: prev close}
        self._seed_closes: dict[str, float] = {}  # ticker -> pre-window close
        self._sources: set[str] = set()  # distinct daily_bars sources loaded
        self._ignored: set[str] = set()  # no-coverage tickers (announced once)
        self._day_index = 0
        self._tick_index = 0
        self._finished = False
        self._last_session_id: int | None = None
        self._paths: dict[str, list[float]] = {}
        self._volumes: dict[str, list[float]] = {}
        # Running (high, low) of ticks WRITTEN so far for the current replay
        # day — passed explicitly to cache.update so gap days never report an
        # extreme the historical day never traded (the roll baseline would
        # otherwise seed the running extremes with the prior close).
        self._day_extremes: dict[str, tuple[float, float]] = {}
        # Path length: 90% of the expected open-window tick count; the tail
        # 10% holds the close at zero volume/noise so the day's last written
        # tick is ALWAYS the real close regardless of sleep jitter.
        expected_ticks = max(1, round(self._config.seconds_per_day / update_interval))
        self._active_points = max(2, int(expected_ticks * ACTIVE_PATH_FRACTION))

    # --- MarketDataSource lifecycle ---

    async def start(self, tickers: list[str]) -> None:
        conn = get_conn(self._db_path)
        try:
            candidates = (
                replay_universe_tickers(self._universe)
                if self._universe is not None
                else [t.strip().upper() for t in tickers]
            )
            # The calendar comes from the tickers that actually HAVE stored
            # bars in the window — a no-coverage ticker (crypto, unsynced
            # user adds) must not empty the common-days intersection.
            base = [
                ticker
                for ticker in candidates
                if _window_bar_count(conn, self._market, ticker, self._config) > 0
            ]
            # The day calendar is the UNION of covered dates inside the
            # window, so a single suspended ticker freezes for the day
            # instead of dropping the day for everyone. Explicit windows use
            # the configured bounds; the auto window anchors its bounds on
            # COMMON coverage (identical to main.py's startup validation).
            if self._config.from_date is not None:
                self._days = union_trading_days(
                    conn,
                    self._market,
                    base,
                    self._config.from_date,
                    self._config.to_date,
                )
            else:
                bounds = resolve_replay_window(conn, self._market, base, self._config)
                self._days = (
                    union_trading_days(conn, self._market, base, bounds[0], bounds[-1])
                    if bounds
                    else []
                )
            if len(self._days) < 2:
                raise ValueError(
                    "Replay window has fewer than 2 common trading days — "
                    "main.py's startup validation should have injected or "
                    "failed before source creation"
                )
            skipped: list[str] = []
            for ticker in tickers:
                if not self._load_ticker(conn, ticker.strip().upper()):
                    skipped.append(ticker.strip().upper())
            if skipped:  # one-time log; silently absent from the cache/SSE
                self._ignored.update(skipped)
                logger.info(
                    "Replay: %d tickers have no daily-bar coverage in "
                    "%s..%s and are excluded: %s",
                    len(skipped),
                    self._days[0],
                    self._days[-1],
                    ", ".join(sorted(skipped)),
                )
        finally:
            conn.close()

        # First-frame seed: the close of the trading day BEFORE the window.
        # The cache's first write fixes prev_close (simulator seed-write
        # pattern), so day 1 opens with a real day-change vs the real prior
        # close.
        for ticker in self._tickers:
            seed = self._seed_closes[ticker]
            self._write_tick(ticker, seed, prev_close=seed, volume=0.0)

        if self._session_clock is not None:
            self._last_session_id = self._session_clock.session_id
        self._build_day(0)
        self._task = asyncio.create_task(self._run_loop(), name="replay-loop")
        logger.info(
            "Replay started: %d tickers over %d trading days (%s..%s, "
            "%.0fs/day, loop=%s, sources=%s)",
            len(self._tickers),
            len(self._days),
            self._days[0],
            self._days[-1],
            self._config.seconds_per_day,
            self._config.loop,
            self._source_hint(),
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Replay stopped")

    async def add_ticker(self, ticker: str) -> None:
        symbol = ticker.strip().upper()
        if symbol in self._tickers:
            return
        if symbol in self._ignored:
            return  # already announced once — stay silent
        conn = get_conn(self._db_path)
        try:
            loaded = self._load_ticker(conn, symbol)
        finally:
            conn.close()
        if not loaded:
            self._ignored.add(symbol)
            logger.info(
                "Replay: %s has no daily-bar coverage in %s..%s — ignored",
                symbol,
                self._days[0] if self._days else "?",
                self._days[-1] if self._days else "?",
            )
            return
        # Seed immediately (quotable right away, like the simulator): the
        # ticker's real prev close for the CURRENT replay day.
        current = self._current_date()
        seed = self._prev_closes[symbol].get(current, self._seed_closes[symbol])
        self._write_tick(symbol, seed, prev_close=seed, volume=0.0)
        self._build_ticker_day(symbol, current)
        logger.info("Replay: added ticker %s", symbol)

    async def remove_ticker(self, ticker: str) -> None:
        symbol = ticker.strip().upper()
        if symbol in self._tickers:
            self._tickers.remove(symbol)
        self._bars.pop(symbol, None)
        self._prev_closes.pop(symbol, None)
        self._seed_closes.pop(symbol, None)
        self._paths.pop(symbol, None)
        self._volumes.pop(symbol, None)
        self._cache.remove(symbol)
        logger.info("Replay: removed ticker %s", symbol)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- Status snapshot (D3 §3) ---

    def snapshot(self) -> dict:
        """Thread-safe status snapshot for GET /api/market/replay."""
        with self._lock:
            day_index = self._day_index
            finished = self._finished
        days = self._days
        return {
            "active": True,
            "from": days[0] if days else None,
            "to": days[-1] if days else None,
            "current_date": days[day_index] if days else None,
            "day_index": day_index,
            "total_days": len(days),
            "seconds_per_day": self._config.seconds_per_day,
            "loop": self._config.loop,
            "finished": finished,
            "source_hint": self._source_hint(),
        }

    # --- Internals ---

    def _source_hint(self) -> str:
        if not self._sources:
            return "none"
        if len(self._sources) == 1:
            return next(iter(self._sources))
        return "mixed"

    def _current_date(self) -> str:
        with self._lock:
            return self._days[self._day_index]

    def _load_ticker(self, conn, symbol: str) -> bool:
        """Load one ticker's window bars + pre-window close. False = no coverage."""
        if not self._days:
            return False
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume, source FROM daily_bars "
            "WHERE market = ? AND ticker = ? AND date >= ? AND date <= ? "
            "ORDER BY date",
            (self._market, symbol, self._days[0], self._days[-1]),
        ).fetchall()
        if not rows:
            return False
        bars_by_date = {row["date"]: dict(row) for row in rows}
        prev_row = conn.execute(
            "SELECT close FROM daily_bars WHERE market = ? AND ticker = ? "
            "AND date < ? ORDER BY date DESC LIMIT 1",
            (self._market, symbol, self._days[0]),
        ).fetchone()
        # No bar before the window (window starts at the series head): fall
        # back to the first day's open — day 1 then opens flat.
        seed_close = (
            float(prev_row["close"]) if prev_row is not None else float(rows[0]["open"])
        )
        prev_close_by_date: dict[str, float] = {}
        running = seed_close
        for day in self._days:
            prev_close_by_date[day] = running
            bar = bars_by_date.get(day)
            if bar is not None:
                running = float(bar["close"])
        self._bars[symbol] = bars_by_date
        self._prev_closes[symbol] = prev_close_by_date
        self._seed_closes[symbol] = seed_close
        self._sources.update(row["source"] for row in rows)
        self._tickers.append(symbol)
        return True

    def _build_ticker_day(self, symbol: str, date: str) -> bool:
        """(Re)build one ticker's path+volumes for a date. False = no bar/failed."""
        bar = self._bars.get(symbol, {}).get(date)
        if bar is None:  # suspended that day — frozen, no writes (停牌)
            self._paths.pop(symbol, None)
            self._volumes.pop(symbol, None)
            return False
        try:
            rng = random.Random(replay_seed(symbol, date))
            path = build_day_path(bar, self._active_points, rng)
            volumes = build_day_volumes(bar["volume"], self._active_points, rng)
        except Exception:
            # Single-ticker failure: drop the ticker, keep the replay alive.
            logger.exception(
                "Replay: path construction failed for %s on %s — dropping ticker",
                symbol,
                date,
            )
            if symbol in self._tickers:
                self._tickers.remove(symbol)
            self._paths.pop(symbol, None)
            self._volumes.pop(symbol, None)
            return False
        self._paths[symbol] = path
        self._volumes[symbol] = volumes
        return True

    def _build_day(self, day_index: int) -> None:
        date = self._days[day_index]
        self._paths = {}
        self._volumes = {}
        self._day_extremes = {}
        for symbol in list(self._tickers):
            self._build_ticker_day(symbol, date)
        self._tick_index = 0

    def _advance_day(self) -> None:
        """Move to the next replay day; wrap (loop) or freeze (finished)."""
        with self._lock:
            if self._finished:
                return
            next_index = self._day_index + 1
            if next_index >= len(self._days):
                if not self._config.loop:
                    self._finished = True
                    logger.info(
                        "Replay finished at %s (loop disabled) — prices frozen",
                        self._days[self._day_index],
                    )
                    return
                next_index = 0  # wrap: settle/roll already ran on the transition
            self._day_index = next_index
        self._build_day(next_index)
        logger.info(
            "Replay: day %d/%d (%s)",
            next_index + 1,
            len(self._days),
            self._days[next_index],
        )

    def _write_tick(
        self,
        ticker: str,
        price: float,
        *,
        prev_close: float,
        volume: float,
        day_high: float | None = None,
        day_low: float | None = None,
    ) -> None:
        """Write one replay tick (price + explicit real prev_close + quote).

        The explicit ``prev_close`` (the AKShare real-data precedent) keeps
        the session baseline on the REAL previous close in every case —
        including the loop-wrap day, whose true prev close is the pre-window
        close rather than the settled last-day close. The CN price-limit
        band derives from it inside ``cache.update``, so 涨跌停 follows the
        real 昨收 automatically.
        """
        bid, ask = compute_quote(ticker, price)
        self._cache.update(
            ticker=ticker,
            price=price,
            prev_close=prev_close,
            volume=volume,
            bid=bid,
            ask=ask,
            day_high=day_high,
            day_low=day_low,
        )

    def _step(self) -> None:
        """One loop iteration: session alignment + at most one tick per ticker.

        Mirrors the simulator's is_open polling: closed/midday → no writes
        (the path resumes at the same tick index when the session resumes);
        a session_id change → next replay day. The intraday path advances by
        ticks WRITTEN, not wall clock. Past the active path (the tail ~10%
        of the open window) the close is re-stamped at zero volume, so the
        day's final written tick is exactly the real close for settlement.
        """
        clock = self._session_clock
        if clock is not None:
            session_id = clock.session_id
            if self._last_session_id is None:
                self._last_session_id = session_id
            elif session_id != self._last_session_id:
                self._last_session_id = session_id
                self._advance_day()
            if self._finished or not clock.is_open:
                return
        elif self._finished:
            return
        date = self._current_date()
        index = self._tick_index
        for symbol, path in self._paths.items():
            if index < len(path):
                price = path[index]
                volume = self._volumes[symbol][index]
            else:
                price = path[-1]  # tail: hold the real close, zero volume
                volume = 0.0
            hi, lo = self._day_extremes.get(symbol, (price, price))
            hi, lo = max(hi, price), min(lo, price)
            self._day_extremes[symbol] = (hi, lo)
            self._write_tick(
                symbol,
                price,
                prev_close=self._prev_closes[symbol][date],
                volume=volume,
                day_high=hi,
                day_low=lo,
            )
        self._tick_index = index + 1

    async def _run_loop(self) -> None:
        """Core loop: one _step per interval; errors are logged, never fatal."""
        while True:
            try:
                self._step()
            except Exception:
                logger.exception("Replay step failed")
            await asyncio.sleep(self._interval)
