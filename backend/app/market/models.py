"""Data models for market data."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .seed_prices import asset_class_for


@dataclass(frozen=True, slots=True)
class MarketEvent:
    """Immutable record of a sudden single-tick price move ("market event").

    Recorded centrally by ``PriceCache.update()`` whenever a tick's move
    exceeds the event threshold (see ``app.market.cache``), regardless of
    whether the simulator or the Massive source produced the tick.
    """

    id: str  # uuid4 string
    ticker: str
    headline: str  # e.g. "NVDA surges +3.4% in sudden move"
    change_percent: float  # signed tick move, rounded to 2 decimals
    direction: str  # "up" | "down"
    timestamp: float  # Unix seconds (the update timestamp)
    # LLM-generated news-style headline (M3.2a). None until the narrative
    # enricher processes the event; events skipped by the enricher's throttle
    # stay template-only (None) forever.
    narrative: str | None = None

    def to_dict(self) -> dict:
        """Serialize for JSON transmission. ``narrative`` is always present
        (null until enriched)."""
        return {
            "id": self.id,
            "ticker": self.ticker,
            "headline": self.headline,
            "change_percent": self.change_percent,
            "direction": self.direction,
            "timestamp": self.timestamp,
            "narrative": self.narrative,
        }


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Immutable snapshot of a single ticker's price at a point in time.

    Per-tick fields (`change`, `change_percent`, `direction`) compare against
    the immediately preceding update. Session fields (`prev_close`,
    `day_high`, `day_low` and the derived `day_change`/`day_change_percent`)
    compare against the previous session close and track running extremes.
    When session fields are omitted at construction they default to the
    current price (first-tick-of-session semantics).

    Quote/volume fields: `volume` is the volume traded since the previous
    update for this ticker (0.0 when the source supplies none). `bid`/`ask`
    are the best bid/ask; when a source supplies neither they default to the
    current price (zero spread), so both are always present in `to_dict()`.
    """

    ticker: str
    price: float
    previous_price: float
    timestamp: float = field(default_factory=time.time)  # Unix seconds
    prev_close: float | None = None  # Previous session close reference price
    day_high: float | None = None  # Running session high
    day_low: float | None = None  # Running session low
    volume: float = 0.0  # Volume traded since the previous update
    bid: float | None = None  # Best bid (defaults to price)
    ask: float | None = None  # Best ask (defaults to price)

    def __post_init__(self) -> None:
        # Normalize omitted session fields to the current price.
        # frozen=True blocks normal assignment; object.__setattr__ is the
        # sanctioned dataclass escape hatch inside __post_init__.
        if self.prev_close is None:
            object.__setattr__(self, "prev_close", self.price)
        if self.day_high is None:
            object.__setattr__(self, "day_high", self.price)
        if self.day_low is None:
            object.__setattr__(self, "day_low", self.price)
        # Omitted quote fields default to the price (zero spread).
        if self.bid is None:
            object.__setattr__(self, "bid", self.price)
        if self.ask is None:
            object.__setattr__(self, "ask", self.price)

    @property
    def change(self) -> float:
        """Absolute price change from previous update."""
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        """Percentage change from previous update."""
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def direction(self) -> str:
        """'up', 'down', or 'flat'."""
        if self.price > self.previous_price:
            return "up"
        elif self.price < self.previous_price:
            return "down"
        return "flat"

    @property
    def asset_class(self) -> str:
        """'crypto' for the crypto set (BTC/ETH), 'equity' for everything else."""
        return asset_class_for(self.ticker)

    @property
    def day_change(self) -> float:
        """Absolute price change vs the previous session close."""
        return round(self.price - self.prev_close, 4)

    @property
    def day_change_percent(self) -> float:
        """Percentage change vs the previous session close."""
        if self.prev_close is None or self.prev_close <= 0:
            return 0.0
        return round((self.price - self.prev_close) / self.prev_close * 100, 4)

    def to_dict(self) -> dict:
        """Serialize for JSON / SSE transmission."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "timestamp": self.timestamp,
            "change": self.change,
            "change_percent": self.change_percent,
            "direction": self.direction,
            "prev_close": self.prev_close,
            "day_change": self.day_change,
            "day_change_percent": self.day_change_percent,
            "day_high": self.day_high,
            "day_low": self.day_low,
            "volume": self.volume,
            "bid": self.bid,
            "ask": self.ask,
            "asset_class": self.asset_class,
        }
