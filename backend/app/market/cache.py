"""Thread-safe in-memory price cache."""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from threading import Lock

from .models import MarketEvent, PriceUpdate

logger = logging.getLogger(__name__)

# ~2 hours of 1-second bars per ticker.
DEFAULT_HISTORY_CAPACITY = 7200

# Throttle for the stale-quote rejection warning: at most one log line per
# ticker per this many seconds (wall clock), no matter how many execution
# paths keep rejecting on the same frozen quote.
STALE_WARN_INTERVAL_SECONDS = 60.0

# Market-event detection (news feed): a single-tick move of at least this
# magnitude (percent, absolute) records a MarketEvent.
EVENT_THRESHOLD_PERCENT = 1.0
# After an event fires for a ticker, further events for that ticker are
# suppressed until this much update-timestamp time has elapsed (not wall
# clock, so detection is deterministic under injected timestamps).
EVENT_COOLDOWN_SECONDS = 30.0
# Ring buffer size for recent events across all tickers.
EVENT_BUFFER_SIZE = 100


def _clamp(value: float, low: float, high: float) -> float:
    """Clamp ``value`` into ``[low, high]`` (CN-2 §4 price-limit band)."""
    return min(max(value, low), high)


class PriceCache:
    """Thread-safe in-memory cache of the latest price for each ticker.

    Writers: SimulatorDataSource or MassiveDataSource (one at a time).
    Readers: SSE streaming endpoint, portfolio valuation, trade execution,
    and the market history endpoint (1-second OHLCV ring buffer).
    """

    def __init__(
        self,
        history_capacity: int = DEFAULT_HISTORY_CAPACITY,
        limit_pct_fn: Callable[[str], float | None] | None = None,
        max_quote_age_seconds: float | None = None,
    ) -> None:
        # Per-ticker daily price-limit percent (CN-2 §4). When provided and the
        # ticker has a session prev_close, ``update()``/``roll_session()`` derive
        # the [limit_down, limit_up] band and clamp the price (and bid/ask) into
        # it — the single funnel every market source writes through, so the
        # simulator's ticks, random events, and sector bursts are all bounded.
        # None (us) disables clamping entirely — the pre-CN-2 behavior.
        self._limit_pct_fn = limit_pct_fn
        self._max_quote_age_seconds = max_quote_age_seconds
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = Lock()
        self._version: int = 0  # Monotonically increasing; bumped on every update
        self._history_capacity = history_capacity
        # Per-ticker ring buffer of 1-second OHLCV bars, ascending by time.
        # Bars are plain dicts (time/open/high/low/close/volume) so the
        # history endpoint can serialize them directly.
        self._bars: dict[str, deque[dict]] = {}
        # Recent market events across all tickers, oldest -> newest.
        self._events: deque[MarketEvent] = deque(maxlen=EVENT_BUFFER_SIZE)
        # Per-ticker update-timestamp of the last recorded event (cooldown).
        self._last_event_ts: dict[str, float] = {}
        # Official session closes stamped at market close (M3.1); consumed by
        # roll_session() at the next open to become each ticker's prev_close.
        self._settled_closes: dict[str, float] = {}
        # Per-ticker wall-clock time of the last stale-rejection warning
        # (throttle state for warn_stale_rejection).
        self._stale_warned: dict[str, float] = {}

    def update(
        self,
        ticker: str,
        price: float,
        timestamp: float | None = None,
        prev_close: float | None = None,
        day_high: float | None = None,
        day_low: float | None = None,
        volume: float = 0.0,
        bid: float | None = None,
        ask: float | None = None,
    ) -> PriceUpdate:
        """Record a new price for a ticker. Returns the created PriceUpdate.

        Automatically computes direction and change from the previous price.
        If this is the first update for the ticker, previous_price == price (direction='flat').

        Session fields may be supplied explicitly (e.g. the Massive source
        passes the snapshot's prevDay close and day high/low). When omitted,
        the cache carries per-ticker session state forward:
          - prev_close: captured from the first price seen for the ticker and
            held constant for the session (for the simulator this is the seed
            price the GBM walk starts from, written by start()/add_ticker()).
          - day_high / day_low: running session extremes, initialized to the
            first price and updated on every tick.

        Quote/volume fields:
          - volume: volume traded since the previous update (clamped >= 0;
            defaults to 0.0 when the source supplies none).
          - bid / ask: best bid/ask; when omitted they default to the price
            (zero spread), preserving pre-quote behavior for test fakes.

        Every update also feeds the ticker's 1-second OHLCV ring buffer:
        updates in the same Unix second merge into one bar, a new second
        appends a bar, and updates older than the newest bar are ignored
        (for the buffer only — the latest-price record still updates).
        """
        with self._lock:
            ts = timestamp if timestamp is not None else time.time()
            prev = self._prices.get(ticker)
            previous_price = prev.price if prev else price
            rounded_price = round(price, 2)
            tick_volume = max(0.0, float(volume))

            if prev_close is not None:
                session_prev_close = round(prev_close, 2)
            elif prev is not None:
                session_prev_close = prev.prev_close
            else:
                session_prev_close = rounded_price

            # CN-2 §4: derive today's price-limit band from prev_close and clamp
            # the price BEFORE the buffers/extremes see it — a封板 tick lands
            # exactly on limit_up/limit_down. us (no fn) yields (None, None), so
            # nothing below changes and the SSE payload is unchanged.
            limit_up, limit_down = self._limits_for(ticker, session_prev_close)
            rounded_bid = round(bid, 2) if bid is not None else None
            rounded_ask = round(ask, 2) if ask is not None else None
            if limit_up is not None:
                rounded_price = _clamp(rounded_price, limit_down, limit_up)
                if rounded_bid is not None:
                    rounded_bid = _clamp(rounded_bid, limit_down, limit_up)
                if rounded_ask is not None:
                    rounded_ask = _clamp(rounded_ask, limit_down, limit_up)

            if day_high is not None:
                session_high = round(day_high, 2)
            elif prev is not None:
                session_high = max(prev.day_high, rounded_price)
            else:
                session_high = rounded_price

            if day_low is not None:
                session_low = round(day_low, 2)
            elif prev is not None:
                session_low = min(prev.day_low, rounded_price)
            else:
                session_low = rounded_price

            update = PriceUpdate(
                ticker=ticker,
                price=rounded_price,
                previous_price=round(previous_price, 2),
                timestamp=ts,
                prev_close=session_prev_close,
                day_high=session_high,
                day_low=session_low,
                volume=tick_volume,
                bid=rounded_bid,
                ask=rounded_ask,
                limit_up=limit_up,
                limit_down=limit_down,
            )
            self._prices[ticker] = update
            self._version += 1
            self._record_bar(ticker, rounded_price, ts, tick_volume)
            self._maybe_record_event(update)
            return update

    def _limits_for(
        self, ticker: str, prev_close: float | None
    ) -> tuple[float | None, float | None]:
        """Today's [limit_down, limit_up] band for a ticker (CN-2 §4).

        Returns ``(limit_up, limit_down)`` when a price-limit function is
        configured and yields a percent for a ticker with a positive
        prev_close; ``(None, None)`` otherwise (always so for us). Rounded to
        cents, symmetric around prev_close.

        Must be called with self._lock held.
        """
        if self._limit_pct_fn is None or prev_close is None or prev_close <= 0:
            return None, None
        pct = self._limit_pct_fn(ticker)
        if pct is None:
            return None, None
        limit_up = round(prev_close * (1.0 + pct / 100.0), 2)
        limit_down = round(prev_close * (1.0 - pct / 100.0), 2)
        return limit_up, limit_down

    def _maybe_record_event(self, update: PriceUpdate) -> None:
        """Record a MarketEvent when a tick's move crosses the event threshold.

        Detection lives here — the single funnel every market source writes
        through — so both the simulator's random "events" and real Massive
        data produce news-feed entries. A per-ticker cooldown (measured in
        update-timestamp time, not wall clock) prevents a volatile ticker
        from flooding the feed.

        Must be called with self._lock held.
        """
        change_percent = update.change_percent
        if abs(change_percent) < EVENT_THRESHOLD_PERCENT:
            return

        last_ts = self._last_event_ts.get(update.ticker)
        if last_ts is not None and update.timestamp - last_ts < EVENT_COOLDOWN_SECONDS:
            return

        direction = "up" if change_percent > 0 else "down"
        verb = "surges" if direction == "up" else "plunges"
        event = MarketEvent(
            id=str(uuid.uuid4()),
            ticker=update.ticker,
            headline=f"{update.ticker} {verb} {change_percent:+.1f}% in sudden move",
            change_percent=round(change_percent, 2),
            direction=direction,
            timestamp=update.timestamp,
        )
        self._events.append(event)
        self._last_event_ts[update.ticker] = update.timestamp

    def set_event_narrative(self, event_id: str, narrative: str) -> bool:
        """Attach an LLM-generated narrative to a recorded event (M3.2a).

        MarketEvent is a frozen dataclass, so the enriched record is built
        with ``dataclasses.replace`` and swapped into the ring buffer in
        place (ordering is preserved). Thread-safe.

        Returns True on success, False when the event id is unknown — e.g.
        the event has already been evicted from the ring buffer.
        """
        with self._lock:
            for i, event in enumerate(self._events):
                if event.id == event_id:
                    self._events[i] = replace(event, narrative=narrative)
                    return True
        return False

    def get_events(self, limit: int | None = None) -> list[MarketEvent]:
        """Return recent market events, newest first.

        At most ``limit`` newest events when limit is given. Returns a new
        list of immutable MarketEvent records, so callers cannot mutate the
        ring buffer. Events for tickers since removed from the cache may
        still appear (they are history).
        """
        with self._lock:
            items = list(self._events)
        items.reverse()
        if limit is not None:
            items = items[:limit]
        return items

    def _record_bar(self, ticker: str, price: float, ts: float, volume: float) -> None:
        """Merge a tick into the ticker's 1-second OHLCV ring buffer.

        Must be called with self._lock held.
        """
        bucket = int(ts)
        bars = self._bars.get(ticker)
        if bars is None:
            bars = deque(maxlen=self._history_capacity)
            self._bars[ticker] = bars

        if bars:
            newest = bars[-1]
            if bucket < newest["time"]:
                return  # Older than the newest bar — ignore (buffer only)
            if bucket == newest["time"]:
                newest["high"] = max(newest["high"], price)
                newest["low"] = min(newest["low"], price)
                newest["close"] = price
                newest["volume"] += volume
                return

        bars.append(
            {
                "time": bucket,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
            }
        )

    def get_history(self, ticker: str, limit: int | None = None) -> list[dict]:
        """Return the ticker's 1-second OHLCV bars, ascending by time.

        At most ``limit`` most-recent bars when limit is given. Unknown
        tickers return an empty list. Bars are returned as copies so callers
        can't mutate the ring buffer.
        """
        with self._lock:
            bars = self._bars.get(ticker)
            if not bars:
                return []
            items = list(bars)
            if limit is not None:
                items = items[-limit:]
            return [dict(bar) for bar in items]

    def settle_close(self, tickers: list[str]) -> dict[str, float]:
        """Stamp each ticker's current price as its official session close (M3.1).

        Called by the settlement hook when the market closes. The stamped
        close is held internally and becomes the ticker's ``prev_close`` when
        ``roll_session()`` runs at the next open. Live quotes are NOT touched
        — while the market is closed the frozen quote keeps showing the
        finished session's day change, exactly like a real after-hours tape.

        Tickers absent from the cache are skipped. Returns the stamped
        closes as ``{ticker: close}``.
        """
        with self._lock:
            closes: dict[str, float] = {}
            for ticker in tickers:
                update = self._prices.get(ticker)
                if update is not None:
                    self._settled_closes[ticker] = update.price
                    closes[ticker] = update.price
            return closes

    def roll_session(self, tickers: list[str], timestamp: float | None = None) -> None:
        """Reset day-session state for ``tickers`` at market open (M3.1).

        For each ticker present in the cache, replaces its record with a
        fresh-session baseline: ``prev_close`` becomes the close stamped by
        ``settle_close()`` (falling back to the current price — identical for
        equities, whose quotes freeze while closed), ``day_high``/``day_low``
        reset to that close, ``previous_price`` resets to the current price
        (first tick of the new session is 'flat'), and per-tick ``volume``
        resets to 0. Quote (bid/ask) carries over. The version counter bumps
        once so SSE clients receive the reset baseline immediately.

        The cache stays the single owner of session state — sources never
        write prev_close/day extremes themselves in simulator mode.
        """
        with self._lock:
            ts = timestamp if timestamp is not None else time.time()
            rolled = False
            for ticker in tickers:
                update = self._prices.get(ticker)
                close = self._settled_closes.pop(ticker, None)
                if update is None:
                    continue
                if close is None:
                    close = update.price
                # CN-2 §4: recompute the price-limit band against the new
                # session prev_close so the rolled baseline already carries
                # today's ceiling/floor (us yields None, None — unchanged).
                limit_up, limit_down = self._limits_for(ticker, close)
                self._prices[ticker] = PriceUpdate(
                    ticker=ticker,
                    price=update.price,
                    previous_price=update.price,
                    timestamp=ts,
                    prev_close=close,
                    day_high=max(update.price, close),
                    day_low=min(update.price, close),
                    volume=0.0,
                    bid=update.bid,
                    ask=update.ask,
                    limit_up=limit_up,
                    limit_down=limit_down,
                )
                rolled = True
            if rolled:
                self._version += 1

    def get(self, ticker: str) -> PriceUpdate | None:
        """Get the latest price for a single ticker, or None if unknown."""
        with self._lock:
            return self._prices.get(ticker)

    def get_all(self) -> dict[str, PriceUpdate]:
        """Snapshot of all current prices. Returns a shallow copy."""
        with self._lock:
            return dict(self._prices)

    def get_price(self, ticker: str) -> float | None:
        """Convenience: get just the price float, or None."""
        update = self.get(ticker)
        return update.price if update else None

    def is_fresh(self, ticker: str, now: float | None = None) -> bool:
        """Whether ``ticker`` has a quote inside the configured trading TTL.

        A cache created without ``max_quote_age_seconds`` keeps the legacy
        no-expiry behavior used by isolated unit tests. The real application
        always injects an explicit TTL from :class:`RuntimeSettings`.
        """
        update = self.get(ticker)
        if update is None:
            return False
        if self._max_quote_age_seconds is None:
            return True
        reference = time.time() if now is None else now
        return reference - update.timestamp <= self._max_quote_age_seconds

    def get_fresh(self, ticker: str, now: float | None = None) -> PriceUpdate | None:
        """Return the latest quote only when it is fresh enough to trade."""
        update = self.get(ticker)
        return update if update is not None and self.is_fresh(ticker, now) else None

    def warn_stale_rejection(self, ticker: str, now: float | None = None) -> None:
        """Log (throttled) that an execution path rejected ``ticker`` as stale.

        The trade/order/rule/strategy paths all fail closed on a stale quote
        (deliberately — never fill at a frozen price). Without observability
        that looks like a silent freeze, so every rejection site calls this
        single helper, which emits at most one ``logger.warning`` per ticker
        per ``STALE_WARN_INTERVAL_SECONDS`` including the quote's age. The
        throttle state is shared across all call sites — the log line exists
        to flag the condition, not to count rejections.
        """
        reference = time.time() if now is None else now
        update = self.get(ticker)
        with self._lock:
            last = self._stale_warned.get(ticker)
            if last is not None and reference - last < STALE_WARN_INTERVAL_SECONDS:
                return
            self._stale_warned[ticker] = reference
        age = reference - update.timestamp if update is not None else None
        logger.warning(
            "Rejecting execution on stale quote for %s: quote age %s "
            "exceeds TTL %ss (fail-closed; will retry when fresh data arrives)",
            ticker,
            f"{age:.1f}s" if age is not None else "unknown (no quote)",
            self._max_quote_age_seconds,
        )

    def remove(self, ticker: str) -> None:
        """Remove a ticker from the cache (e.g., when removed from watchlist).

        Also clears the ticker's OHLCV history ring buffer and its event
        cooldown state (a re-added ticker starts a fresh session). Already
        recorded events remain in the event buffer — they are history.
        """
        with self._lock:
            self._prices.pop(ticker, None)
            self._bars.pop(ticker, None)
            self._last_event_ts.pop(ticker, None)
            self._settled_closes.pop(ticker, None)
            self._stale_warned.pop(ticker, None)

    @property
    def version(self) -> int:
        """Current version counter. Useful for SSE change detection."""
        with self._lock:
            return self._version

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    def __contains__(self, ticker: str) -> bool:
        with self._lock:
            return ticker in self._prices
