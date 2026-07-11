"""History sync + query API tests (D1 contract §2/§6) — ZERO network.

The router is built with INJECTED fake providers (never the real
yfinance/akshare fetchers), so no test path can reach an external host.

Covers:
- POST /sync: sample upsert + response shape, idempotent re-sync (INSERT OR
  REPLACE — no duplicate rows), auto -> real source, auto fallback to sample
  with the annotating ``error`` field, explicit-source failure rows,
  tickers/years/source validation, the default ticker set, the 10s throttle
  (429), and the Bearer 403 red line (gateway-marker and raw-header forms);
- GET /daily: shape, ascending order, limit default/clamps, non-integer 400,
  missing ticker 400, empty result shape;
- GET /coverage: per-ticker rows, ascending, newest-bar source convention.
"""

from __future__ import annotations

from datetime import date

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.connection import get_conn, init_db
from app.market.history import DailyBar, HistoryFetchError
from app.routes.history import create_history_router


class FakeProvider:
    """Scriptable provider: returns fixed bars or raises, and records calls."""

    def __init__(self, name: str, bars: list[DailyBar] | None = None, error: str | None = None):
        self.name = name
        self._bars = bars or []
        self._error = error
        self.calls: list[str] = []

    def fetch_daily(self, ticker: str, start: date, end: date) -> list[DailyBar]:
        self.calls.append(ticker)
        if self._error is not None:
            raise HistoryFetchError(self._error)
        return self._bars


def _bars(n: int = 30, base: float = 100.0) -> list[DailyBar]:
    out = []
    for i in range(n):
        day = date(2026, 1, 1).fromordinal(date(2026, 1, 1).toordinal() + i)
        px = base + i
        out.append(
            DailyBar(day.isoformat(), px, px + 1.0, px - 1.0, px + 0.5, 1000.0 + i)
        )
    return out


def _build_app(tmp_path, providers, interval=0.0):
    db_file = str(tmp_path / "history.db")
    init_db(db_file)
    app = FastAPI()
    app.include_router(
        create_history_router(
            db_file, providers=providers, min_sync_interval_seconds=interval
        )
    )
    return app, db_file


@pytest_asyncio.fixture
async def api(tmp_path):
    """US-market app with fakes: working yfinance + sample, and a DB handle."""
    providers = {
        "sample": FakeProvider("sample", bars=_bars(25, 50.0)),
        "yfinance": FakeProvider("yfinance", bars=_bars(30, 100.0)),
    }
    app, db_file = _build_app(tmp_path, providers)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        yield type("Ctx", (), {"client": client, "db": db_file, "providers": providers})()


def _row_count(db_file: str) -> int:
    conn = get_conn(db_file)
    try:
        return conn.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]
    finally:
        conn.close()


@pytest.mark.asyncio
class TestSync:
    async def test_sample_sync_shape_and_upsert(self, api):
        resp = await api.client.post(
            "/api/market/history/sync",
            json={"source": "sample", "tickers": ["aapl", "NVDA"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert set(data) == {"results", "total_bars"}
        assert data["total_bars"] == 50
        assert [r["ticker"] for r in data["results"]] == ["AAPL", "NVDA"]
        for row in data["results"]:
            assert row["source"] == "sample"
            assert row["bars"] == 25
            assert "error" not in row  # success rows carry no error key
        assert _row_count(api.db) == 50

    async def test_resync_is_idempotent(self, api):
        body = {"source": "sample", "tickers": ["AAPL"]}
        await api.client.post("/api/market/history/sync", json=body)
        first = _row_count(api.db)
        resp = await api.client.post("/api/market/history/sync", json=body)
        assert resp.status_code == 200
        assert _row_count(api.db) == first  # INSERT OR REPLACE — no dupes

    async def test_auto_uses_real_source_for_market(self, api):
        resp = await api.client.post(
            "/api/market/history/sync", json={"tickers": ["AAPL"]}
        )
        row = resp.json()["results"][0]
        assert row["source"] == "yfinance" and row["bars"] == 30
        assert api.providers["yfinance"].calls == ["AAPL"]
        assert api.providers["sample"].calls == []  # no fallback needed

    async def test_auto_falls_back_to_sample_and_annotates(self, tmp_path):
        providers = {
            "sample": FakeProvider("sample", bars=_bars(25, 50.0)),
            "yfinance": FakeProvider("yfinance", error="rate limited by host"),
        }
        app, db_file = _build_app(tmp_path, providers)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.post(
                "/api/market/history/sync", json={"source": "auto", "tickers": ["AAPL"]}
            )
        data = resp.json()
        row = data["results"][0]
        assert row["source"] == "sample" and row["bars"] == 25
        assert "yfinance: rate limited by host" in row["error"]
        assert data["total_bars"] == 25
        assert _row_count(db_file) == 25  # the fallback rows persisted

    async def test_explicit_source_failure_reports_zero_bars(self, tmp_path):
        providers = {
            "sample": FakeProvider("sample", bars=_bars(25)),
            "yfinance": FakeProvider("yfinance", error="host unreachable"),
        }
        app, _ = _build_app(tmp_path, providers)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.post(
                "/api/market/history/sync",
                json={"source": "yfinance", "tickers": ["AAPL"]},
            )
        data = resp.json()
        assert resp.status_code == 200
        row = data["results"][0]
        assert row["bars"] == 0 and data["total_bars"] == 0
        assert "host unreachable" in row["error"]
        assert providers["sample"].calls == []  # explicit source: NO fallback

    async def test_default_tickers_are_the_profile_watchlist(self, api):
        resp = await api.client.post("/api/market/history/sync", json={"source": "sample"})
        assert resp.status_code == 200
        tickers = [r["ticker"] for r in resp.json()["results"]]
        from app.market.seed_prices import DEFAULT_WATCHLIST

        assert tickers == list(DEFAULT_WATCHLIST)  # us default = 10 equities

    async def test_empty_body_defaults_to_auto(self, api):
        resp = await api.client.post("/api/market/history/sync")
        assert resp.status_code == 200
        assert all(r["source"] == "yfinance" for r in resp.json()["results"])

    @pytest.mark.parametrize(
        "body,fragment",
        [
            ({"source": "akshare"}, "source must be one of"),  # not a us source
            ({"source": "bogus"}, "source must be one of"),
            ({"years": 0}, "years must be an integer between 1 and 10"),
            ({"years": 11}, "years must be an integer between 1 and 10"),
            ({"years": "three"}, "years must be an integer between 1 and 10"),
            ({"tickers": []}, "tickers must be a non-empty list"),
            ({"tickers": [42]}, "tickers must be a non-empty list"),
            ({"tickers": "AAPL"}, "tickers must be a non-empty list"),
        ],
    )
    async def test_validation_rejects_bad_bodies(self, api, body, fragment):
        resp = await api.client.post("/api/market/history/sync", json=body)
        assert resp.status_code == 400
        assert fragment in resp.json()["error"]

    async def test_throttle_returns_429_within_window(self, tmp_path):
        providers = {"sample": FakeProvider("sample", bars=_bars(25))}
        app, _ = _build_app(tmp_path, providers, interval=10.0)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            first = await client.post(
                "/api/market/history/sync", json={"source": "sample", "tickers": ["AAPL"]}
            )
            assert first.status_code == 200
            second = await client.post(
                "/api/market/history/sync", json={"source": "sample", "tickers": ["AAPL"]}
            )
            assert second.status_code == 429
            assert "rate limited" in second.json()["error"]

    async def test_bearer_header_gets_403(self, api):
        """keys.py red line: a leaked key must not trigger real fetches."""
        resp = await api.client.post(
            "/api/market/history/sync",
            json={"source": "sample", "tickers": ["AAPL"]},
            headers={"Authorization": "Bearer fk_abcdef0123456789"},
        )
        assert resp.status_code == 403
        assert "cannot trigger a history sync" in resp.json()["error"]

    async def test_gateway_key_marker_gets_403(self, tmp_path):
        """Even a gateway-authenticated key (request.state marker) is rejected."""
        providers = {"sample": FakeProvider("sample", bars=_bars(25))}
        app, _ = _build_app(tmp_path, providers)

        @app.middleware("http")
        async def stamp_key(request, call_next):  # mimics the api gateway
            request.state.api_key_id = "key-123"
            return await call_next(request)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.post(
                "/api/market/history/sync", json={"source": "sample", "tickers": ["AAPL"]}
            )
        assert resp.status_code == 403

    async def test_cn_profile_selects_akshare_and_cn_market(self, tmp_path):
        from app.market.profiles import CN_PROFILE

        providers = {
            "sample": FakeProvider("sample", bars=_bars(25)),
            "akshare": FakeProvider("akshare", bars=_bars(30)),
        }
        db_file = str(tmp_path / "cn.db")
        init_db(db_file)
        app = FastAPI()
        app.include_router(
            create_history_router(
                db_file, profile=CN_PROFILE, providers=providers,
                min_sync_interval_seconds=0.0,
            )
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.post(
                "/api/market/history/sync", json={"tickers": ["600519"]}
            )
            assert resp.json()["results"][0]["source"] == "akshare"
            # yfinance is not a valid explicit source on the cn market.
            bad = await client.post(
                "/api/market/history/sync", json={"source": "yfinance"}
            )
            assert bad.status_code == 400
            coverage = (await client.get("/api/market/history/coverage")).json()
            assert coverage["market"] == "cn"
            assert coverage["coverage"][0]["ticker"] == "600519"


@pytest.mark.asyncio
class TestDailyAndCoverage:
    async def _seed(self, api, n=30):
        await api.client.post(
            "/api/market/history/sync", json={"source": "sample", "tickers": ["AAPL"]}
        )
        # sample fake stores 25 bars for AAPL

    async def test_daily_shape_and_ascending(self, api):
        await self._seed(api)
        resp = await api.client.get("/api/market/history/daily?ticker=aapl")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data) == {"ticker", "bars", "source", "coverage"}
        assert data["ticker"] == "AAPL"
        assert data["source"] == "sample"
        dates = [b["date"] for b in data["bars"]]
        assert dates == sorted(dates) and len(dates) == 25
        assert set(data["bars"][0]) == {"date", "open", "high", "low", "close", "volume"}
        assert data["coverage"]["count"] == 25
        assert data["coverage"]["from"] == dates[0]
        assert data["coverage"]["to"] == dates[-1]

    async def test_daily_limit_clamps_and_takes_newest(self, api):
        await self._seed(api)
        resp = await api.client.get("/api/market/history/daily?ticker=AAPL&limit=5")
        data = resp.json()
        assert len(data["bars"]) == 5
        # The 5 NEWEST bars, still ascending; coverage stays whole-table.
        assert data["bars"][-1]["date"] == data["coverage"]["to"]
        assert data["coverage"]["count"] == 25
        low = await api.client.get("/api/market/history/daily?ticker=AAPL&limit=0")
        assert len(low.json()["bars"]) == 1  # clamped up to 1
        high = await api.client.get("/api/market/history/daily?ticker=AAPL&limit=99999")
        assert len(high.json()["bars"]) == 25  # clamped to 2600, all rows

    async def test_daily_validation(self, api):
        assert (await api.client.get("/api/market/history/daily")).status_code == 400
        resp = await api.client.get("/api/market/history/daily?ticker=AAPL&limit=abc")
        assert resp.status_code == 400
        assert "limit must be an integer" in resp.json()["error"]

    async def test_daily_unknown_ticker_empty_shape(self, api):
        resp = await api.client.get("/api/market/history/daily?ticker=ZZZZ")
        assert resp.status_code == 200
        data = resp.json()
        assert data["bars"] == [] and data["source"] is None
        assert data["coverage"] == {"from": None, "to": None, "count": 0}

    async def test_coverage_rows_shape(self, api):
        await api.client.post(
            "/api/market/history/sync",
            json={"source": "sample", "tickers": ["NVDA", "AAPL"]},
        )
        resp = await api.client.get("/api/market/history/coverage")
        assert resp.status_code == 200
        data = resp.json()
        rows = data["coverage"]
        assert [r["ticker"] for r in rows] == ["AAPL", "NVDA"]  # ascending
        for row in rows:
            assert set(row) == {"ticker", "from", "to", "count", "source"}
            assert row["count"] == 25 and row["source"] == "sample"
            assert row["from"] <= row["to"]
        assert data["market"] == "us"

    async def test_coverage_source_is_newest_bar(self, tmp_path):
        """A later real-source sync flips the reported source (newest wins)."""
        providers = {
            "sample": FakeProvider("sample", bars=_bars(25, 50.0)),
            "yfinance": FakeProvider("yfinance", bars=_bars(40, 100.0)),
        }
        app, _ = _build_app(tmp_path, providers)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            await client.post(
                "/api/market/history/sync", json={"source": "sample", "tickers": ["AAPL"]}
            )
            await client.post(
                "/api/market/history/sync", json={"source": "yfinance", "tickers": ["AAPL"]}
            )
            rows = (await client.get("/api/market/history/coverage")).json()["coverage"]
        assert rows[0]["source"] == "yfinance"
        assert rows[0]["count"] == 40  # overlapping dates replaced, not doubled
