"""GBM-based market simulator."""

from __future__ import annotations

import asyncio
import logging
import math
import random
import zlib

import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource
from .seed_prices import (
    CORRELATION_GROUPS,
    CROSS_GROUP_CORR,
    DEFAULT_PARAMS,
    INTRA_CRYPTO_CORR,
    INTRA_FINANCE_CORR,
    INTRA_TECH_CORR,
    SEED_PRICES,
    TICKER_PARAMS,
    TSLA_CORR,
    asset_class_for,
    sector_for,
)
from .session import SessionClock
from .universe import MarketUniverse

logger = logging.getLogger(__name__)

# Sector-correlated event bursts (M3.2b): when a random event shocks a ticker,
# with this probability every same-sector peer is shocked too, by a fraction
# of the source move (same sign, per-peer jitter).
BURST_PROBABILITY = 0.35
BURST_FRACTION_MIN = 0.25
BURST_FRACTION_MAX = 0.50

# Per-tick volume: lognormal draw shared by all tickers.
# median = e^9.2 ~= 9,900 shares; sigma 0.8 puts the 2-sigma range at roughly
# 2k-49k, so typical values land in the 1k-100k band and vary tick to tick.
VOLUME_LOG_MEAN = 9.2
VOLUME_LOG_SIGMA = 0.8

# Quoted spread bounds in basis points (deterministic per ticker).
MIN_SPREAD_BPS = 1
MAX_SPREAD_BPS = 5


def spread_bps_for(ticker: str) -> float:
    """Deterministic per-ticker quoted spread in basis points (1-5 bp).

    Derived from a stable CRC32 hash of the ticker so the spread is fixed for
    the life of the process (and across processes — CRC32 is not seed-salted
    like Python's built-in hash()).
    """
    span = MAX_SPREAD_BPS - MIN_SPREAD_BPS + 1
    return float(MIN_SPREAD_BPS + zlib.crc32(ticker.encode("utf-8")) % span)


def compute_quote(ticker: str, price: float) -> tuple[float, float]:
    """Best bid/ask for a price using the ticker's fixed spread.

    bid = price * (1 - spread/2), ask = price * (1 + spread/2), both rounded
    to 2dp. After rounding, a half-spread smaller than half a cent would
    collapse onto the price, so each side is pushed at least one cent away —
    guaranteeing bid < price < ask for prices >= $1.
    """
    half_spread = price * spread_bps_for(ticker) / 2.0 / 10_000.0
    bid = round(price - half_spread, 2)
    ask = round(price + half_spread, 2)
    rounded_price = round(price, 2)
    if bid >= rounded_price:
        bid = round(rounded_price - 0.01, 2)
    if ask <= rounded_price:
        ask = round(rounded_price + 0.01, 2)
    return bid, ask


def draw_volume() -> float:
    """Per-tick traded volume: a lognormal draw (whole shares, > 0)."""
    return float(max(1, round(random.lognormvariate(VOLUME_LOG_MEAN, VOLUME_LOG_SIGMA))))


def compute_peer_shocks(
    ticker: str,
    shock_magnitude: float,
    shock_sign: int,
    candidates: list[str],
    rng: random.Random | None = None,
) -> dict[str, float]:
    """Sector-correlated burst for one random event (M3.2b).

    Given a random event that just shocked ``ticker`` by
    ``shock_sign * shock_magnitude`` (fractional, e.g. +0.03 for +3%), decide
    whether the shock cascades to the ticker's sector and compute each peer's
    shock. The burst fires with probability ``BURST_PROBABILITY``; when it
    does, EVERY same-sector peer among ``candidates`` (excluding ``ticker``
    itself) is shocked by 25-50% of the source magnitude, same sign — the
    fraction is drawn independently per peer (the jitter).

    Tickers in the "other" bucket (unknown/user-added) have no meaningful
    sector, so they never burst.

    Deterministic-testable: pass a seeded ``random.Random`` (or a fake with
    ``random()``/``uniform()``) as ``rng`` to force or suppress the burst;
    defaults to the module-level ``random``. Draw order: one ``random()`` for
    the fire decision, then one ``uniform(BURST_FRACTION_MIN,
    BURST_FRACTION_MAX)`` per peer in ``candidates`` order.

    Returns {peer: signed fractional shock}; empty dict when no burst fires
    or the ticker has no sector peers.
    """
    r = rng if rng is not None else random
    sector = sector_for(ticker)
    if sector == "other":
        return {}
    peers = [t for t in candidates if t != ticker and sector_for(t) == sector]
    if not peers:
        return {}
    if r.random() >= BURST_PROBABILITY:
        return {}
    return {
        peer: shock_sign
        * shock_magnitude
        * r.uniform(BURST_FRACTION_MIN, BURST_FRACTION_MAX)
        for peer in peers
    }


class GBMSimulator:
    """Geometric Brownian Motion simulator for correlated stock prices.

    Math:
        S(t+dt) = S(t) * exp((mu - sigma^2/2) * dt + sigma * sqrt(dt) * Z)

    Where:
        S(t)   = current price
        mu     = annualized drift (expected return)
        sigma  = annualized volatility
        dt     = time step as fraction of a trading year
        Z      = correlated standard normal random variable

    The tiny dt (~8.5e-8 for 500ms ticks over 252 trading days * 6.5h/day)
    produces sub-cent moves per tick that accumulate naturally over time.
    """

    # 500ms expressed as a fraction of a trading year
    # 252 trading days * 6.5 hours/day * 3600 seconds/hour = 5,896,800 seconds
    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600  # 5,896,800
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR  # ~8.48e-8

    def __init__(
        self,
        tickers: list[str],
        dt: float = DEFAULT_DT,
        event_probability: float = 0.001,
        rng: random.Random | None = None,
        universe: MarketUniverse | None = None,
    ) -> None:
        self._dt = dt
        self._event_prob = event_probability
        # Random source for the event/burst mechanism (NOT the GBM draws,
        # which stay on numpy). Injectable so tests can force events and
        # sector bursts deterministically; defaults to the module-level
        # ``random`` (same behavior as before).
        self._rng = rng if rng is not None else random
        # Optional market universe (CN-1): when provided, seed prices, GBM
        # params, and the correlation structure come from it instead of the
        # module-level US constants. None reproduces the pre-CN-1 behavior
        # exactly.
        self._universe = universe

        # Per-ticker state
        self._tickers: list[str] = []
        self._prices: dict[str, float] = {}
        self._params: dict[str, dict[str, float]] = {}

        # Cholesky decomposition of the correlation matrix (for correlated moves)
        self._cholesky: np.ndarray | None = None

        # Initialize all starting tickers
        for ticker in tickers:
            self._add_ticker_internal(ticker)
        self._rebuild_cholesky()

    # --- Public API ---

    def step(self, only: set[str] | None = None) -> dict[str, float]:
        """Advance tickers by one time step. Returns {ticker: new_price}.

        This is the hot path — called every 500ms. Keep it fast.

        Args:
            only: When given, only tickers in this set advance (and appear in
                the result); all others keep their current price untouched.
                Used while the session is closed to tick crypto 24/7 while
                equity prices stay frozen (M3.1/M3.3). None advances all.
        """
        n = len(self._tickers)
        if n == 0:
            return {}

        # Generate n independent standard normal draws
        z_independent = np.random.standard_normal(n)

        # Apply Cholesky to get correlated draws
        if self._cholesky is not None:
            z_correlated = self._cholesky @ z_independent
        else:
            z_correlated = z_independent

        result: dict[str, float] = {}
        pending_burst_shocks: list[tuple[str, float]] = []
        for i, ticker in enumerate(self._tickers):
            if only is not None and ticker not in only:
                continue  # Frozen (e.g. equity while the session is closed)
            params = self._params[ticker]
            mu = params["mu"]
            sigma = params["sigma"]

            # GBM: S(t+dt) = S(t) * exp((mu - 0.5*sigma^2)*dt + sigma*sqrt(dt)*Z)
            drift = (mu - 0.5 * sigma**2) * self._dt
            diffusion = sigma * math.sqrt(self._dt) * z_correlated[i]
            self._prices[ticker] *= math.exp(drift + diffusion)

            # Random event: ~0.1% chance per tick per ticker
            # With 10 tickers at 2 ticks/sec, expect an event ~every 50 seconds
            if self._rng.random() < self._event_prob:
                shock_magnitude = self._rng.uniform(0.02, 0.05)
                shock_sign = self._rng.choice([-1, 1])
                self._prices[ticker] *= 1 + shock_magnitude * shock_sign
                logger.debug(
                    "Random event on %s: %.1f%% %s",
                    ticker,
                    shock_magnitude * 100,
                    "up" if shock_sign > 0 else "down",
                )
                # Sector-correlated burst (M3.2b): the event may cascade to
                # same-sector peers. Staged and applied after the loop so
                # every peer lands in THIS tick's result regardless of
                # ticker iteration order.
                pending_burst_shocks.extend(
                    compute_peer_shocks(
                        ticker,
                        shock_magnitude,
                        shock_sign,
                        self._tickers,
                        rng=self._rng,
                    ).items()
                )

            result[ticker] = round(self._prices[ticker], 2)

        for peer, shock in pending_burst_shocks:
            if only is not None and peer not in only:
                continue  # Never shock a frozen ticker's price silently
            if peer not in self._prices:
                continue
            self._prices[peer] *= 1 + shock
            result[peer] = round(self._prices[peer], 2)
            logger.debug("Sector burst shock on %s: %+.2f%%", peer, shock * 100)

        return result

    def add_ticker(self, ticker: str) -> None:
        """Add a ticker to the simulation. Rebuilds the correlation matrix."""
        if ticker in self._prices:
            return
        self._add_ticker_internal(ticker)
        self._rebuild_cholesky()

    def remove_ticker(self, ticker: str) -> None:
        """Remove a ticker from the simulation. Rebuilds the correlation matrix."""
        if ticker not in self._prices:
            return
        self._tickers.remove(ticker)
        del self._prices[ticker]
        del self._params[ticker]
        self._rebuild_cholesky()

    def get_price(self, ticker: str) -> float | None:
        """Current price for a ticker, or None if not tracked."""
        return self._prices.get(ticker)

    def get_tickers(self) -> list[str]:
        """Return the list of currently tracked tickers."""
        return list(self._tickers)

    # --- Internals ---

    def _add_ticker_internal(self, ticker: str) -> None:
        """Add a ticker without rebuilding Cholesky (for batch initialization)."""
        if ticker in self._prices:
            return
        self._tickers.append(ticker)
        if self._universe is not None:
            seeds = self._universe.seed_prices
            params = self._universe.ticker_params
            defaults = self._universe.default_params
        else:
            seeds, params, defaults = SEED_PRICES, TICKER_PARAMS, DEFAULT_PARAMS
        self._prices[ticker] = seeds.get(ticker, random.uniform(50.0, 300.0))
        self._params[ticker] = params.get(ticker, dict(defaults))

    def _rebuild_cholesky(self) -> None:
        """Rebuild the Cholesky decomposition of the ticker correlation matrix.

        Called whenever tickers are added or removed. O(n^2) but n < 50.
        """
        n = len(self._tickers)
        if n <= 1:
            self._cholesky = None
            return

        # Build the correlation matrix
        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                rho = self._correlation(self._tickers[i], self._tickers[j])
                corr[i, j] = rho
                corr[j, i] = rho

        self._cholesky = np.linalg.cholesky(corr)

    def _correlation(self, t1: str, t2: str) -> float:
        """Pairwise correlation: from the injected universe when present (CN-1),
        otherwise the module-level US map (``_pairwise_correlation``)."""
        if self._universe is not None:
            return self._universe.pairwise_correlation(t1, t2)
        return self._pairwise_correlation(t1, t2)

    @staticmethod
    def _pairwise_correlation(t1: str, t2: str) -> float:
        """Determine correlation between two tickers based on sector grouping.

        Correlation structure:
          - Same tech sector:   0.6
          - Same finance sector: 0.5
          - TSLA with anything: 0.3 (it does its own thing)
          - Cross-sector:       0.3
          - Unknown tickers:    0.3
        """
        tech = CORRELATION_GROUPS["tech"]
        finance = CORRELATION_GROUPS["finance"]
        crypto = CORRELATION_GROUPS["crypto"]

        # TSLA is in tech set but behaves independently
        if t1 == "TSLA" or t2 == "TSLA":
            return TSLA_CORR

        if t1 in tech and t2 in tech:
            return INTRA_TECH_CORR
        if t1 in finance and t2 in finance:
            return INTRA_FINANCE_CORR
        if t1 in crypto and t2 in crypto:
            return INTRA_CRYPTO_CORR

        return CROSS_GROUP_CORR


class SimulatorDataSource(MarketDataSource):
    """MarketDataSource backed by the GBM simulator.

    Runs a background asyncio task that calls GBMSimulator.step() every
    `update_interval` seconds and writes results to the PriceCache.

    Session awareness (M3.1/M3.3): when a ``session_clock`` is provided and
    the market is CLOSED, only crypto tickers advance and write to the cache
    — equity prices freeze at their last value (no cache updates at all, so
    per-ticker records, bars, and the version counter stay put for them).
    Without a clock (or with a 24/7 clock) everything ticks continuously.
    ``add_ticker`` still seeds a first price even while closed so a
    just-watched ticker is immediately quotable in the UI.
    """

    def __init__(
        self,
        price_cache: PriceCache,
        update_interval: float = 0.5,
        event_probability: float = 0.001,
        session_clock: SessionClock | None = None,
        universe: MarketUniverse | None = None,
    ) -> None:
        self._cache = price_cache
        self._interval = update_interval
        self._event_prob = event_probability
        self._session_clock = session_clock
        # Optional market universe (CN-1): forwarded to the GBM simulator
        # (seeds/params/correlations) and used for the closed-session
        # asset-class check. None keeps the module-constant US behavior.
        self._universe = universe
        self._sim: GBMSimulator | None = None
        self._task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        self._sim = GBMSimulator(
            tickers=tickers,
            event_probability=self._event_prob,
            universe=self._universe,
        )
        # Seed the cache with initial prices so SSE has data immediately.
        # This first write also fixes each ticker's session prev_close in the
        # cache to the price the GBM walk starts from (constant thereafter).
        for ticker in tickers:
            price = self._sim.get_price(ticker)
            if price is not None:
                self._write_tick(ticker, price)
        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")
        logger.info("Simulator started with %d tickers", len(tickers))

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Simulator stopped")

    async def add_ticker(self, ticker: str) -> None:
        if self._sim:
            self._sim.add_ticker(ticker)
            # Seed cache immediately so the ticker has a price right away.
            # This first write fixes the ticker's session prev_close to its
            # GBM starting price (constant thereafter).
            price = self._sim.get_price(ticker)
            if price is not None:
                self._write_tick(ticker, price)
            logger.info("Simulator: added ticker %s", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        if self._sim:
            self._sim.remove_ticker(ticker)
        self._cache.remove(ticker)
        logger.info("Simulator: removed ticker %s", ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers() if self._sim else []

    def _write_tick(self, ticker: str, price: float) -> None:
        """Write one simulated tick (price + volume + bid/ask quote) to the cache."""
        bid, ask = compute_quote(ticker, price)
        self._cache.update(
            ticker=ticker,
            price=price,
            volume=draw_volume(),
            bid=bid,
            ask=ask,
        )

    def _active_tickers(self) -> set[str] | None:
        """Tickers allowed to tick right now.

        None means "all" (market open, or no session clock). While the
        session is closed only crypto tickers advance — equities freeze.
        """
        if self._session_clock is None or self._session_clock.is_open:
            return None
        classify = (
            self._universe.asset_class_for if self._universe is not None else asset_class_for
        )
        return {
            ticker
            for ticker in (self._sim.get_tickers() if self._sim else [])
            if classify(ticker) == "crypto"
        }

    async def _run_loop(self) -> None:
        """Core loop: step the simulation, write to cache, sleep."""
        while True:
            try:
                if self._sim:
                    prices = self._sim.step(only=self._active_tickers())
                    for ticker, price in prices.items():
                        self._write_tick(ticker, price)
            except Exception:
                logger.exception("Simulator step failed")
            await asyncio.sleep(self._interval)
