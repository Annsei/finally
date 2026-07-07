"""Midday-break four-phase session cycle (CN-2 §5).

open_seconds/2 (am) -> midday_break_seconds -> open_seconds/2 (pm) ->
break_seconds (closed) -> am (next day). The break is a pause: no on_close /
on_open hook fires, session_id increments only on the day reopen, and equity
market orders are rejected (休市中) during midday.
"""

from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.connection import init_db
from app.market import PriceCache
from app.market.profiles import CN_PROFILE
from app.market.session import SessionClock, session_clock_loop
from app.routes.market import create_market_router
from app.routes.portfolio import create_portfolio_router
from tests.conftest import FakeTime

# open=60 -> am=pm=30; midday=15; closed=15. Start at t=1000.
OPEN, BREAK, MIDDAY, T0 = 60.0, 15.0, 15.0, 1_000.0


def _midday_clock() -> tuple[SessionClock, FakeTime]:
    fake = FakeTime(T0)
    return SessionClock(OPEN, BREAK, now=fake, midday_break_seconds=MIDDAY), fake


class TestPhaseSequence:
    def test_am_midday_pm_closed_am(self):
        clock, fake = _midday_clock()
        assert clock.phase == "am"
        assert clock.is_open is True
        assert clock.session_id == 1

        fake.advance(30.0)  # am (60/2) elapses
        assert clock.tick() == ["midday"]
        assert clock.phase == "midday"
        assert clock.is_open is False  # lunch break — market shut
        assert clock.state == "closed"  # coarse view

        fake.advance(15.0)  # midday elapses
        assert clock.tick() == ["resume"]
        assert clock.phase == "pm"
        assert clock.is_open is True

        fake.advance(30.0)  # pm elapses
        assert clock.tick() == ["close"]
        assert clock.phase == "closed"
        assert clock.session_id == 1  # not yet a new day

        fake.advance(15.0)  # closed elapses
        assert clock.tick() == ["open"]
        assert clock.phase == "am"
        assert clock.session_id == 2  # new trading day

    def test_session_id_only_increments_on_day_reopen(self):
        clock, fake = _midday_clock()
        # Through am -> midday -> pm -> closed, session_id stays 1.
        for seconds in (30.0, 15.0, 30.0):  # am, midday, pm durations
            fake.advance(seconds)
            clock.tick()
            assert clock.session_id == 1
        assert clock.phase == "closed"
        fake.advance(15.0)  # closed -> am (new day)
        clock.tick()
        assert clock.session_id == 2


class TestMiddayIdenticalWhenDisabled:
    def test_midday_zero_is_two_phase(self):
        fake = FakeTime(T0)
        clock = SessionClock(OPEN, BREAK, now=fake, midday_break_seconds=0.0)
        assert clock.phase == "open"
        fake.advance(OPEN)
        assert clock.tick() == ["close"]
        assert clock.phase == "closed"
        # No phase key leaks into the snapshot in two-phase mode.
        assert "phase" not in clock.snapshot()


class TestLoopHooks:
    async def test_midday_runs_no_hooks_only_close_open_do(self):
        clock, fake = _midday_clock()
        events: list[str] = []
        task = asyncio.create_task(
            session_clock_loop(
                clock,
                on_close=lambda: events.append("close"),
                on_open=lambda: events.append("open"),
                interval=0.005,
            )
        )

        async def _wait(pred, timeout=2.0):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if pred():
                    return
                await asyncio.sleep(0.005)
            pytest.fail("condition not met")

        try:
            fake.advance(30.0)  # -> midday (no hook)
            await _wait(lambda: clock.phase == "midday")
            fake.advance(15.0)  # -> pm (no hook)
            await _wait(lambda: clock.phase == "pm")
            assert events == []  # neither on_close nor on_open ran at lunch
            fake.advance(30.0)  # -> closed (on_close)
            await _wait(lambda: events == ["close"])
            fake.advance(15.0)  # -> am (on_open)
            await _wait(lambda: events == ["close", "open"])
        finally:
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest_asyncio.fixture
async def midday_app(tmp_path, monkeypatch, fake_market_source):
    db_file = str(tmp_path / "midday.db")
    monkeypatch.setenv("DB_PATH", db_file)
    init_db(db_file, seed_cash=CN_PROFILE.seed_cash)

    cache = PriceCache()
    cache.update("600036", 35.00)
    fake_market_source.price_cache = cache

    clock, fake = _midday_clock()
    app = FastAPI()
    app.state.market_source = fake_market_source
    app.include_router(create_portfolio_router(cache, db_file, 0.0, clock, CN_PROFILE))
    app.include_router(create_market_router(cache, clock))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, clock, fake


class TestSessionRoute:
    async def test_session_payload_carries_phase(self, midday_app):
        client, clock, fake = midday_app
        data = (await client.get("/api/market/session")).json()
        assert data["phase"] == "am"
        assert data["state"] == "open"
        fake.advance(30.0)
        clock.tick()  # -> midday
        data = (await client.get("/api/market/session")).json()
        assert data["phase"] == "midday"
        assert data["state"] == "closed"

    async def test_market_order_rejected_during_midday_zh(self, midday_app):
        client, clock, fake = midday_app
        fake.advance(30.0)
        clock.tick()  # -> midday (is_open False)
        resp = await client.post(
            "/api/portfolio/trade",
            json={"ticker": "600036", "quantity": 100, "side": "buy"},
        )
        assert resp.status_code == 400
        assert resp.json() == {"error": "休市中"}

    async def test_market_order_ok_in_pm(self, midday_app):
        client, clock, fake = midday_app
        fake.advance(30.0)
        clock.tick()  # midday
        fake.advance(15.0)
        clock.tick()  # -> pm (open again)
        resp = await client.post(
            "/api/portfolio/trade",
            json={"ticker": "600036", "quantity": 100, "side": "buy"},
        )
        assert resp.status_code == 200
