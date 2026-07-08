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
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.connection import init_db
from app.market import PriceCache
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
