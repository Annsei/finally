"""Market-event persistence (P1 §3.2).

The PriceCache holds recent market events (sudden >=1% single-tick moves) in
a 100-slot in-memory ring buffer — enough for the live news ticker, but
events older than the window are lost on eviction and everything is lost on
restart. This module archives the buffer to the ``market_events`` table so
the events archive endpoint (GET /api/market/events/archive) can serve the
full history.

A background loop (wired in main.py's lifespan, same pattern as the briefs
watcher) upserts the buffer every ~5 seconds:

    INSERT ... ON CONFLICT(id) DO UPDATE SET narrative = excluded.narrative

so re-persisting an already-archived event is a no-op EXCEPT for the
narrative column — the LLM narrative enricher (M3.2a) attaches its headline
seconds after the event fires, and the upsert backfills it automatically as
long as the event is still inside the ring-buffer window.

Provides:
- ``persist_events_once(price_cache, db_path)`` — one archive pass
  (synchronous, unit-testable; single commit). Returns the number of events
  upserted.
- ``events_persist_loop(price_cache, db_path, interval)`` — asyncio
  background task running the pass every ~5 seconds with clean cancellation
  and error isolation (one bad pass never kills the loop).
"""

from __future__ import annotations

import asyncio
import logging

from app.db.connection import get_conn
from app.market.cache import EVENT_BUFFER_SIZE, PriceCache

logger = logging.getLogger(__name__)

# Cadence of the background archiver (P1 §3.2: every ~5s).
EVENTS_PERSIST_INTERVAL_SECONDS = 5.0

_UPSERT_SQL = (
    "INSERT INTO market_events "
    "(id, ticker, headline, narrative, change_percent, direction, timestamp) "
    "VALUES (?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(id) DO UPDATE SET narrative = excluded.narrative"
)


def persist_events_once(price_cache: PriceCache, db_path: str) -> int:
    """One archive pass: upsert the cache's event ring buffer into SQLite.

    Every event currently in the buffer (at most ``EVENT_BUFFER_SIZE``) is
    written with ``INSERT ... ON CONFLICT(id) DO UPDATE SET narrative`` —
    new events insert, already-archived events only refresh their
    ``narrative`` (late LLM enrichment backfills; None never regresses a
    stored value because the cache is the narrative's only writer and it
    never clears one). One commit per pass.

    Returns:
        The number of events upserted this pass (0 for an empty buffer).
    """
    events = price_cache.get_events(limit=EVENT_BUFFER_SIZE)
    if not events:
        return 0

    conn = get_conn(db_path)
    try:
        conn.executemany(
            _UPSERT_SQL,
            [
                (
                    event.id,
                    event.ticker,
                    event.headline,
                    event.narrative,
                    event.change_percent,
                    event.direction,
                    event.timestamp,
                )
                for event in events
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return len(events)


async def events_persist_loop(
    price_cache: PriceCache,
    db_path: str,
    interval: float = EVENTS_PERSIST_INTERVAL_SECONDS,
) -> None:
    """Background task: archive market events to SQLite every ``interval``s.

    Same lifecycle contract as the other main.py background loops (snapshot,
    briefs): runs indefinitely until cancelled via ``asyncio.CancelledError``;
    any other exception (DB lock, cache hiccup) is logged and the loop
    continues — one bad pass never kills the archiver.
    """
    while True:
        try:
            persist_events_once(price_cache, db_path)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Event persist loop error — will retry in %ss", interval)
        await asyncio.sleep(interval)
