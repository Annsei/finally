"""Thread-safe in-memory price cache."""

from __future__ import annotations

import time
import uuid
from collections import deque
from threading import Lock

from .models import MarketEvent, PriceUpdate

# ~2 hours of 1-second bars per ticker.
DEFAULT_HISTORY_CAPACITY = 7200

# Market-event detection (news feed): a single-tick move of at least this
# magnitude (percent, absolute) records a MarketEvent.
EVENT_THRESHOLD_PERCENT = 1.0
# After an event fires for a ticker, further events for that ticker are
# suppressed until this much update-timestamp time has elapsed (not wall
# clock, so detection is deterministic under injected timestamps).
EVENT_COOLDOWN_SECONDS = 30.0
# Ring buffer size for recent events across all tickers.
EVENT_BUFFER_SIZE = 100


class PriceCache:
    """Thread-safe in-memory cache of the latest price for each ticker.

    Writers: SimulatorDataSource or MassiveDataSource (one at a time).
    Readers: SSE streaming endpoint, portfolio valuation, trade execution,
    and the market history endpoint (1-second OHLCV ring buffer).
    """

    def __init__(self, history_capacity: int = DEFAULT_HISTORY_CAPACITY) -> None:
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
                bid=round(bid, 2) if bid is not None else None,
                ask=round(ask, 2) if ask is not None else None,
            )
            self._prices[ticker] = update
            self._version += 1
            self._record_bar(ticker, rounded_price, ts, tick_volume)
            self._maybe_record_event(update)
            return update

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
