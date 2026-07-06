"""Event-driven AI briefs (M2.3) and news-narrative enrichment (M3.2a).

A background watcher polls the price cache's market-event feed (~2s cadence)
and runs TWO passes per cycle:

1. Narrative enrichment (M3.2a): for each NEW event — ALL tickers, not only
   held/watched — ask the LLM for a one-line news-style headline (a plausible
   but clearly simulated cause) and attach it to the event in the cache via
   ``PriceCache.set_event_narrative``. Throttle: at most one enrichment per
   ``NARRATIVE_COOLDOWN_SECONDS`` globally; skipped events keep their
   template headline forever (they are consumed, never retried).

2. AI briefs (M2.3): for each NEW event whose ticker the user holds or
   watches, ask the LLM for a one-sentence, actionable take and push it into
   the chat panel as an unsolicited assistant message with kind='brief'
   (actions NULL — the frontend labels briefs by their kind, so the stored
   content carries no prefix).

Brief throttling (module constants):
- Global: at most one brief per BRIEF_GLOBAL_COOLDOWN_SECONDS across all
  tickers.
- Per ticker: at most one brief per BRIEF_TICKER_COOLDOWN_SECONDS.
Events skipped by either throttle (or because the ticker is irrelevant, or
the LLM call failed) are CONSUMED — they are never queued for a later pass.
The two passes keep independent seen-sets and throttles: an event throttled
for narration can still produce a brief, and vice versa.

Provides:
- ``process_events_for_briefs_once(price_cache, db_path, state, now=None)`` —
  one briefs scan pass (async, unit-testable; ``now`` injects a wall-clock
  for deterministic throttle tests). Returns counts.
- ``process_events_for_narratives_once(price_cache, state, now=None)`` —
  one narrative-enrichment pass (same testability contract). Returns counts.
- ``briefs_watch_loop(price_cache, db_path, interval)`` — asyncio background
  task wired in main.py's lifespan, running both passes every ~2 seconds with
  clean cancellation and per-pass error isolation.

When ``LLM_MOCK=true`` briefs and narratives are deterministic text — no
network call.
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
from app.market.seed_prices import asset_class_for

logger = logging.getLogger(__name__)

MODEL = "openrouter/openai/gpt-oss-120b"
EXTRA_BODY = {"provider": {"order": ["cerebras"]}}

# At most one brief per minute across all tickers…
BRIEF_GLOBAL_COOLDOWN_SECONDS = 60.0
# …and at most one brief per ticker every five minutes.
BRIEF_TICKER_COOLDOWN_SECONDS = 300.0
# Cadence of the background watcher.
BRIEFS_WATCH_INTERVAL_SECONDS = 2.0
# At most one LLM narrative enrichment per 10s globally (M3.2a). Events
# skipped by this throttle stay template-only forever.
NARRATIVE_COOLDOWN_SECONDS = 10.0
# Hard cap requested from the LLM for narrative headlines.
NARRATIVE_MAX_CHARS = 90

BRIEF_SYSTEM_PROMPT = (
    "You are FinAlly, an AI trading assistant. A sudden market move just "
    "happened on a ticker the user holds or watches. Reply with exactly ONE "
    "short, actionable sentence for the user — plain text, no JSON, no "
    "markdown."
)

NARRATIVE_SYSTEM_PROMPT = (
    "You are a financial news wire inside a market SIMULATOR. Given a "
    "templated market-event headline, write exactly ONE punchy news-style "
    f"headline of at most {NARRATIVE_MAX_CHARS} characters explaining the "
    "move with a plausible but clearly invented, simulated cause (this is "
    "fake money — never reference real current events). Plain text only: no "
    "surrounding quotes, no JSON, no markdown, no emoji."
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


@dataclass
class NarrativeEnricherState:
    """Mutable narrative-enricher state carried across passes (M3.2a).

    Independent of ``BriefWatcherState`` — the enricher covers ALL tickers
    and has its own (10s global) throttle. ``seen_event_ids`` marks events
    already consumed (enriched OR skipped); pruned each pass to ids still in
    the cache's event ring buffer so it stays bounded.
    """

    seen_event_ids: set[str] = field(default_factory=set)
    last_narrative_ts: float | None = None


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


async def _generate_narrative_text(
    price_cache: PriceCache,
    event: MarketEvent,
) -> str | None:
    """One-line news-style narrative for a market event, or None on failure.

    Mock path (LLM_MOCK=true): deterministic ``[MOCK NEWS] {headline}`` — no
    network call. Real path: compact prompt — the template headline, ticker,
    day change, and asset class — via LiteLLM -> OpenRouter (Cerebras), plain
    text, asking for a single punchy simulated-cause headline (<= 90 chars).
    Errors are logged and reported as None; the caller skips the event.
    """
    if os.getenv("LLM_MOCK", "false").lower() == "true":
        return f"[MOCK NEWS] {event.headline}"

    quote = price_cache.get(event.ticker)
    day_line = (
        f"Day change: {quote.day_change_percent:+.2f}%" if quote else "Day change: n/a"
    )
    asset_class = quote.asset_class if quote else asset_class_for(event.ticker)

    messages = [
        {"role": "system", "content": NARRATIVE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Template headline: {event.headline}\n"
                f"Ticker: {event.ticker}\n"
                f"{day_line}\n"
                f"Asset class: {asset_class}"
            ),
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
            "Narrative LLM call failed for %s — skipping event %s",
            event.ticker,
            event.id,
        )
        return None
    if not text:
        logger.warning(
            "Narrative LLM returned empty content for %s — skipping event %s",
            event.ticker,
            event.id,
        )
        return None
    # Belt and braces: the prompt asks for <= 90 chars and no quotes, but the
    # model may not comply — enforce both so the feed stays tidy.
    text = text.strip("\"'").strip()
    if len(text) > NARRATIVE_MAX_CHARS:
        text = text[: NARRATIVE_MAX_CHARS - 1].rstrip() + "…"
    return text or None


async def process_events_for_narratives_once(
    price_cache: PriceCache,
    state: NarrativeEnricherState,
    now: float | None = None,
) -> dict[str, int]:
    """One enrichment pass: narrate every new, unthrottled market event.

    New events (ids not yet in ``state.seen_event_ids``) are processed oldest
    first — ALL tickers, not only held/watched. Every new event is consumed
    this pass regardless of outcome; skipped events keep their template
    headline forever:

    - Global 10s cooldown active            -> "skipped_throttled"
    - LLM call failed / empty content       -> "skipped_llm_error"
    - Event evicted from the ring buffer
      before the narrative landed           -> "skipped_evicted"
    - Otherwise the narrative is attached via
      ``PriceCache.set_event_narrative``    -> "enriched"

    The throttle timestamp advances only on a successful enrichment, so a
    failed LLM call does not burn the cooldown budget.

    Args:
        price_cache: Shared cache (source of events; narrative store).
        state: Enricher state carried across passes (seen ids + throttle).
        now: Wall-clock override for deterministic throttle tests; defaults
            to ``time.time()`` per event.

    Returns:
        Counts: {"enriched", "skipped_throttled", "skipped_llm_error",
        "skipped_evicted"}.
    """
    counts = {
        "enriched": 0,
        "skipped_throttled": 0,
        "skipped_llm_error": 0,
        "skipped_evicted": 0,
    }
    events = price_cache.get_events()  # newest first
    # Ids that fell off the ring buffer can never come back — prune the
    # seen-set so it stays bounded by the buffer size.
    state.seen_event_ids &= {e.id for e in events}
    new_events = [e for e in reversed(events) if e.id not in state.seen_event_ids]

    for event in new_events:
        # Consumed no matter the outcome below — throttled/failed events are
        # never queued for a later pass (they stay template-only).
        state.seen_event_ids.add(event.id)

        ts = now if now is not None else time.time()
        if (
            state.last_narrative_ts is not None
            and ts - state.last_narrative_ts < NARRATIVE_COOLDOWN_SECONDS
        ):
            counts["skipped_throttled"] += 1
            continue

        text = await _generate_narrative_text(price_cache, event)
        if text is None:
            counts["skipped_llm_error"] += 1
            continue

        if not price_cache.set_event_narrative(event.id, text):
            # Evicted between the scan and the LLM round-trip — nothing to
            # attach the narrative to.
            counts["skipped_evicted"] += 1
            continue

        state.last_narrative_ts = ts
        counts["enriched"] += 1
        logger.info("Narrative attached for %s: %s", event.ticker, text)

    return counts


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
    """Background task: enrich event narratives and post AI briefs.

    Each cycle runs the narrative-enrichment pass (M3.2a — all tickers) and
    then the briefs pass (M2.3 — held/watched tickers only). The passes are
    error-isolated from each other: a failure in one never blocks the other.

    Runs indefinitely until cancelled via ``asyncio.CancelledError``. Any
    other exception (DB lock, cache hiccup) is logged and the loop continues
    — one bad pass never kills the watcher.
    """
    state = BriefWatcherState()
    narrative_state = NarrativeEnricherState()
    while True:
        try:
            await process_events_for_narratives_once(price_cache, narrative_state)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Narrative enrichment pass error — will retry in %ss", interval)
        try:
            await process_events_for_briefs_once(price_cache, db_path, state)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Briefs watch loop error — will retry in %ss", interval)
        await asyncio.sleep(interval)
