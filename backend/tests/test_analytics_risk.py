"""Tests for the D2 §4 portfolio risk keys: var_95_pct / beta / risk_window_bars.

The metrics live inline in GET /api/portfolio/analytics and read the
daily_bars table on the same connection. This module pins:

- the pure percentile helper (numpy-style linear interpolation),
- ``_risk_metrics`` against HAND-COMPUTED fixtures (single-asset beta=1 and
  a two-asset 0.6/0.4 book vs a three-ticker equal-weight benchmark),
- the common-window alignment (intersection of per-ticker coverage, ≤60
  clamp) and the market partition scoping,
- every null path (no positions / <20 common bars / zero benchmark
  variance / a held ticker without bars) with risk_window_bars = 0,
- the additive-response contract: a profile-configured router appends
  EXACTLY the three new keys, and a profile-less (pre-D2 construction)
  router's response stays byte-identical — the existing-keys regression.

No network, no optional packages: bars are inserted straight into SQLite.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.connection import get_conn, init_db
from app.market import PriceCache
from app.market.profiles import US_PROFILE
from app.market.seed_prices import SEED_PRICES
from app.routes.portfolio import (
    MIN_RISK_BARS,
    RISK_WINDOW_BARS,
    _percentile,
    _risk_metrics,
    create_portfolio_router,
)

RISK_KEYS = {"var_95_pct", "beta", "risk_window_bars"}
# The pre-D2 analytics contract (mirrors tests/test_analytics.py).
LEGACY_KEYS = {
    "total_trades", "sell_trades", "win_rate", "realized_pnl",
    "max_drawdown_pct", "sharpe", "best_trade", "worst_trade",
    "sector_allocation",
}

BASE_DATE = date(2026, 1, 1)


def _dates(count: int, offset: int = 0) -> list[str]:
    return [(BASE_DATE + timedelta(days=offset + i)).isoformat() for i in range(count)]


def _closes_from_returns(start: float, returns: list[float]) -> list[float]:
    closes = [start]
    for r in returns:
        closes.append(closes[-1] * (1.0 + r))
    return closes


def _insert_bars(
    db_file: str, ticker: str, closes: list[float], *,
    offset: int = 0, market: str = "us",
) -> None:
    conn = get_conn(db_file)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO daily_bars "
            "(market, ticker, date, open, high, low, close, volume, source, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'sample', '2026-01-01T00:00:00+00:00')",
            [
                (market, ticker, d, c, c, c, c, 1000.0)
                for d, c in zip(_dates(len(closes), offset), closes)
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _insert_position(db_file: str, ticker: str, quantity: float, avg_cost: float) -> None:
    conn = get_conn(db_file)
    try:
        conn.execute(
            "INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at) "
            "VALUES (?, 'default', ?, ?, ?, '2026-01-01T00:00:00+00:00')",
            (str(uuid.uuid4()), ticker, quantity, avg_cost),
        )
        conn.commit()
    finally:
        conn.close()


# --- Hand-computed fixture A: single asset, benchmark = itself --------------
# 20 returns → 21 bars. Sorted returns: [-0.04, -0.02, -0.01 ×9, 0.01 ×9].
# 5th-percentile position = 0.05 × 19 = 0.95 → interpolate the two lowest:
# -0.04×0.05 + -0.02×0.95 = -0.021 → VaR = 2.10. Benchmark = the same
# series (single constituent) → beta = cov(x,x)/var(x) = 1.0 exactly.
SINGLE_RETURNS = [-0.04, -0.02] + [0.01] * 9 + [-0.01] * 9

# --- Hand-computed fixture B: two assets 0.6/0.4 vs 3-ticker benchmark ------
# Four repeats of 5-day return cycles (20 returns → 21 bars):
#   r_p cycle = 0.6·a + 0.4·b = [0.002, -0.004, 0.022, -0.012, 0.002]
#   sorted r_p has -0.012 in the four lowest slots → p5 = -0.012 → VaR 1.20
#   benchmark = mean(a, b, n); beta = cov/var = 66/43 = 1.5348… → 1.53
RETURNS_A = [0.01, -0.02, 0.03, 0.00, -0.01] * 4
RETURNS_B = [-0.01, 0.02, 0.01, -0.03, 0.02] * 4
RETURNS_N = [0.02, 0.00, -0.01, 0.01, -0.02] * 4


class TestPercentileHelper:
    def test_single_value(self):
        assert _percentile([7.5], 0.05) == 7.5

    def test_exact_index(self):
        values = [float(i) for i in range(21)]  # pos = 0.05*20 = 1.0 exactly
        assert _percentile(values, 0.05) == 1.0

    def test_interpolates_between_neighbors(self):
        assert _percentile([0.0, 10.0], 0.05) == pytest.approx(0.5)
        assert _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.5) == 3.0

    def test_endpoints(self):
        values = [1.0, 2.0, 9.0]
        assert _percentile(values, 0.0) == 1.0
        assert _percentile(values, 1.0) == 9.0


@pytest.fixture
def risk_db(tmp_path, monkeypatch):
    """Isolated DB + seeded cache + an open-connection helper for _risk_metrics."""
    db_file = str(tmp_path / "risk.db")
    monkeypatch.setenv("DB_PATH", db_file)
    init_db(db_file)
    price_cache = PriceCache()
    for ticker, price in SEED_PRICES.items():
        price_cache.update(ticker, price)
    return SimpleNamespace(db_file=db_file, price_cache=price_cache)


def _metrics(env, benchmark: list[str], market: str = "us"):
    conn = get_conn(env.db_file)
    try:
        return _risk_metrics(
            conn, env.price_cache, "default", market=market, benchmark_tickers=benchmark
        )
    finally:
        conn.close()


class TestRiskMetricsHandComputed:
    """_risk_metrics against fixtures whose answers are worked by hand."""

    def test_single_asset_var_interpolation_and_beta_one(self, risk_db):
        env = risk_db
        env.price_cache.update("AAPL", 100.0)
        _insert_position(env.db_file, "AAPL", 10, 100.0)
        _insert_bars(env.db_file, "AAPL", _closes_from_returns(100.0, SINGLE_RETURNS))

        var_95_pct, beta, bars = _metrics(env, ["AAPL", "GOOGL"])
        assert bars == 21
        assert var_95_pct == 2.1
        assert beta == 1.0  # the portfolio IS the (single-ticker) benchmark

    def test_two_asset_book_against_equal_weight_benchmark(self, risk_db):
        env = risk_db
        env.price_cache.update("AAPL", 100.0)
        env.price_cache.update("MSFT", 100.0)
        _insert_position(env.db_file, "AAPL", 6, 100.0)  # weight 0.6
        _insert_position(env.db_file, "MSFT", 4, 100.0)  # weight 0.4
        _insert_bars(env.db_file, "AAPL", _closes_from_returns(100.0, RETURNS_A))
        _insert_bars(env.db_file, "MSFT", _closes_from_returns(50.0, RETURNS_B))
        _insert_bars(env.db_file, "NVDA", _closes_from_returns(200.0, RETURNS_N))

        var_95_pct, beta, bars = _metrics(env, list(US_PROFILE.universe.default_watchlist))
        assert bars == 21
        assert var_95_pct == 1.2  # −(−0.012)×100
        assert beta == 1.53  # 66/43 rounded

    def test_exactly_min_bars_is_computed(self, risk_db):
        env = risk_db
        env.price_cache.update("AAPL", 100.0)
        _insert_position(env.db_file, "AAPL", 10, 100.0)
        # 19 returns → exactly MIN_RISK_BARS (20) bars: p5 position = 0.9 →
        # -0.04×0.1 + -0.02×0.9 = -0.022 → VaR 2.20.
        returns = [-0.04, -0.02] + [0.01] * 9 + [-0.01] * 8
        _insert_bars(env.db_file, "AAPL", _closes_from_returns(100.0, returns))

        var_95_pct, beta, bars = _metrics(env, ["AAPL"])
        assert bars == MIN_RISK_BARS
        assert var_95_pct == 2.2
        assert beta == 1.0


class TestRiskMetricsWindow:
    """Common-window alignment, ≤60 clamp, market partition scoping."""

    def test_window_is_the_common_coverage_intersection(self, risk_db):
        env = risk_db
        env.price_cache.update("AAPL", 100.0)
        env.price_cache.update("MSFT", 100.0)
        _insert_position(env.db_file, "AAPL", 1, 100.0)
        _insert_position(env.db_file, "MSFT", 1, 100.0)
        # Different return periods (2 vs 3) so the shifted equal-weight
        # benchmark cannot degenerate to zero variance.
        aapl = _closes_from_returns(100.0, [0.01, -0.01] * 15)[:30]  # 30 closes
        msft = _closes_from_returns(80.0, [0.02, 0.01, -0.03] * 10)[:30]
        _insert_bars(env.db_file, "AAPL", aapl, offset=0)   # days 0..29
        _insert_bars(env.db_file, "MSFT", msft, offset=5)   # days 5..34

        var_95_pct, beta, bars = _metrics(env, ["AAPL", "MSFT"])
        assert bars == 25  # intersection: days 5..29
        assert var_95_pct is not None
        assert beta is not None

    def test_window_clamps_to_sixty_bars(self, risk_db):
        env = risk_db
        env.price_cache.update("AAPL", 100.0)
        _insert_position(env.db_file, "AAPL", 1, 100.0)
        closes = _closes_from_returns(100.0, [0.01, -0.005] * 35)  # 71 bars
        _insert_bars(env.db_file, "AAPL", closes)

        _, _, bars = _metrics(env, ["AAPL"])
        assert bars == RISK_WINDOW_BARS  # 60

    def test_bars_in_another_market_partition_do_not_count(self, risk_db):
        env = risk_db
        env.price_cache.update("AAPL", 100.0)
        _insert_position(env.db_file, "AAPL", 1, 100.0)
        _insert_bars(
            env.db_file, "AAPL",
            _closes_from_returns(100.0, SINGLE_RETURNS), market="cn",
        )

        assert _metrics(env, ["AAPL"], market="us") == (None, None, 0)

    def test_uncached_held_ticker_weights_at_avg_cost(self, risk_db):
        env = risk_db
        # ZZ is not in the cache — its weight comes from avg_cost. It is not
        # in the benchmark list either; AAPL alone forms the benchmark.
        _insert_position(env.db_file, "ZZ", 5, 100.0)
        _insert_bars(env.db_file, "ZZ", _closes_from_returns(100.0, SINGLE_RETURNS))
        _insert_bars(env.db_file, "AAPL", _closes_from_returns(50.0, RETURNS_A))

        var_95_pct, beta, bars = _metrics(env, ["AAPL"])
        assert bars == 21
        assert var_95_pct == 2.1  # single holding → r_p is ZZ's series
        assert beta is not None


class TestRiskMetricsNullPaths:
    """Every null path reports (None, None, 0)."""

    def test_no_positions(self, risk_db):
        _insert_bars(risk_db.db_file, "AAPL", _closes_from_returns(100.0, SINGLE_RETURNS))
        assert _metrics(risk_db, ["AAPL"]) == (None, None, 0)

    def test_below_min_common_bars(self, risk_db):
        env = risk_db
        env.price_cache.update("AAPL", 100.0)
        _insert_position(env.db_file, "AAPL", 1, 100.0)
        closes = _closes_from_returns(100.0, [0.01] * 18)  # 19 bars < 20
        _insert_bars(env.db_file, "AAPL", closes)
        assert _metrics(env, ["AAPL"]) == (None, None, 0)

    def test_held_ticker_without_bars(self, risk_db):
        env = risk_db
        env.price_cache.update("AAPL", 100.0)
        env.price_cache.update("BTC", 65000.0)
        _insert_position(env.db_file, "AAPL", 1, 100.0)
        _insert_position(env.db_file, "BTC", 0.01, 65000.0)
        _insert_bars(env.db_file, "AAPL", _closes_from_returns(100.0, SINGLE_RETURNS))
        # BTC has no daily bars → the common window is empty.
        assert _metrics(env, ["AAPL"]) == (None, None, 0)

    def test_zero_benchmark_variance(self, risk_db):
        env = risk_db
        # Held ticker varies, but the only benchmark constituent is flat.
        _insert_position(env.db_file, "ZZ", 5, 100.0)
        _insert_bars(env.db_file, "ZZ", _closes_from_returns(100.0, SINGLE_RETURNS))
        _insert_bars(env.db_file, "AAPL", [250.0] * 21)
        assert _metrics(env, ["AAPL"]) == (None, None, 0)

    def test_benchmark_without_full_window_coverage_drops_out(self, risk_db):
        env = risk_db
        _insert_position(env.db_file, "ZZ", 5, 100.0)
        _insert_bars(env.db_file, "ZZ", _closes_from_returns(100.0, SINGLE_RETURNS))
        # AAPL misses the window's first day (offset 1) → drops out → the
        # benchmark is empty → null.
        _insert_bars(
            env.db_file, "AAPL",
            _closes_from_returns(50.0, [0.01] * 19), offset=1,
        )
        assert _metrics(env, ["AAPL"]) == (None, None, 0)


@pytest_asyncio.fixture
async def risk_app(tmp_path, monkeypatch):
    """Two clients over ONE DB/cache: a profile-configured portfolio router
    (D2 — risk keys present) and the legacy profile-less construction
    (pre-D2 shape) for the byte-regression comparison."""
    db_file = str(tmp_path / "risk_app.db")
    monkeypatch.setenv("DB_PATH", db_file)
    init_db(db_file)
    price_cache = PriceCache()
    for ticker, price in SEED_PRICES.items():
        price_cache.update(ticker, price)

    profile_app = FastAPI()
    profile_app.include_router(
        create_portfolio_router(price_cache, db_file, profile=US_PROFILE)
    )
    legacy_app = FastAPI()
    legacy_app.include_router(create_portfolio_router(price_cache, db_file))

    async with AsyncClient(
        transport=ASGITransport(app=profile_app), base_url="http://test"
    ) as client, AsyncClient(
        transport=ASGITransport(app=legacy_app), base_url="http://test"
    ) as legacy_client:
        yield SimpleNamespace(
            db_file=db_file,
            price_cache=price_cache,
            client=client,
            legacy_client=legacy_client,
        )


class TestAnalyticsRiskEndpoint:
    """GET /api/portfolio/analytics — additive keys on the profile router."""

    async def test_empty_portfolio_reports_null_and_zero_bars(self, risk_app):
        data = (await risk_app.client.get("/api/portfolio/analytics")).json()
        assert set(data.keys()) == LEGACY_KEYS | RISK_KEYS
        assert data["var_95_pct"] is None
        assert data["beta"] is None
        assert data["risk_window_bars"] == 0

    async def test_golden_two_asset_values_through_endpoint(self, risk_app):
        env = risk_app
        env.price_cache.update("AAPL", 100.0)
        env.price_cache.update("MSFT", 100.0)
        _insert_position(env.db_file, "AAPL", 6, 100.0)
        _insert_position(env.db_file, "MSFT", 4, 100.0)
        _insert_bars(env.db_file, "AAPL", _closes_from_returns(100.0, RETURNS_A))
        _insert_bars(env.db_file, "MSFT", _closes_from_returns(50.0, RETURNS_B))
        _insert_bars(env.db_file, "NVDA", _closes_from_returns(200.0, RETURNS_N))

        data = (await env.client.get("/api/portfolio/analytics")).json()
        assert data["var_95_pct"] == 1.2
        assert data["beta"] == 1.53
        assert data["risk_window_bars"] == 21

    async def test_position_without_bars_stays_null_until_synced(self, risk_app):
        env = risk_app
        env.price_cache.update("AAPL", 100.0)
        resp = await env.client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "side": "buy", "quantity": 3},
        )
        assert resp.status_code == 200

        data = (await env.client.get("/api/portfolio/analytics")).json()
        assert data["var_95_pct"] is None
        assert data["beta"] is None
        assert data["risk_window_bars"] == 0  # the E2E null-phase contract

        _insert_bars(env.db_file, "AAPL", _closes_from_returns(100.0, SINGLE_RETURNS))
        data = (await env.client.get("/api/portfolio/analytics")).json()
        assert data["var_95_pct"] == 2.1
        assert data["beta"] == 1.0
        assert data["risk_window_bars"] == 21

    async def test_existing_keys_byte_regression_against_legacy_router(self, risk_app):
        """The profile router's response minus the three risk keys must equal
        the pre-D2 (profile-less) router's response exactly — keys AND values."""
        env = risk_app
        env.price_cache.update("AAPL", 100.0)
        for side, qty in (("buy", 10), ("sell", 4)):
            resp = await env.client.post(
                "/api/portfolio/trade",
                json={"ticker": "AAPL", "side": side, "quantity": qty},
            )
            assert resp.status_code == 200
        _insert_bars(env.db_file, "AAPL", _closes_from_returns(100.0, SINGLE_RETURNS))
        conn = get_conn(env.db_file)
        try:
            for i, value in enumerate([10000.0, 10500.0, 9800.0, 10200.0]):
                conn.execute(
                    "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at) "
                    "VALUES (?, 'default', ?, ?)",
                    (str(uuid.uuid4()), value, f"2026-07-06T10:{i:02d}:00+00:00"),
                )
            conn.commit()
        finally:
            conn.close()

        with_risk = (await env.client.get("/api/portfolio/analytics")).json()
        legacy = (await env.legacy_client.get("/api/portfolio/analytics")).json()

        assert set(legacy.keys()) == LEGACY_KEYS  # pre-D2 shape untouched
        assert set(with_risk.keys()) == LEGACY_KEYS | RISK_KEYS
        assert {k: v for k, v in with_risk.items() if k in LEGACY_KEYS} == legacy
        # And the risk keys are live on the profile router (position + bars).
        assert with_risk["var_95_pct"] is not None
        assert with_risk["beta"] == 1.0
        assert with_risk["risk_window_bars"] == 21
