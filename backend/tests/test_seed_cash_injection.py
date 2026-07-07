"""Leaderboard / seasons seed-cash injection (CN-1).

The return-percent baseline and the season-reset cash amount both come from
the injected ``seed_cash`` (main.py passes the active profile's value); the
defaults keep the US $10,000 behavior.
"""

from __future__ import annotations

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.connection import get_conn, init_db
from app.market import PriceCache
from app.market.seed_prices_cn import CN_DEFAULT_WATCHLIST, CN_SEED_PRICES
from app.routes.leaderboard import compute_standings, create_leaderboard_router
from app.routes.seasons import create_seasons_router

CN_SEED_CASH = 100_000.0


class TestComputeStandingsSeedCash:
    """compute_standings(seed_cash=...) shifts only the return% baseline."""

    def _seeded_conn(self, tmp_path, cash: float):
        db_file = str(tmp_path / "standings.db")
        init_db(db_file, seed_cash=cash, default_watchlist=list(CN_DEFAULT_WATCHLIST))
        return get_conn(db_file)

    def test_cn_baseline_gives_zero_return_at_seed(self, tmp_path):
        conn = self._seeded_conn(tmp_path, CN_SEED_CASH)
        try:
            entries = compute_standings(conn, PriceCache(), seed_cash=CN_SEED_CASH)
        finally:
            conn.close()
        assert entries[0]["total_value"] == CN_SEED_CASH
        assert entries[0]["return_pct"] == 0.0

    def test_default_baseline_still_10000(self, tmp_path):
        """Without injection a ¥100k balance reads as +900% vs $10k — the
        pre-CN-1 math, untouched."""
        conn = self._seeded_conn(tmp_path, CN_SEED_CASH)
        try:
            entries = compute_standings(conn, PriceCache())
        finally:
            conn.close()
        assert entries[0]["return_pct"] == 900.0

    def test_cn_baseline_with_gain(self, tmp_path):
        conn = self._seeded_conn(tmp_path, CN_SEED_CASH)
        try:
            conn.execute(
                "UPDATE users_profile SET cash_balance = 150000.0 WHERE id = 'default'"
            )
            conn.commit()
            entries = compute_standings(conn, PriceCache(), seed_cash=CN_SEED_CASH)
        finally:
            conn.close()
        assert entries[0]["return_pct"] == 50.0


@pytest_asyncio.fixture
async def cn_arena(tmp_path):
    """Leaderboard + seasons app wired with CN seed cash (as main.py would)."""
    db_file = str(tmp_path / "cn_arena.db")
    init_db(db_file, seed_cash=CN_SEED_CASH, default_watchlist=list(CN_DEFAULT_WATCHLIST))

    price_cache = PriceCache()
    for ticker, price in CN_SEED_PRICES.items():
        price_cache.update(ticker, price)

    app = FastAPI()
    app.include_router(
        create_leaderboard_router(price_cache, db_file, seed_cash=CN_SEED_CASH)
    )
    app.include_router(create_seasons_router(price_cache, db_file, seed_cash=CN_SEED_CASH))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, db_file


class TestLeaderboardSeedCashInjection:
    async def test_fresh_cn_user_has_zero_return(self, cn_arena):
        client, _ = cn_arena
        response = await client.get("/api/leaderboard")
        assert response.status_code == 200
        entries = response.json()["entries"]
        assert entries[0]["total_value"] == CN_SEED_CASH
        assert entries[0]["return_pct"] == 0.0


class TestSeasonResetSeedCashInjection:
    async def test_reset_restores_cn_seed_cash(self, cn_arena):
        client, db_file = cn_arena
        conn = get_conn(db_file)
        conn.execute("UPDATE users_profile SET cash_balance = 150000.0 WHERE id = 'default'")
        conn.commit()
        conn.close()

        response = await client.post("/api/season/reset", json={"confirm": True})
        assert response.status_code == 200

        # Archived standings use the CN baseline: 150k on 100k = +50%.
        entries = response.json()["archived"]["entries"]
        assert entries[0]["final_value"] == 150_000.0
        assert entries[0]["return_pct"] == 50.0

        # And every user restarts at the CN seed cash, not $10,000.
        conn = get_conn(db_file)
        try:
            cash = conn.execute(
                "SELECT cash_balance FROM users_profile WHERE id = 'default'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert cash == CN_SEED_CASH
