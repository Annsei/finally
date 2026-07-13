"""GET /api/market/replay — two-state status endpoint (D3 contract §3/§5).

Non-replay sources → exactly ``{"active": false}``; an active
ReplayDataSource → the full status shape read via the thread-safe snapshot.
The session snapshot endpoint's exact shape is pinned elsewhere
(test_market_session.py) and is untouched by replay.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.connection import get_conn, init_db
from app.market.cache import PriceCache
from app.market.replay_source import ReplayConfig, ReplayDataSource
from app.routes.replay import create_replay_router
from tests.conftest import FakeMarketSource

PRE = ("2026-05-29", 99.0, 100.0, 98.0, 99.5, 1_000)
DAY0 = ("2026-06-01", 100.0, 104.0, 97.0, 102.0, 50_000)
DAY1 = ("2026-06-02", 103.0, 108.0, 101.0, 107.0, 60_000)


def make_client(source) -> AsyncClient:
    app = FastAPI()
    app.include_router(create_replay_router(source))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest_asyncio.fixture
async def replay_source(tmp_path):
    db_path = str(tmp_path / "replay.db")
    init_db(db_path)
    conn = get_conn(db_path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO daily_bars (market, ticker, date, open, high, "
            "low, close, volume, source, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                ("us", "AAPL", d, o, h, low, c, v, "sample", "x")
                for d, o, h, low, c, v in (PRE, DAY0, DAY1)
            ],
        )
        conn.commit()
    finally:
        conn.close()
    config = ReplayConfig(
        from_date=DAY0[0],
        to_date=DAY1[0],
        seconds_per_day=30.0,
        break_seconds=2.0,
        loop=False,
    )
    source = ReplayDataSource(
        PriceCache(),
        db_path=db_path,
        market="us",
        session_clock=None,
        universe=None,
        update_interval=0.5,
        config=config,
    )
    await source.start(["AAPL"])
    await source.stop()
    return source


@pytest.mark.asyncio
class TestReplayEndpoint:
    async def test_non_replay_source_reports_inactive_exactly(self):
        async with make_client(FakeMarketSource()) as client:
            response = await client.get("/api/market/replay")
        assert response.status_code == 200
        assert response.json() == {"active": False}

    async def test_no_source_reports_inactive(self):
        async with make_client(None) as client:
            response = await client.get("/api/market/replay")
        assert response.status_code == 200
        assert response.json() == {"active": False}

    async def test_replay_source_reports_full_status_shape(self, replay_source):
        async with make_client(replay_source) as client:
            response = await client.get("/api/market/replay")
        assert response.status_code == 200
        assert response.json() == {
            "active": True,
            "from": DAY0[0],
            "to": DAY1[0],
            "current_date": DAY0[0],
            "day_index": 0,
            "total_days": 2,
            "seconds_per_day": 30.0,
            "loop": False,
            "finished": False,
            "source_hint": "sample",
        }

    async def test_status_tracks_progress_and_finished(self, replay_source):
        # Force the finished state through the source's own advance logic.
        replay_source._day_index = 1
        replay_source._advance_day()  # past the end, loop=False -> finished
        async with make_client(replay_source) as client:
            payload = (await client.get("/api/market/replay")).json()
        assert payload["active"] is True
        assert payload["finished"] is True
        assert payload["current_date"] == DAY1[0]
        assert payload["day_index"] == 1
