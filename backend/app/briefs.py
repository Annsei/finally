"""Event-driven AI briefs for FinAlly (M2.3).

A background watcher polls the price cache's market-event feed (~2s cadence).
For each NEW event whose ticker the user holds or watches, it asks the LLM
for a one-sentence, actionable take and pushes it into the chat panel as an
unsolicited assistant message with kind='brief' (actions NULL — the frontend
labels briefs by their kind, so the stored content carries no prefix).

Throttling (module constants):
- Global: at most one brief per BRIEF_GLOBAL_COOLDOWN_SECONDS across all
  tickers.
- Per ticker: at most one brief per BRIEF_TICKER_COOLDOWN_SECONDS.
Events skipped by either throttle (or because the ticker is irrelevant, or
the LLM call failed) are CONSUMED — they are never queued for a later pass.

Provides:
- ``process_events_for_briefs_once(price_cache, db_path, state, now=None)`` —
  one scan pass (async, unit-testable; ``now`` injects a wall-clock for
  deterministic throttle tests). Returns counts.
- ``briefs_watch_loop(price_cache, db_path, interval)`` — asyncio background
  task wired in main.py's lifespan, calling the pass every ~2 seconds with
  clean cancellation and per-pass error isolation.

When ``LLM_MOCK=true`` briefs are deterministic text — no network call.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.db.connection import get_conn
from app.market.cache import PriceCache
from app.market.models import MarketEvent

logger = logging.getLogger(__name__)

MODEL = "openrouter/openai/gpt-oss-120b"
EXTRA_BODY = {"provider": {"order": ["cerebras"]}}

# At most one brief per minute across all tickers…
BRIEF_GLOBAL_COOLDOWN_SECONDS = 60.0
# …and at most one brief per ticker every five minutes.
BRIEF_TICKER_COOLDOWN_SECONDS = 300.0
# Cadence of the background watcher.
BRIEFS_WATCH_INTERVAL_SECONDS = 2.0

BRIEF_SYSTEM_PROMPT = (
    "You are FinAlly, an AI trading assistant. A sudden market move just "
    "happened on a ticker the user holds or watches. Reply with exactly ONE "
    "short, actionable sentence for the user — plain text, no JSON, no "
    "markdown."
)


@dataclass
class BriefWatcherState:
    """Mutable watcher state carried across passes (one instance per loop).

    ``seen_event_ids`` marks events already consumed (briefed OR skipped);
    it is pruned each pass to ids still present in the cache's event ring
    buffer, so it stays bounded. The two timestamp fields drive the global
    and per-ticker throttles.
    """

    seen_event_ids: set[str] = field(default_factory=set)
    last_brief_ts: float | None = None
    last_ticker_brief_ts: dict[str, float] = field(default_factory=dict)


async def _generate_brief_text(
    price_cache: PriceCache,
    position_row: sqlite3.Row | None,
    event: MarketEvent,
) -> str | None:
    """One-sentence AI brief for a market event, or None on any LLM failure.

    Mock path (LLM_MOCK=true): deterministic text, no network call.
    Real path: compact prompt — the event headline, the user's position in
    the ticker (qty / avg cost / unrealized P&L) or "watching, no position",
    and the day change — via LiteLLM -> OpenRouter (Cerebras), plain text.
    Errors are logged and reported as None; the caller skips the event.
    """
    if os.getenv("LLM_MOCK", "false").lower() == "true":
        return (
            f"[MOCK BRIEF] {event.ticker} moved {event.change_percent:+.1f}%"
            " — review your exposure."
        )

    quote = price_cache.get(event.ticker)
    if position_row is not None:
        quantity: float = position_row["quantity"]
        avg_cost: float = position_row["avg_cost"]
        # No quote (ticker evicted between event and pass) — value at cost so
        # the prompt stays coherent (unrealized reads $0.00).
        current_price = quote.price if quote else avg_cost
        unrealized = (current_price - avg_cost) * quantity
        position_line = (
            f"User position: {quantity:g} shares at avg cost ${avg_cost:.2f}, "
            f"unrealized P&L ${unrealized:+.2f}"
        )
    else:
        position_line = "User position: watching, no position"
    day_line = (
        f"Day change: {quote.day_change_percent:+.2f}%" if quote else "Day change: n/a"
    )

    messages = [
        {"role": "system", "content": BRIEF_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Market event: {event.headline}\n{position_line}\n{day_line}",
        },
    ]
    try:
        from litellm import completion  # lazy import — never reached when mocked

        response = await asyncio.to_thread(
            completion,
            model=MODEL,
            messages=messages,
            reasoning_effort="low",
            extra_body=EXTRA_BODY,
        )
        text = (response.choices[0].message.content or "").strip()
    except Exception:
        logger.exception(
            "Brief LLM call failed for %s — skipping event %s", event.ticker, event.id
        )
        return None
    if not text:
        logger.warning(
            "Brief LLM returned empty content for %s — skipping event %s",
            event.ticker,
            event.id,
        )
        return None
    return text


async def process_events_for_briefs_once(
    price_cache: PriceCache,
    db_path: str,
    state: BriefWatcherState,
    now: float | None = None,
) -> dict[str, int]:
    """One watcher pass: brief every new, relevant, unthrottled market event.

    New events (ids not yet in ``state.seen_event_ids``) are processed oldest
    first. Every new event is consumed this pass regardless of outcome:

    - Ticker neither held nor watched     -> "skipped_irrelevant"
    - Global or per-ticker cooldown active -> "skipped_throttled"
    - LLM call failed / empty content      -> "skipped_llm_error" (no row)
    - Otherwise an assistant chat_messages row (kind='brief', actions NULL)
      is inserted and committed            -> "briefed"

    Throttle timestamps advance only on a successful brief, so a failed LLM
    call does not burn the cooldown budget.

    Args:
        price_cache: Shared cache (source of events, quotes, positions P&L).
        db_path: Path to the SQLite database file.
        state: Watcher state carried across passes (seen ids + throttles).
        now: Wall-clock override for deterministic throttle tests; defaults
            to ``time.time()`` per event.

    Returns:
        Counts: {"briefed", "skipped_irrelevant", "skipped_throttled",
        "skipped_llm_error"}.
    """
    counts = {
        "briefed": 0,
        "skipped_irrelevant": 0,
        "skipped_throttled": 0,
        "skipped_llm_error": 0,
    }
    events = price_cache.get_events()  # newest first
    # Events that fell off the ring buffer can never be returned again —
    # prune their ids so the seen-set stays bounded by the buffer size.
    state.seen_event_ids &= {e.id for e in events}
    new_events = [e for e in reversed(events) if e.id not in state.seen_event_ids]
    if not new_events:
        return counts

    conn = get_conn(db_path)
    try:
        watched = {
            row["ticker"]
            for row in conn.execute(
                "SELECT ticker FROM watchlist WHERE user_id = 'default'"
            )
        }
        positions = {
            row["ticker"]: row
            for row in conn.execute(
                "SELECT ticker, quantity, avg_cost FROM positions "
                "WHERE user_id = 'default'"
            )
        }

        for event in new_events:
            # Consumed no matter the outcome below — throttled/irrelevant/
            # failed events are never queued for a later pass.
            state.seen_event_ids.add(event.id)
            ticker = event.ticker

            if ticker not in watched and ticker not in positions:
                counts["skipped_irrelevant"] += 1
                continue

            ts = now if now is not None else time.time()
            if (
                state.last_brief_ts is not None
                and ts - state.last_brief_ts < BRIEF_GLOBAL_COOLDOWN_SECONDS
            ):
                counts["skipped_throttled"] += 1
                continue
            last_ticker_ts = state.last_ticker_brief_ts.get(ticker)
            if (
                last_ticker_ts is not None
                and ts - last_ticker_ts < BRIEF_TICKER_COOLDOWN_SECONDS
            ):
                counts["skipped_throttled"] += 1
                continue

            text = await _generate_brief_text(price_cache, positions.get(ticker), event)
            if text is None:
                counts["skipped_llm_error"] += 1
                continue

            conn.execute(
                "INSERT INTO chat_messages (id, user_id, role, content, actions, kind, created_at) "
                "VALUES (?, 'default', 'assistant', ?, NULL, 'brief', ?)",
                (str(uuid.uuid4()), text, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            state.last_brief_ts = ts
            state.last_ticker_brief_ts[ticker] = ts
            counts["briefed"] += 1
            logger.info("AI brief posted for %s: %s", ticker, text)
    finally:
        conn.close()
    return counts


async def briefs_watch_loop(
    price_cache: PriceCache,
    db_path: str,
    interval: float = BRIEFS_WATCH_INTERVAL_SECONDS,
) -> None:
    """Background task: watch the event feed and post AI briefs (M2.3).

    Runs indefinitely until cancelled via ``asyncio.CancelledError``. Any
    other exception (DB lock, cache hiccup) is logged and the loop continues
    — one bad pass never kills the watcher.
    """
    state = BriefWatcherState()
    while True:
        try:
            await process_events_for_briefs_once(price_cache, db_path, state)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Briefs watch loop error — will retry in %ss", interval)
        await asyncio.sleep(interval)
