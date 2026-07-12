"""AKShare live spot quotes for real A-share market data (D2 contract §1).

``AkshareLiveSource`` mirrors :class:`MassiveDataSource`'s shape (start/
stop/add_ticker/remove_ticker/_poll_loop/_poll_once): a background task
polls the 东财 full-market snapshot ``ak.stock_zh_a_spot_em()`` every
``FINALLY_AKSHARE_POLL_SECONDS`` (default 15, clamped 5..120 by the
factory), filters the rows down to the tracked ticker set, and writes the
quotes into the shared :class:`PriceCache`.

CORE INVARIANTS (contract §1):
- ``akshare`` is imported LAZILY inside the fetch call (the
  ``_import_akshare`` hook, same pattern as ``app/market/history.py``) and
  the fetcher is injectable — the test suite never touches the network and
  importing this module never loads akshare/pandas.
- The 成交量 column is the session's CUMULATIVE volume: each poll writes the
  delta vs the previous poll (first frame per ticker records 0; negative
  deltas clamp to 0 — mirrors the Massive day-volume differencing).
- 买一/卖一 columns are mapped to bid/ask when present and omitted when
  absent (the cache then falls back to the zero-spread price).
- Failures never kill the loop: a failed poll logs a THROTTLED warning
  (at most one per ``FAILURE_WARN_INTERVAL_SECONDS`` while failures are
  consecutive) and keeps the previous frame's quotes.
- A ticker whose frame is UNCHANGED (same price, no new cumulative volume)
  is not re-stamped: outside trading hours the 东财 snapshot keeps serving
  the closing frame, so quotes freeze at the close and the trade-path
  freshness gate blocks executions on them — the documented closed-session
  behavior for real CN data.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable, Mapping

from .cache import PriceCache
from .interface import MarketDataSource

logger = logging.getLogger(__name__)

# Poll cadence bounds (contract §1): FINALLY_AKSHARE_POLL_SECONDS default 15,
# clamped into [5, 120] by the factory's env parser.
DEFAULT_AKSHARE_POLL_SECONDS = 15.0
AKSHARE_MIN_POLL_SECONDS = 5.0
AKSHARE_MAX_POLL_SECONDS = 120.0

# Throttle for the consecutive-failure warning: while polls keep failing,
# warn at most once per this many seconds (the cache's stale-rejection
# warn mechanism style). A fresh failure streak always warns immediately.
FAILURE_WARN_INTERVAL_SECONDS = 60.0

# 东财 full-market spot columns (ak.stock_zh_a_spot_em DataFrame headers).
COL_CODE = "代码"
COL_PRICE = "最新价"
COL_PREV_CLOSE = "昨收"
COL_DAY_HIGH = "最高"
COL_DAY_LOW = "最低"
COL_VOLUME = "成交量"  # CUMULATIVE session volume — differenced per poll
COL_BID = "买一"  # Mapped when present; the spot_em endpoint may omit them
COL_ASK = "卖一"


def _positive_float(value: object) -> float | None:
    """Return value as a float if it is a positive finite real, else None.

    Mirrors the Massive client's guard and additionally rejects NaN/inf —
    suspended (停牌) rows in the 东财 snapshot carry NaN in every numeric
    column, and '-' placeholders arrive as strings. Returning None lets the
    PriceCache fall back to its carried session state.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def _import_akshare():
    """Lazy akshare import — module-level hook so tests can monkeypatch."""
    import akshare  # noqa: PLC0415 — deliberate lazy import (contract §1)

    return akshare


class AkshareLiveSource(MarketDataSource):
    """MarketDataSource backed by AKShare's 东财 full-market spot snapshot.

    One poll fetches the WHOLE market (~5000 rows) in a single request —
    the tracked-ticker filter happens client-side, so add_ticker never
    changes the request shape. Data is delayed/教学-grade; the CN simulator
    remains the product default (contract §1: explicit opt-in only).
    """

    def __init__(
        self,
        price_cache: PriceCache,
        poll_interval: float = DEFAULT_AKSHARE_POLL_SECONDS,
        fetch_fn: Callable[[], list[Mapping]] | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._cache = price_cache
        self._interval = poll_interval
        self._fetch_fn = fetch_fn
        self._now = now
        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None
        # Cumulative session volume (成交量) seen on the previous poll, per
        # ticker. Used to derive per-poll volume deltas.
        self._prev_cumulative_volume: dict[str, float] = {}
        # Consecutive-failure state for the throttled poll warning.
        self._consecutive_failures = 0
        self._last_failure_warn: float | None = None

    async def start(self, tickers: list[str]) -> None:
        self._tickers = list(tickers)

        # Do an immediate first poll so the cache has data right away
        await self._poll_once()

        self._task = asyncio.create_task(self._poll_loop(), name="akshare-poller")
        logger.info(
            "AKShare poller started: %d tickers, %.1fs interval",
            len(tickers),
            self._interval,
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("AKShare poller stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        if ticker not in self._tickers:
            self._tickers.append(ticker)
            logger.info("AKShare: added ticker %s (will appear on next poll)", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)
        self._prev_cumulative_volume.pop(ticker, None)
        logger.info("AKShare: removed ticker %s", ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- Internal ---

    async def _poll_loop(self) -> None:
        """Poll on interval. First poll already happened in start()."""
        while True:
            await asyncio.sleep(self._interval)
            await self._poll_once()

    async def _poll_once(self) -> None:
        """Execute one poll cycle: fetch the spot snapshot, update cache."""
        if not self._tickers:
            return

        try:
            # The akshare fetch is synchronous (requests + pandas) — run in
            # a thread to avoid blocking the event loop.
            rows = await asyncio.to_thread(self._fetch_spot_rows)
            tracked = set(self._tickers)
            timestamp = float(self._now())
            processed = 0
            for row in rows:
                try:
                    code = str(row.get(COL_CODE, "")).strip().upper()
                    if code not in tracked:
                        continue
                    price = _positive_float(row.get(COL_PRICE))
                    if price is None:
                        # 停牌/placeholder row — keep the previous frame.
                        logger.debug("AKShare: no tradable price for %s", code)
                        continue
                    volume = self._volume_delta(code, row.get(COL_VOLUME))
                    if self._is_frozen_frame(code, price, volume):
                        # Unchanged closing frame (market closed): do not
                        # re-stamp the quote — freshness reflects the tape.
                        continue
                    self._cache.update(
                        ticker=code,
                        price=price,
                        timestamp=timestamp,
                        prev_close=_positive_float(row.get(COL_PREV_CLOSE)),
                        day_high=_positive_float(row.get(COL_DAY_HIGH)),
                        day_low=_positive_float(row.get(COL_DAY_LOW)),
                        volume=volume,
                        bid=_positive_float(row.get(COL_BID)),
                        ask=_positive_float(row.get(COL_ASK)),
                    )
                    processed += 1
                except (AttributeError, TypeError, ValueError) as e:
                    logger.warning(
                        "Skipping AKShare row for %s: %s",
                        row.get(COL_CODE, "???") if isinstance(row, Mapping) else "???",
                        e,
                    )
            self._consecutive_failures = 0
            logger.debug(
                "AKShare poll: updated %d/%d tickers", processed, len(self._tickers)
            )

        except Exception as e:
            # Don't re-raise — keep the previous frame's quotes and retry on
            # the next interval. Common failures: network errors, 东财
            # endpoint hiccups, akshare not installed.
            self._warn_poll_failure(e)

    def _is_frozen_frame(self, ticker: str, price: float, volume: float) -> bool:
        """True when this poll shows no market activity for the ticker.

        The 东财 snapshot keeps serving the last session's closing frame
        while the market is closed. Re-stamping it every poll would keep the
        quote "fresh" forever and let trades fill at frozen prices all
        night; skipping the write freezes the quote at the close so the
        trade-path freshness gate blocks executions (contract §1 docs:
        收盘时段报价冻结 → 新鲜度闸门拦截交易). A price change or any new
        cumulative volume counts as activity; the first frame always writes.
        """
        previous = self._cache.get(ticker)
        return previous is not None and previous.price == round(price, 2) and volume <= 0.0

    def _warn_poll_failure(self, error: Exception) -> None:
        """Log a poll failure, throttled while the failure streak continues."""
        self._consecutive_failures += 1
        now = float(self._now())
        if (
            self._consecutive_failures > 1
            and self._last_failure_warn is not None
            and now - self._last_failure_warn < FAILURE_WARN_INTERVAL_SECONDS
        ):
            return
        self._last_failure_warn = now
        logger.warning(
            "AKShare spot poll failed (%d consecutive): %s — keeping last "
            "quotes; will retry in %.1fs",
            self._consecutive_failures,
            error,
            self._interval,
        )

    def _volume_delta(self, ticker: str, cumulative_volume: object) -> float:
        """Volume traded since the previous poll for a ticker.

        Computed as the delta of the snapshot's cumulative session volume
        (成交量) vs the previous poll, clamped >= 0 (the cumulative resets
        across sessions). Returns 0.0 when the column is unavailable/NaN or
        on the first poll for the ticker (no previous cumulative to diff
        against). Mirrors the Massive client's day-volume differencing.
        """
        if isinstance(cumulative_volume, bool) or not isinstance(
            cumulative_volume, (int, float)
        ):
            return 0.0
        cumulative = float(cumulative_volume)
        if not math.isfinite(cumulative) or cumulative < 0:
            return 0.0
        previous = self._prev_cumulative_volume.get(ticker)
        self._prev_cumulative_volume[ticker] = cumulative
        if previous is None:
            return 0.0
        return max(0.0, cumulative - previous)

    def _fetch_spot_rows(self) -> list:
        """One full-market spot snapshot as a list of row mappings.

        Synchronous — runs in a thread. Uses the injected ``fetch_fn`` when
        provided (tests); otherwise imports akshare lazily and converts the
        ``stock_zh_a_spot_em()`` DataFrame to records.
        """
        if self._fetch_fn is not None:
            return list(self._fetch_fn())
        akshare = _import_akshare()
        frame = akshare.stock_zh_a_spot_em()
        return frame.to_dict("records")
