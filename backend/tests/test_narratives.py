"""Tests for the LLM news-narrative enricher (M3.2a).

Drives ``process_events_for_narratives_once`` directly with controlled
``now`` timestamps so the 10s global throttle is deterministic. Events are
fired through the real cache funnel (``PriceCache.update`` with a >=1% tick
move). The enricher covers ALL tickers (not only held/watched) and attaches
narratives to events in the cache via ``set_event_narrative`` — the briefs
pass (M2.3) keeps its own independent state and still works alongside.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.briefs import (
    NARRATIVE_COOLDOWN_SECONDS,
    BriefWatcherState,
    NarrativeEnricherState,
    process_events_for_briefs_once,
    process_events_for_narratives_once,
)
from app.db.connection import init_db
from app.market import PriceCache

BASE_TS = 1_750_000_000.0  # deterministic cache event timestamps
NOW = 2_000_000_000.0  # deterministic throttle clock (independent of cache time)

NO_NARRATIVES = {
    "enriched": 0,
    "skipped_throttled": 0,
    "skipped_llm_error": 0,
    "skipped_evicted": 0,
}


@pytest.fixture
def narratives_env(monkeypatch):
    """Cache at fixed prices + fresh enricher state, LLM mocked."""
    monkeypatch.setenv("LLM_MOCK", "true")
    cache = PriceCache()
    # Fixed $100 seeds: a +3% event tick is exactly +3.0 change_percent.
    for ticker in ("AAPL", "MSFT", "NVDA", "ZZZZ"):
        cache.update(ticker, 100.0, timestamp=BASE_TS)
    return SimpleNamespace(cache=cache, state=NarrativeEnricherState())


def _fire_event(cache: PriceCache, ticker: str, ts: float, pct: float = 3.0) -> None:
    """Push a single-tick +pct% move through the cache funnel (records an event)."""
    price = cache.get_price(ticker)
    if price is None:
        price = 100.0
        cache.update(ticker, price, timestamp=ts - 1)  # first tick is flat — no event
    before = len(cache.get_events())
    cache.update(ticker, price * (1 + pct / 100), timestamp=ts)
    assert len(cache.get_events()) == before + 1, "test setup: event not recorded"


@pytest.mark.asyncio
class TestNarrativeEnrichment:
    """New events gain a narrative via the mock LLM path — all tickers."""

    async def test_new_event_gets_mock_narrative(self, narratives_env):
        _fire_event(narratives_env.cache, "NVDA", BASE_TS + 10)

        counts = await process_events_for_narratives_once(
            narratives_env.cache, narratives_env.state, now=NOW
        )
        assert counts == {**NO_NARRATIVES, "enriched": 1}

        event = narratives_env.cache.get_events()[0]
        assert event.narrative == "[MOCK NEWS] NVDA surges +3.0% in sudden move"
        assert event.to_dict()["narrative"] == event.narrative

    async def test_unwatched_unheld_ticker_still_enriched(self, narratives_env):
        """Unlike briefs, the enricher covers ALL tickers (ZZZZ is neither)."""
        _fire_event(narratives_env.cache, "ZZZZ", BASE_TS + 10)

        counts = await process_events_for_narratives_once(
            narratives_env.cache, narratives_env.state, now=NOW
        )
        assert counts["enriched"] == 1
        assert narratives_env.cache.get_events()[0].narrative is not None

    async def test_seen_events_not_reprocessed(self, narratives_env):
        _fire_event(narratives_env.cache, "NVDA", BASE_TS + 10)
        first = await process_events_for_narratives_once(
            narratives_env.cache, narratives_env.state, now=NOW
        )
        assert first["enriched"] == 1

        second = await process_events_for_narratives_once(
            narratives_env.cache, narratives_env.state, now=NOW + 10_000
        )
        assert second == NO_NARRATIVES


@pytest.mark.asyncio
class TestNarrativeThrottle:
    """Global 10s cooldown; throttled events stay template-only forever."""

    async def test_second_event_in_pass_is_throttled_and_consumed(self, narratives_env):
        _fire_event(narratives_env.cache, "AAPL", BASE_TS + 10)
        _fire_event(narratives_env.cache, "MSFT", BASE_TS + 11)

        counts = await process_events_for_narratives_once(
            narratives_env.cache, narratives_env.state, now=NOW
        )
        assert counts == {**NO_NARRATIVES, "enriched": 1, "skipped_throttled": 1}

        events = narratives_env.cache.get_events()  # newest first
        assert events[1].ticker == "AAPL" and events[1].narrative is not None
        assert events[0].ticker == "MSFT" and events[0].narrative is None

        # The throttled MSFT event was consumed — a later pass never retries it.
        counts = await process_events_for_narratives_once(
            narratives_env.cache, narratives_env.state, now=NOW + 10_000
        )
        assert counts == NO_NARRATIVES
        assert narratives_env.cache.get_events()[0].narrative is None

    async def test_throttle_across_passes_with_injected_time(self, narratives_env):
        _fire_event(narratives_env.cache, "AAPL", BASE_TS + 10)
        counts = await process_events_for_narratives_once(
            narratives_env.cache, narratives_env.state, now=NOW
        )
        assert counts["enriched"] == 1

        # 5s later (< 10s cooldown): a fresh MSFT event is throttled.
        _fire_event(narratives_env.cache, "MSFT", BASE_TS + 20)
        counts = await process_events_for_narratives_once(
            narratives_env.cache, narratives_env.state, now=NOW + 5
        )
        assert counts == {**NO_NARRATIVES, "skipped_throttled": 1}

        # Just past the cooldown: a fresh NVDA event is enriched.
        _fire_event(narratives_env.cache, "NVDA", BASE_TS + 30)
        counts = await process_events_for_narratives_once(
            narratives_env.cache,
            narratives_env.state,
            now=NOW + NARRATIVE_COOLDOWN_SECONDS + 1,
        )
        assert counts["enriched"] == 1


@pytest.mark.asyncio
class TestNarrativeLLMFailure:
    """LLM errors log-and-skip: event consumed, narrative stays null."""

    async def test_llm_failure_leaves_null_and_consumes(
        self, narratives_env, monkeypatch
    ):
        import litellm

        monkeypatch.setenv("LLM_MOCK", "false")

        def exploding_completion(*args, **kwargs):
            raise RuntimeError("provider down")

        monkeypatch.setattr(litellm, "completion", exploding_completion)

        _fire_event(narratives_env.cache, "NVDA", BASE_TS + 10)
        counts = await process_events_for_narratives_once(
            narratives_env.cache, narratives_env.state, now=NOW
        )
        assert counts == {**NO_NARRATIVES, "skipped_llm_error": 1}
        assert narratives_env.cache.get_events()[0].narrative is None

        # Consumed — never retried, even with the LLM healthy again…
        monkeypatch.setenv("LLM_MOCK", "true")
        counts = await process_events_for_narratives_once(
            narratives_env.cache, narratives_env.state, now=NOW + 1
        )
        assert counts == NO_NARRATIVES
        assert narratives_env.cache.get_events()[0].narrative is None

        # …and the failure did not burn the 10s cooldown: a brand-new event
        # one second later is enriched immediately.
        _fire_event(narratives_env.cache, "MSFT", BASE_TS + 20)
        counts = await process_events_for_narratives_once(
            narratives_env.cache, narratives_env.state, now=NOW + 2
        )
        assert counts["enriched"] == 1


@pytest.mark.asyncio
class TestNarrativesAlongsideBriefs:
    """The enricher and the briefs pass coexist on the same event feed."""

    async def test_brief_still_posted_for_enriched_event(
        self, narratives_env, tmp_path
    ):
        """One event -> narrative attached AND a brief row for a watched ticker."""
        db_file = str(tmp_path / "narratives.db")
        init_db(db_file)  # seeds the default watchlist (NVDA included)

        _fire_event(narratives_env.cache, "NVDA", BASE_TS + 10)

        narrative_counts = await process_events_for_narratives_once(
            narratives_env.cache, narratives_env.state, now=NOW
        )
        brief_counts = await process_events_for_briefs_once(
            narratives_env.cache, db_file, BriefWatcherState(), now=NOW
        )

        assert narrative_counts["enriched"] == 1
        assert brief_counts["briefed"] == 1
        assert narratives_env.cache.get_events()[0].narrative is not None

        from app.db.connection import get_conn

        conn = get_conn(db_file)
        try:
            rows = conn.execute(
                "SELECT kind, content FROM chat_messages WHERE user_id = 'default'"
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 1
        assert rows[0]["kind"] == "brief"
        assert "NVDA" in rows[0]["content"]
