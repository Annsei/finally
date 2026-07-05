"""TDD tests for execute_trade_on_conn helper function.

Tests the module-level helper directly (not via HTTP) to verify:
- All validation failure paths return dicts with status="failed" (no raise)
- Successful trade returns status="executed" dict with all required keys
- The existing HTTP route remains a thin wrapper (regression via existing tests)
"""

from __future__ import annotations

import sqlite3

import pytest

from app.market import PriceCache
from app.market.seed_prices import SEED_PRICES


@pytest.fixture
def fresh_db(tmp_path):
    """Provide a fresh initialized SQLite connection for direct helper testing."""
    db_file = str(tmp_path / "test_helper.db")
    from app.db.connection import init_db, get_conn
    init_db(db_file)
    conn = get_conn(db_file)
    yield conn
    conn.close()


@pytest.fixture
def seeded_cache():
    """Price cache populated with seed prices."""
    cache = PriceCache()
    for ticker, price in SEED_PRICES.items():
        cache.update(ticker, price)
    return cache


@pytest.fixture
def empty_cache():
    """Empty price cache with no prices loaded."""
    return PriceCache()


class TestExecuteTradeOnConnImport:
    """Verify the helper is importable as a module-level function."""

    def test_importable(self):
        from app.routes.portfolio import execute_trade_on_conn
        assert callable(execute_trade_on_conn)

    def test_signature(self):
        import inspect
        from app.routes.portfolio import execute_trade_on_conn
        sig = inspect.signature(execute_trade_on_conn)
        params = list(sig.parameters.keys())
        assert params == ["conn", "price_cache", "ticker", "side", "quantity"]


class TestExecuteTradeOnConnFailurePaths:
    """All validation failures return dicts with status='failed' — never raise."""

    def test_ticker_not_in_cache_returns_failed_dict(self, fresh_db, empty_cache):
        from app.routes.portfolio import execute_trade_on_conn
        result = execute_trade_on_conn(fresh_db, empty_cache, "AAPL", "buy", 1.0)
        assert isinstance(result, dict)
        assert result["status"] == "failed"
        assert "Ticker not found in price cache" in result["error"]
        assert result["ticker"] == "AAPL"

    def test_invalid_side_returns_failed_dict(self, fresh_db, seeded_cache):
        from app.routes.portfolio import execute_trade_on_conn
        result = execute_trade_on_conn(fresh_db, seeded_cache, "AAPL", "hold", 1.0)
        assert isinstance(result, dict)
        assert result["status"] == "failed"
        assert "buy" in result["error"].lower() or "sell" in result["error"].lower()
        assert result["ticker"] == "AAPL"

    def test_zero_quantity_returns_failed_dict(self, fresh_db, seeded_cache):
        from app.routes.portfolio import execute_trade_on_conn
        result = execute_trade_on_conn(fresh_db, seeded_cache, "AAPL", "buy", 0.0)
        assert isinstance(result, dict)
        assert result["status"] == "failed"
        assert "greater than 0" in result["error"].lower() or "quantity" in result["error"].lower()
        assert result["ticker"] == "AAPL"

    def test_negative_quantity_returns_failed_dict(self, fresh_db, seeded_cache):
        from app.routes.portfolio import execute_trade_on_conn
        result = execute_trade_on_conn(fresh_db, seeded_cache, "AAPL", "buy", -5.0)
        assert isinstance(result, dict)
        assert result["status"] == "failed"
        assert result["ticker"] == "AAPL"

    def test_insufficient_cash_returns_failed_dict(self, fresh_db, seeded_cache):
        from app.routes.portfolio import execute_trade_on_conn
        # AAPL seed price ~$190; buying 1,000,000 exceeds $10k balance
        result = execute_trade_on_conn(fresh_db, seeded_cache, "AAPL", "buy", 1_000_000.0)
        assert isinstance(result, dict)
        assert result["status"] == "failed"
        assert result["error"] == "Insufficient cash"
        assert result["ticker"] == "AAPL"

    def test_insufficient_shares_returns_failed_dict(self, fresh_db, seeded_cache):
        from app.routes.portfolio import execute_trade_on_conn
        # No shares held — selling any amount should fail
        result = execute_trade_on_conn(fresh_db, seeded_cache, "AAPL", "sell", 1.0)
        assert isinstance(result, dict)
        assert result["status"] == "failed"
        assert result["error"] == "Insufficient shares to sell"
        assert result["ticker"] == "AAPL"


class TestExecuteTradeOnConnSuccessPaths:
    """Successful trades return status='executed' dict with all required keys."""

    def test_buy_returns_executed_dict(self, fresh_db, seeded_cache):
        from app.routes.portfolio import execute_trade_on_conn
        result = execute_trade_on_conn(fresh_db, seeded_cache, "AAPL", "buy", 1.0)
        assert isinstance(result, dict)
        assert result["status"] == "executed"
        assert result["ticker"] == "AAPL"
        assert result["side"] == "buy"
        assert result["quantity"] == 1.0
        assert "price" in result
        assert "trade_id" in result

    def test_sell_after_buy_returns_executed_dict(self, fresh_db, seeded_cache):
        from app.routes.portfolio import execute_trade_on_conn
        # First buy
        buy_result = execute_trade_on_conn(fresh_db, seeded_cache, "AAPL", "buy", 2.0)
        assert buy_result["status"] == "executed"
        # Then sell 1
        sell_result = execute_trade_on_conn(fresh_db, seeded_cache, "AAPL", "sell", 1.0)
        assert isinstance(sell_result, dict)
        assert sell_result["status"] == "executed"
        assert sell_result["ticker"] == "AAPL"
        assert sell_result["side"] == "sell"
        assert sell_result["quantity"] == 1.0

    def test_ticker_normalized_to_uppercase(self, fresh_db, seeded_cache):
        from app.routes.portfolio import execute_trade_on_conn
        result = execute_trade_on_conn(fresh_db, seeded_cache, "aapl", "buy", 1.0)
        assert result["status"] == "executed"
        assert result["ticker"] == "AAPL"

    def test_side_normalized_to_lowercase(self, fresh_db, seeded_cache):
        from app.routes.portfolio import execute_trade_on_conn
        result = execute_trade_on_conn(fresh_db, seeded_cache, "AAPL", "BUY", 1.0)
        assert result["status"] == "executed"
        assert result["side"] == "buy"

    def test_buy_creates_position_in_db(self, fresh_db, seeded_cache):
        from app.routes.portfolio import execute_trade_on_conn
        result = execute_trade_on_conn(fresh_db, seeded_cache, "AAPL", "buy", 3.0)
        assert result["status"] == "executed"
        row = fresh_db.execute(
            "SELECT quantity FROM positions WHERE user_id = 'default' AND ticker = 'AAPL'"
        ).fetchone()
        assert row is not None
        assert row["quantity"] == 3.0

    def test_buy_deducts_cash(self, fresh_db, seeded_cache):
        from app.routes.portfolio import execute_trade_on_conn
        before = fresh_db.execute(
            "SELECT cash_balance FROM users_profile WHERE id = 'default'"
        ).fetchone()["cash_balance"]
        result = execute_trade_on_conn(fresh_db, seeded_cache, "AAPL", "buy", 1.0)
        assert result["status"] == "executed"
        after = fresh_db.execute(
            "SELECT cash_balance FROM users_profile WHERE id = 'default'"
        ).fetchone()["cash_balance"]
        assert after < before


class TestSellAtALossAccounting:
    """Spec §12 edge case: selling at a loss must realize the loss exactly."""

    def test_sell_all_shares_at_a_loss(self, fresh_db):
        """Buy 10 @ $200, price drops to $150, sell all 10.

        Cash must equal initial_cash - buy_cost + sell_proceeds exactly, the
        position row must be deleted, and both trade rows must record the
        price at their respective execution times.
        """
        from app.routes.portfolio import execute_trade_on_conn

        cache = PriceCache()
        cache.update("AAPL", 200.0)

        initial_cash: float = fresh_db.execute(
            "SELECT cash_balance FROM users_profile WHERE id = 'default'"
        ).fetchone()["cash_balance"]

        buy = execute_trade_on_conn(fresh_db, cache, "AAPL", "buy", 10.0)
        assert buy["status"] == "executed"
        assert buy["price"] == 200.0

        # Price drops 25% — the position is now underwater
        cache.update("AAPL", 150.0)

        sell = execute_trade_on_conn(fresh_db, cache, "AAPL", "sell", 10.0)
        assert sell["status"] == "executed"
        assert sell["price"] == 150.0
        fresh_db.commit()

        # Cash reflects the realized loss exactly: -$2000 buy, +$1500 sell
        cash: float = fresh_db.execute(
            "SELECT cash_balance FROM users_profile WHERE id = 'default'"
        ).fetchone()["cash_balance"]
        assert cash == pytest.approx(initial_cash - 10.0 * 200.0 + 10.0 * 150.0)
        assert cash == pytest.approx(initial_cash - 500.0)  # $500 realized loss

        # Position fully closed — row deleted
        pos_row = fresh_db.execute(
            "SELECT quantity FROM positions WHERE user_id = 'default' AND ticker = 'AAPL'"
        ).fetchone()
        assert pos_row is None

        # Both trades logged with the correct execution prices
        trade_rows = fresh_db.execute(
            "SELECT side, quantity, price FROM trades "
            "WHERE user_id = 'default' AND ticker = 'AAPL'"
        ).fetchall()
        assert len(trade_rows) == 2
        by_side = {row["side"]: row for row in trade_rows}
        assert by_side["buy"]["quantity"] == 10.0
        assert by_side["buy"]["price"] == 200.0
        assert by_side["sell"]["quantity"] == 10.0
        assert by_side["sell"]["price"] == 150.0
