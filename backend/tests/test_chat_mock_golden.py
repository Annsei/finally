"""Golden-sample regression for the chat LLM_MOCK branches (P2 §7/§10).

The P2 contract freezes the default and '回测'/'backtest' keyword LLM_MOCK
outputs byte-for-byte while the §7 strategies action lands. The four fixtures
in tests/golden/ were captured from the pre-§7 implementation:

- chat_mock_default.json      US profile-less router, "hello there" turn
- chat_mock_backtest.json     US profile-less router, "backtest ..." turn
- chat_mock_default_zh.json   CN-profile router (zh mocks), default turn
- chat_mock_backtest_zh.json  CN-profile router (zh mocks), backtest turn

Determinism knobs (exactly what the capture used):
- ``uuid.uuid4`` is patched to a counting fake so the executed AAPL trade's
  ``trade_id`` is stable (the watchlist insert consumes #1, the trade #2).
- ``random.randint`` is patched to return 4242 so the backtest branch's
  drawn seed — echoed in the config and driving every stat — is stable.
- The US cache is seeded with SEED_PRICES (AAPL 190 / NVDA 800 anchors);
  the CN cache with the CN universe prices plus AAPL at 190.0 (so the
  default zh mock's 5-share buy fails the 整手 lot check, and the backtest
  zh mock's NVDA fails "Ticker not found" — the captured outcomes).

Comparison is canonical-JSON equality of the FULL response body against the
stored fixture — any key added, removed, or changed in these two branches
fails the suite.

D4 §2.5 adds two research fixtures on the same machinery (the four above
stay byte-identical — their messages contain neither 'research' nor '研究'):

- chat_mock_research.json     US router, "Research momentum strategies for
                              AAPL" turn (3 ranked template candidates)
- chat_mock_research_zh.json  CN router, "帮我研究一下 600519 的策略" turn
                              (all candidates zero-trade — ¥20k cash_pct
                              cannot buy a 600519 lot — recommendation null)

Their determinism needs nothing new: strategy/run ids come from the patched
uuid4 counter, the seeded COMMITTED sample bars have fixed dates/prices, and
the D1 history replay is RNG-free (seed is null), so the full response body
— stats included — is exactly reproducible and byte-compared like the rest.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.connection import get_conn, init_db
from app.market import PriceCache
from app.market.history import SampleProvider, upsert_daily_bars
from app.market.profiles import CN_PROFILE
from app.market.seed_prices import SEED_PRICES
from app.routes.chat import create_chat_router

GOLDEN_DIR = Path(__file__).parent / "golden"


def _load_golden(name: str) -> dict:
    return json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8"))


def _canonical(payload) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _patch_determinism(monkeypatch) -> None:
    """Deterministic uuid4 counter + fixed backtest seed (capture parity)."""
    counter = {"n": 0}

    def fake_uuid4():
        counter["n"] += 1
        return f"00000000-0000-0000-0000-{counter['n']:012d}"

    monkeypatch.setattr("uuid.uuid4", fake_uuid4)
    monkeypatch.setattr("random.randint", lambda a, b: 4242)


def _seed_sample_bars(db_file: str, market: str, ticker: str) -> None:
    """Store the committed sample series for one ticker (capture parity).

    The committed CSVs have FIXED dates and prices, so the D4 research
    turn's history backtests — and therefore the full response body — are
    deterministic. The fetched_at stamp never reaches a response.
    """
    bars = SampleProvider(market).fetch_daily(
        ticker, date(2020, 1, 1), date(2026, 7, 1)
    )
    conn = get_conn(db_file)
    try:
        upsert_daily_bars(
            conn,
            market=market,
            ticker=ticker,
            bars=bars,
            source="sample",
            fetched_at="2026-07-01T00:00:00+00:00",
        )
        conn.commit()
    finally:
        conn.close()


@pytest_asyncio.fixture
async def us_golden_client(tmp_path, monkeypatch):
    """Profile-less (US) chat router, LLM_MOCK=true, deterministic ids/seed."""
    db_file = str(tmp_path / "us_golden.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("LLM_MOCK", "true")
    init_db(db_file)
    _patch_determinism(monkeypatch)

    price_cache = PriceCache()
    for ticker, price in SEED_PRICES.items():
        price_cache.update(ticker, price)

    test_app = FastAPI()
    test_app.include_router(create_chat_router(price_cache, db_file))
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        yield client


@pytest_asyncio.fixture
async def cn_golden_client(tmp_path, monkeypatch):
    """CN-profile chat router, LLM_MOCK=true, deterministic ids/seed.

    Mirrors the cn3 mock fixture: CN universe prices plus AAPL at 190.0 so
    the default zh mock's buy reaches (and fails) the 整手 lot check while
    NVDA stays unknown for the backtest branch.
    """
    db_file = str(tmp_path / "cn_golden.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("LLM_MOCK", "true")
    init_db(
        db_file,
        seed_cash=CN_PROFILE.seed_cash,
        default_watchlist=list(CN_PROFILE.universe.default_watchlist),
    )
    _patch_determinism(monkeypatch)

    price_cache = PriceCache()
    for ticker, price in CN_PROFILE.universe.seed_prices.items():
        price_cache.update(ticker, price)
    price_cache.update("AAPL", 190.0)

    test_app = FastAPI()
    test_app.include_router(
        create_chat_router(price_cache, db_file, profile=CN_PROFILE)
    )
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.asyncio
class TestChatMockGoldenUS:
    async def test_default_branch_matches_golden(self, us_golden_client):
        resp = await us_golden_client.post(
            "/api/chat/", json={"message": "hello there"}
        )
        assert resp.status_code == 200
        assert _canonical(resp.json()) == _canonical(
            _load_golden("chat_mock_default.json")
        )

    async def test_backtest_branch_matches_golden(self, us_golden_client):
        resp = await us_golden_client.post(
            "/api/chat/", json={"message": "backtest a dip buy on NVDA"}
        )
        assert resp.status_code == 200
        assert _canonical(resp.json()) == _canonical(
            _load_golden("chat_mock_backtest.json")
        )


@pytest.mark.asyncio
class TestChatMockGoldenZH:
    async def test_default_branch_matches_golden(self, cn_golden_client):
        resp = await cn_golden_client.post(
            "/api/chat/", json={"message": "你好"}
        )
        assert resp.status_code == 200
        assert _canonical(resp.json()) == _canonical(
            _load_golden("chat_mock_default_zh.json")
        )

    async def test_backtest_branch_matches_golden(self, cn_golden_client):
        resp = await cn_golden_client.post(
            "/api/chat/", json={"message": "帮我回测一下这个策略"}
        )
        assert resp.status_code == 200
        assert _canonical(resp.json()) == _canonical(
            _load_golden("chat_mock_backtest_zh.json")
        )


# ---------------------------------------------------------------------------
# D4 §2.5 — the research branch goldens (fixtures mirror the two above plus
# the committed sample bars the history backtests replay)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def us_research_golden_client(tmp_path, monkeypatch):
    """us_golden_client + the committed AAPL sample bars (D4 research)."""
    db_file = str(tmp_path / "us_research_golden.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("LLM_MOCK", "true")
    init_db(db_file)
    _seed_sample_bars(db_file, "us", "AAPL")
    _patch_determinism(monkeypatch)

    price_cache = PriceCache()
    for ticker, price in SEED_PRICES.items():
        price_cache.update(ticker, price)

    test_app = FastAPI()
    test_app.include_router(create_chat_router(price_cache, db_file))
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        yield client


@pytest_asyncio.fixture
async def cn_research_golden_client(tmp_path, monkeypatch):
    """cn_golden_client + the committed 600519 sample bars (D4 research)."""
    db_file = str(tmp_path / "cn_research_golden.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("LLM_MOCK", "true")
    init_db(
        db_file,
        seed_cash=CN_PROFILE.seed_cash,
        default_watchlist=list(CN_PROFILE.universe.default_watchlist),
    )
    _seed_sample_bars(db_file, "cn", "600519")
    _patch_determinism(monkeypatch)

    price_cache = PriceCache()
    for ticker, price in CN_PROFILE.universe.seed_prices.items():
        price_cache.update(ticker, price)
    price_cache.update("AAPL", 190.0)

    test_app = FastAPI()
    test_app.include_router(
        create_chat_router(price_cache, db_file, profile=CN_PROFILE)
    )
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.asyncio
class TestChatMockGoldenResearch:
    async def test_us_research_branch_matches_golden(
        self, us_research_golden_client
    ):
        resp = await us_research_golden_client.post(
            "/api/chat/", json={"message": "Research momentum strategies for AAPL"}
        )
        assert resp.status_code == 200
        assert _canonical(resp.json()) == _canonical(
            _load_golden("chat_mock_research.json")
        )

    async def test_zh_research_branch_matches_golden(
        self, cn_research_golden_client
    ):
        resp = await cn_research_golden_client.post(
            "/api/chat/", json={"message": "帮我研究一下 600519 的策略"}
        )
        assert resp.status_code == 200
        assert _canonical(resp.json()) == _canonical(
            _load_golden("chat_mock_research_zh.json")
        )
