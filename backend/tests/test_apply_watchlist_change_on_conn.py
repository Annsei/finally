"""TDD tests for apply_watchlist_change_on_conn helper function.

Tests the module-level helper directly (not via HTTP) to verify:
- All validation failure paths return dicts with status="failed" (no raise)
- add action returns {"status": "added", ...} and is idempotent
- remove action returns {"status": "removed", ...} and is idempotent
"""

from __future__ import annotations

import pytest


@pytest.fixture
def fresh_db(tmp_path):
    """Provide a fresh initialized SQLite connection for direct helper testing."""
    db_file = str(tmp_path / "test_watchlist_helper.db")
    from app.db.connection import init_db, get_conn
    init_db(db_file)
    conn = get_conn(db_file)
    yield conn
    conn.close()


class TestApplyWatchlistChangeOnConnImport:
    """Verify the helper is importable as a module-level function."""

    def test_importable(self):
        from app.routes.watchlist import apply_watchlist_change_on_conn
        assert callable(apply_watchlist_change_on_conn)

    def test_signature(self):
        import inspect
        from app.routes.watchlist import apply_watchlist_change_on_conn
        sig = inspect.signature(apply_watchlist_change_on_conn)
        params = list(sig.parameters.keys())
        assert params == ["conn", "ticker", "action"]


class TestApplyWatchlistChangeOnConnFailurePaths:
    """All validation failures return dicts with status='failed'."""

    def test_empty_ticker_returns_failed(self, fresh_db):
        from app.routes.watchlist import apply_watchlist_change_on_conn
        result = apply_watchlist_change_on_conn(fresh_db, "", "add")
        assert isinstance(result, dict)
        assert result["status"] == "failed"
        assert "empty" in result["error"].lower() or "Ticker" in result["error"]

    def test_whitespace_only_ticker_returns_failed(self, fresh_db):
        from app.routes.watchlist import apply_watchlist_change_on_conn
        result = apply_watchlist_change_on_conn(fresh_db, "   ", "add")
        assert isinstance(result, dict)
        assert result["status"] == "failed"

    def test_invalid_action_returns_failed(self, fresh_db):
        from app.routes.watchlist import apply_watchlist_change_on_conn
        result = apply_watchlist_change_on_conn(fresh_db, "AAPL", "buy")
        assert isinstance(result, dict)
        assert result["status"] == "failed"
        assert "add" in result["error"].lower() or "remove" in result["error"].lower()
        assert result["ticker"] == "AAPL"

    def test_failed_result_has_ticker_key(self, fresh_db):
        from app.routes.watchlist import apply_watchlist_change_on_conn
        result = apply_watchlist_change_on_conn(fresh_db, "AAPL", "invalid")
        assert "ticker" in result
        assert result["ticker"] == "AAPL"


class TestApplyWatchlistChangeOnConnAddAction:
    """add action inserts ticker and returns status='added'."""

    def test_add_returns_added_status(self, fresh_db):
        from app.routes.watchlist import apply_watchlist_change_on_conn
        result = apply_watchlist_change_on_conn(fresh_db, "PYPL", "add")
        assert isinstance(result, dict)
        assert result["status"] == "added"
        assert result["ticker"] == "PYPL"
        assert result["action"] == "add"

    def test_add_normalizes_ticker_to_uppercase(self, fresh_db):
        from app.routes.watchlist import apply_watchlist_change_on_conn
        result = apply_watchlist_change_on_conn(fresh_db, "pypl", "add")
        assert result["status"] == "added"
        assert result["ticker"] == "PYPL"

    def test_add_inserts_into_db(self, fresh_db):
        from app.routes.watchlist import apply_watchlist_change_on_conn
        apply_watchlist_change_on_conn(fresh_db, "PYPL", "add")
        row = fresh_db.execute(
            "SELECT ticker FROM watchlist WHERE user_id = 'default' AND ticker = 'PYPL'"
        ).fetchone()
        assert row is not None

    def test_add_idempotent_does_not_duplicate(self, fresh_db):
        from app.routes.watchlist import apply_watchlist_change_on_conn
        apply_watchlist_change_on_conn(fresh_db, "PYPL", "add")
        apply_watchlist_change_on_conn(fresh_db, "PYPL", "add")
        rows = fresh_db.execute(
            "SELECT COUNT(*) as cnt FROM watchlist WHERE user_id = 'default' AND ticker = 'PYPL'"
        ).fetchone()
        assert rows["cnt"] == 1

    def test_add_action_case_insensitive(self, fresh_db):
        from app.routes.watchlist import apply_watchlist_change_on_conn
        result = apply_watchlist_change_on_conn(fresh_db, "PYPL", "ADD")
        assert result["status"] == "added"


class TestApplyWatchlistChangeOnConnRemoveAction:
    """remove action deletes ticker and returns status='removed'."""

    def test_remove_returns_removed_status(self, fresh_db):
        from app.routes.watchlist import apply_watchlist_change_on_conn
        # Seed data has AAPL in watchlist from init_db
        result = apply_watchlist_change_on_conn(fresh_db, "AAPL", "remove")
        assert isinstance(result, dict)
        assert result["status"] == "removed"
        assert result["ticker"] == "AAPL"
        assert result["action"] == "remove"

    def test_remove_deletes_from_db(self, fresh_db):
        from app.routes.watchlist import apply_watchlist_change_on_conn
        # First add PYPL
        apply_watchlist_change_on_conn(fresh_db, "PYPL", "add")
        # Then remove it
        apply_watchlist_change_on_conn(fresh_db, "PYPL", "remove")
        row = fresh_db.execute(
            "SELECT ticker FROM watchlist WHERE user_id = 'default' AND ticker = 'PYPL'"
        ).fetchone()
        assert row is None

    def test_remove_nonexistent_idempotent(self, fresh_db):
        from app.routes.watchlist import apply_watchlist_change_on_conn
        result = apply_watchlist_change_on_conn(fresh_db, "NOTEXIST", "remove")
        assert result["status"] == "removed"
        assert result["ticker"] == "NOTEXIST"

    def test_remove_action_case_insensitive(self, fresh_db):
        from app.routes.watchlist import apply_watchlist_change_on_conn
        result = apply_watchlist_change_on_conn(fresh_db, "AAPL", "REMOVE")
        assert result["status"] == "removed"
