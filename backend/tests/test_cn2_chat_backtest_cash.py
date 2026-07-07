"""CN-2 §7 regression: the AI-run chat backtest opens the account with the
active profile's seed cash — mirroring routes/backtest.py — not the US $10k.

Board-lot economics make the bug observable end-to-end: 000858 seeds at ¥140,
so a 100-share (one lot) buy is ¥14,000 notional. Under a US-sized $10,000
account every entry is rejected for insufficient cash (0 fires, ¥0 commission);
under the CN profile's ¥100,000 seed cash the strategy trades normally. The
chat pipeline must behave like the /api/backtest route, which passes
starting_cash=profile.seed_cash.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import app.routes.chat as chat_module
from app.db.connection import init_db
from app.market import PriceCache
from app.market.profiles import CN_PROFILE
from app.routes.chat import create_chat_router
from tests.test_chat_agent import _fake_completion_factory

CN_TICKER = "000858"  # seeds at ¥140 → one 100-share lot is ¥14,000 notional
CN_ANCHOR = CN_PROFILE.universe.seed_prices[CN_TICKER]

# A price_above/1 trigger fires on every bar, so a working account round-trips
# each day; a $10k account never affords the ¥14,000 lot.
BACKTEST_PAYLOAD = {
    "message": "Backtested your Wuliangye dip strategy.",
    "backtests": [
        {
            "ticker": CN_TICKER,
            "trigger_type": "price_above",
            "threshold": 1,
            "quantity": 100,  # exactly one board lot
            "take_profit_pct": 2,
            "days": 10,
            "runs": 1,
        }
    ],
}


@pytest_asyncio.fixture
async def cn_chat_client(tmp_path, monkeypatch):
    """Chat router built with the CN profile (¥100k seed cash), real LLM path."""
    db_file = str(tmp_path / "cn_chat.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("LLM_MOCK", "false")  # drive the structured-output path
    init_db(db_file)

    price_cache = PriceCache()
    price_cache.update(CN_TICKER, CN_ANCHOR)

    test_app = FastAPI()
    test_app.include_router(
        create_chat_router(price_cache, db_file, profile=CN_PROFILE)
    )
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_chat_backtest_uses_profile_seed_cash(cn_chat_client, monkeypatch):
    """End-to-end: the CN board-lot strategy trades (fires>0, commission>0)."""
    import litellm

    monkeypatch.setattr(
        litellm, "completion", _fake_completion_factory(BACKTEST_PAYLOAD)
    )

    resp = await cn_chat_client.post("/api/chat/", json={"message": "backtest it"})
    assert resp.status_code == 200
    outcomes = resp.json()["backtests"]
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome["status"] == "completed"
    stats = outcome["stats"]
    # With the ¥100k seed cash the ¥14,000 lot is affordable: entries fire and
    # pay commission, and none are turned away for insufficient cash.
    assert stats["fires"] > 0
    assert stats["commission_paid"] > 0.0
    assert stats["rejections"]["insufficient_cash"] == 0


@pytest.mark.asyncio
async def test_chat_backtest_passes_seed_cash_to_engine(cn_chat_client, monkeypatch):
    """The exact starting_cash forwarded to run_backtest is the profile's seed."""
    import litellm

    monkeypatch.setattr(
        litellm, "completion", _fake_completion_factory(BACKTEST_PAYLOAD)
    )

    captured: dict = {}
    real_run_backtest = chat_module.run_backtest

    def spy_run_backtest(config, **kwargs):
        captured["starting_cash"] = kwargs.get("starting_cash")
        return real_run_backtest(config, **kwargs)

    monkeypatch.setattr(chat_module, "run_backtest", spy_run_backtest)

    resp = await cn_chat_client.post("/api/chat/", json={"message": "backtest it"})
    assert resp.status_code == 200
    # Mirrors routes/backtest.py: starting_cash == profile.seed_cash (¥100k),
    # never the run_backtest default $10k.
    assert captured["starting_cash"] == CN_PROFILE.seed_cash
    assert captured["starting_cash"] == 100_000.0
