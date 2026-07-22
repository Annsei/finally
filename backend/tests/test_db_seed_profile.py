"""init_db seed_cash / default_watchlist injection (CN-1).

Fresh databases seed with the injected profile values; already-seeded
databases are never re-seeded — the pre-CN-1 semantics.
"""

from __future__ import annotations

from app.db.connection import CURRENT_SCHEMA_VERSION, get_conn, init_db
from app.market.seed_prices import DEFAULT_WATCHLIST
from app.market.seed_prices_cn import CN_DEFAULT_WATCHLIST


def _seeded_state(db_file: str) -> tuple[float, set[str]]:
    """(default user's cash, watchlist ticker set) for a database file."""
    conn = get_conn(db_file)
    try:
        cash = conn.execute(
            "SELECT cash_balance FROM users_profile WHERE id = 'default'"
        ).fetchone()[0]
        tickers = {
            row["ticker"] for row in conn.execute("SELECT ticker FROM watchlist")
        }
    finally:
        conn.close()
    return cash, tickers


class TestInitDbProfileSeeding:
    """init_db(seed_cash=..., default_watchlist=...) drives fresh seeds."""

    def test_default_call_seeds_us_values(self, tmp_path):
        db_file = str(tmp_path / "us.db")
        init_db(db_file)
        cash, tickers = _seeded_state(db_file)
        assert cash == 10_000.0
        assert tickers == set(DEFAULT_WATCHLIST)

    def test_cn_seed_cash_and_watchlist(self, tmp_path):
        db_file = str(tmp_path / "cn.db")
        init_db(db_file, seed_cash=100_000.0, default_watchlist=list(CN_DEFAULT_WATCHLIST))
        cash, tickers = _seeded_state(db_file)
        assert cash == 100_000.0
        assert tickers == set(CN_DEFAULT_WATCHLIST)
        assert len(tickers) == 14

    def test_existing_db_is_never_reseeded(self, tmp_path):
        """Re-running init_db with different values must not touch the data."""
        db_file = str(tmp_path / "sticky.db")
        init_db(db_file)
        init_db(db_file, seed_cash=100_000.0, default_watchlist=list(CN_DEFAULT_WATCHLIST))
        cash, tickers = _seeded_state(db_file)
        assert cash == 10_000.0
        assert tickers == set(DEFAULT_WATCHLIST)

    def test_none_watchlist_uses_us_default(self, tmp_path):
        db_file = str(tmp_path / "mixed.db")
        init_db(db_file, seed_cash=100_000.0, default_watchlist=None)
        cash, tickers = _seeded_state(db_file)
        assert cash == 100_000.0
        assert tickers == set(DEFAULT_WATCHLIST)

    def test_cn_seed_is_idempotent(self, tmp_path):
        """Calling init_db twice with CN values duplicates nothing."""
        db_file = str(tmp_path / "cn2.db")
        for _ in range(2):
            init_db(
                db_file, seed_cash=100_000.0, default_watchlist=list(CN_DEFAULT_WATCHLIST)
            )
        conn = get_conn(db_file)
        try:
            count = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
            users = conn.execute("SELECT COUNT(*) FROM users_profile").fetchone()[0]
        finally:
            conn.close()
        assert count == 14
        assert users == 1

    def test_schema_version_is_recorded_once_across_startups(self, tmp_path):
        db_file = str(tmp_path / "versioned.db")
        init_db(db_file)
        init_db(db_file)
        conn = get_conn(db_file)
        try:
            rows = conn.execute(
                "SELECT version, name, applied_at FROM schema_migrations"
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 1
        assert rows[0]["version"] == CURRENT_SCHEMA_VERSION
        assert rows[0]["name"]
        assert rows[0]["applied_at"]
