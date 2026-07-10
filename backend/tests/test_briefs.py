"""Tests for the event-driven AI briefs watcher (M2.3, Task B).

Drives ``process_events_for_briefs_once`` directly with controlled ``now``
timestamps so both throttles are deterministic. Events are fired through the
real cache funnel (``PriceCache.update`` with a >=1% tick move); successive
events on the same ticker are spaced past the cache's own 30s per-ticker
event-detection cooldown.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.briefs import (
    BRIEF_GLOBAL_COOLDOWN_SECONDS,
    BRIEF_TICKER_COOLDOWN_SECONDS,
    BriefWatcherState,
    process_events_for_briefs_once,
)
from app.db.connection import get_conn, init_db
from app.market import PriceCache
from app.routes.portfolio import execute_trade_on_conn

BASE_TS = 1_750_000_000.0  # deterministic cache event timestamps
NOW = 2_000_000_000.0  # deterministic throttle clock (independent of cache time)

NO_BRIEFS = {
    "briefed": 0,
    "skipped_irrelevant": 0,
    "skipped_throttled": 0,
    "skipped_llm_error": 0,
}


@pytest.fixture
def briefs_env(tmp_path, monkeypatch):
    """Isolated DB (seeded watchlist) + cache at fixed prices + fresh state."""
    db_file = str(tmp_path / "briefs.db")
    monkeypatch.setenv("LLM_MOCK", "true")
    init_db(db_file)

    cache = PriceCache()
    # Fixed $100 seeds: a +3% event tick is exactly +3.0 change_percent.
    for ticker in ("AAPL", "MSFT", "GOOGL", "NVDA", "V"):
        cache.update(ticker, 100.0, timestamp=BASE_TS)

    return SimpleNamespace(cache=cache, db=db_file, state=BriefWatcherState())


def _fire_event(cache: PriceCache, ticker: str, ts: float, pct: float = 3.0) -> None:
    """Push a single-tick +pct% move through the cache funnel (records an event)."""
    price = cache.get_price(ticker)
    if price is None:
        price = 100.0
        cache.update(ticker, price, timestamp=ts - 1)  # first tick is flat — no event
    before = len(cache.get_events())
    cache.update(ticker, price * (1 + pct / 100), timestamp=ts)
    assert len(cache.get_events()) == before + 1, "test setup: event not recorded"


def _chat_rows(db_file: str) -> list:
    conn = get_conn(db_file)
    try:
        return conn.execute(
            "SELECT role, content, actions, kind FROM chat_messages "
            "WHERE user_id = 'default' ORDER BY created_at ASC, rowid ASC"
        ).fetchall()
    finally:
        conn.close()


def _remove_from_watchlist(db_file: str, ticker: str) -> None:
    conn = get_conn(db_file)
    try:
        conn.execute(
            "DELETE FROM watchlist WHERE user_id = 'default' AND ticker = ?", (ticker,)
        )
        conn.commit()
    finally:
        conn.close()


def _buy(db_file: str, cache: PriceCache, ticker: str, quantity: float) -> None:
    conn = get_conn(db_file)
    try:
        outcome = execute_trade_on_conn(conn, cache, ticker, "buy", quantity)
        assert outcome["status"] == "executed"
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
class TestBriefGeneration:
    """New relevant events produce kind='brief' rows via the mock LLM path."""

    async def test_event_on_held_ticker_creates_brief(self, briefs_env):
        # Held but NOT watched — proves the positions branch alone qualifies.
        _remove_from_watchlist(briefs_env.db, "AAPL")
        _buy(briefs_env.db, briefs_env.cache, "AAPL", 5)
        _fire_event(briefs_env.cache, "AAPL", BASE_TS + 10)

        counts = await process_events_for_briefs_once(
            briefs_env.cache, briefs_env.db, briefs_env.state, now=NOW
        )
        assert counts["briefed"] == 1

        rows = [r for r in _chat_rows(briefs_env.db) if r["kind"] == "brief"]
        assert len(rows) == 1
        assert rows[0]["role"] == "assistant"
        assert rows[0]["actions"] is None
        assert rows[0]["content"] == (
            "[MOCK BRIEF] AAPL moved +3.0% — review your exposure."
        )

    async def test_event_on_watched_ticker_creates_brief(self, briefs_env):
        # NVDA is in the seeded watchlist; no position exists.
        _fire_event(briefs_env.cache, "NVDA", BASE_TS + 10)

        counts = await process_events_for_briefs_once(
            briefs_env.cache, briefs_env.db, briefs_env.state, now=NOW
        )
        assert counts["briefed"] == 1
        rows = _chat_rows(briefs_env.db)
        assert len(rows) == 1
        assert rows[0]["kind"] == "brief"
        assert "NVDA" in rows[0]["content"]

    async def test_event_on_unrelated_ticker_no_brief(self, briefs_env):
        # ZZZZ is neither watched nor held.
        _fire_event(briefs_env.cache, "ZZZZ", BASE_TS + 10)

        counts = await process_events_for_briefs_once(
            briefs_env.cache, briefs_env.db, briefs_env.state, now=NOW
        )
        assert counts == {**NO_BRIEFS, "skipped_irrelevant": 1}
        assert _chat_rows(briefs_env.db) == []

    async def test_seen_events_not_reprocessed(self, briefs_env):
        _fire_event(briefs_env.cache, "NVDA", BASE_TS + 10)
        first = await process_events_for_briefs_once(
            briefs_env.cache, briefs_env.db, briefs_env.state, now=NOW
        )
        assert first["briefed"] == 1

        # Same events, much later clock: nothing new to process.
        second = await process_events_for_briefs_once(
            briefs_env.cache, briefs_env.db, briefs_env.state, now=NOW + 10_000
        )
        assert second == NO_BRIEFS
        assert len(_chat_rows(briefs_env.db)) == 1


@pytest.mark.asyncio
class TestBriefThrottles:
    """Global 60s + per-ticker 300s cooldowns; throttled events are consumed."""

    async def test_global_throttle_one_brief_per_pass(self, briefs_env):
        # Two relevant events in one pass share one `now` — second is throttled.
        _fire_event(briefs_env.cache, "AAPL", BASE_TS + 10)
        _fire_event(briefs_env.cache, "MSFT", BASE_TS + 11)

        counts = await process_events_for_briefs_once(
            briefs_env.cache, briefs_env.db, briefs_env.state, now=NOW
        )
        assert counts == {**NO_BRIEFS, "briefed": 1, "skipped_throttled": 1}
        assert len(_chat_rows(briefs_env.db)) == 1

        # Throttled MSFT event was consumed — a later pass does NOT retry it.
        counts = await process_events_for_briefs_once(
            briefs_env.cache, briefs_env.db, briefs_env.state, now=NOW + 10_000
        )
        assert counts == NO_BRIEFS

    async def test_global_throttle_across_passes(self, briefs_env):
        _fire_event(briefs_env.cache, "AAPL", BASE_TS + 10)
        counts = await process_events_for_briefs_once(
            briefs_env.cache, briefs_env.db, briefs_env.state, now=NOW
        )
        assert counts["briefed"] == 1

        # 30s later (< 60s global cooldown): a fresh GOOGL event is throttled.
        _fire_event(briefs_env.cache, "GOOGL", BASE_TS + 20)
        counts = await process_events_for_briefs_once(
            briefs_env.cache, briefs_env.db, briefs_env.state, now=NOW + 30
        )
        assert counts == {**NO_BRIEFS, "skipped_throttled": 1}

        # 61s after the first brief: a fresh V event goes through.
        _fire_event(briefs_env.cache, "V", BASE_TS + 30)
        counts = await process_events_for_briefs_once(
            briefs_env.cache,
            briefs_env.db,
            briefs_env.state,
            now=NOW + BRIEF_GLOBAL_COOLDOWN_SECONDS + 1,
        )
        assert counts["briefed"] == 1
        assert len(_chat_rows(briefs_env.db)) == 2

    async def test_per_ticker_cooldown(self, briefs_env):
        _fire_event(briefs_env.cache, "AAPL", BASE_TS + 10)
        counts = await process_events_for_briefs_once(
            briefs_env.cache, briefs_env.db, briefs_env.state, now=NOW
        )
        assert counts["briefed"] == 1

        # Between the two cooldowns (global expired, ticker not): AAPL
        # throttled, while an MSFT event in the same pass is the pass's first
        # brief — AAPL was skipped, so the global throttle doesn't block MSFT.
        mid = (BRIEF_GLOBAL_COOLDOWN_SECONDS + BRIEF_TICKER_COOLDOWN_SECONDS) / 2
        _fire_event(briefs_env.cache, "AAPL", BASE_TS + 50)  # past cache 30s cooldown
        _fire_event(briefs_env.cache, "MSFT", BASE_TS + 51)
        counts = await process_events_for_briefs_once(
            briefs_env.cache, briefs_env.db, briefs_env.state, now=NOW + mid
        )
        assert counts == {**NO_BRIEFS, "briefed": 1, "skipped_throttled": 1}
        assert "MSFT" in _chat_rows(briefs_env.db)[-1]["content"]

        # Past BOTH cooldowns (AAPL's ticker window from NOW, the global
        # window from the MSFT brief at NOW + mid): AAPL briefs again.
        _fire_event(briefs_env.cache, "AAPL", BASE_TS + 90)
        counts = await process_events_for_briefs_once(
            briefs_env.cache,
            briefs_env.db,
            briefs_env.state,
            now=NOW + mid + BRIEF_GLOBAL_COOLDOWN_SECONDS + 1,
        )
        assert counts["briefed"] == 1
        assert "AAPL" in _chat_rows(briefs_env.db)[-1]["content"]


@pytest.mark.asyncio
class TestBriefLLMFailure:
    """LLM errors skip the event without a row and without burning cooldowns."""

    async def test_llm_failure_skips_without_row(self, briefs_env, monkeypatch):
        import litellm

        monkeypatch.setenv("LLM_MOCK", "false")

        def exploding_completion(*args, **kwargs):
            raise RuntimeError("provider down")

        monkeypatch.setattr(litellm, "completion", exploding_completion)

        _fire_event(briefs_env.cache, "NVDA", BASE_TS + 10)
        counts = await process_events_for_briefs_once(
            briefs_env.cache, briefs_env.db, briefs_env.state, now=NOW
        )
        assert counts == {**NO_BRIEFS, "skipped_llm_error": 1}
        assert _chat_rows(briefs_env.db) == []

        # The failed event was consumed — never retried…
        monkeypatch.setenv("LLM_MOCK", "true")
        counts = await process_events_for_briefs_once(
            briefs_env.cache, briefs_env.db, briefs_env.state, now=NOW + 1
        )
        assert counts == NO_BRIEFS

        # …and the failure did not burn the global cooldown: a brand-new event
        # one second after the failure is briefed immediately.
        _fire_event(briefs_env.cache, "MSFT", BASE_TS + 20)
        counts = await process_events_for_briefs_once(
            briefs_env.cache, briefs_env.db, briefs_env.state, now=NOW + 2
        )
        assert counts["briefed"] == 1
        assert len(_chat_rows(briefs_env.db)) == 1


@pytest.mark.asyncio
class TestBriefCompactness:
    """Brief text is hard-capped and the prompt is long-only (chat-flood fix)."""

    async def test_rambling_llm_brief_is_truncated_and_unquoted(
        self, briefs_env, monkeypatch
    ):
        import litellm

        from app.briefs import BRIEF_MAX_CHARS

        monkeypatch.setenv("LLM_MOCK", "false")
        rambling = '"' + "Consider rebalancing your position now. " * 10 + '"'

        def rambling_completion(*args, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=rambling))]
            )

        monkeypatch.setattr(litellm, "completion", rambling_completion)

        _fire_event(briefs_env.cache, "NVDA", BASE_TS + 10)
        counts = await process_events_for_briefs_once(
            briefs_env.cache, briefs_env.db, briefs_env.state, now=NOW
        )
        assert counts["briefed"] == 1

        rows = _chat_rows(briefs_env.db)
        content = rows[0]["content"]
        assert len(content) <= BRIEF_MAX_CHARS
        assert content.endswith("…")
        assert not content.startswith('"')

    async def test_prompt_constrains_length_and_forbids_shorting(self):
        from app.briefs import BRIEF_MAX_CHARS, BRIEF_SYSTEM_PROMPT

        # The parked M2.3 refinement: briefs once suggested short entries the
        # platform cannot execute — the prompt must forbid them explicitly.
        assert "NEVER suggest short selling" in BRIEF_SYSTEM_PROMPT
        assert str(BRIEF_MAX_CHARS) in BRIEF_SYSTEM_PROMPT
