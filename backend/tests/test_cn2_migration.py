"""positions.t1_locked migration (CN-2 §2).

Fresh databases get the column from schema.sql; pre-existing volumes gain it via
the idempotent ALTER-TABLE step in _migrate_schema (the orders-table precedent).
"""

from __future__ import annotations

from app.db.connection import get_conn, init_db


def _columns(db_file, table):
    conn = get_conn(db_file)
    try:
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


class TestT1LockedColumn:
    def test_fresh_db_has_column(self, tmp_path):
        db = str(tmp_path / "fresh.db")
        init_db(db)
        assert "t1_locked" in _columns(db, "positions")

    def test_column_defaults_to_zero(self, tmp_path):
        db = str(tmp_path / "default.db")
        init_db(db)
        conn = get_conn(db)
        try:
            conn.execute(
                "INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, "
                "updated_at) VALUES ('p1', 'default', 'AAPL', 5, 100.0, '2026-01-01')"
            )
            conn.commit()
            row = conn.execute(
                "SELECT t1_locked FROM positions WHERE id='p1'"
            ).fetchone()
            assert row["t1_locked"] == 0
        finally:
            conn.close()

    def test_migration_adds_column_to_legacy_positions_table(self, tmp_path):
        """Simulate a pre-CN-2 volume: positions WITHOUT t1_locked, then init_db."""
        db = str(tmp_path / "legacy.db")
        conn = get_conn(db)
        try:
            # Old-shape positions table (no t1_locked), plus a legacy row.
            conn.execute(
                "CREATE TABLE positions (id TEXT PRIMARY KEY, user_id TEXT, "
                "ticker TEXT, quantity REAL, avg_cost REAL, updated_at TEXT, "
                "UNIQUE(user_id, ticker))"
            )
            conn.execute(
                "INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, "
                "updated_at) VALUES ('old', 'default', 'MSFT', 3, 200.0, '2025-01-01')"
            )
            conn.commit()
        finally:
            conn.close()

        assert "t1_locked" not in _columns(db, "positions")
        init_db(db)  # runs _migrate_schema
        assert "t1_locked" in _columns(db, "positions")

        # The legacy row survives and defaults to an unlocked 0.
        conn = get_conn(db)
        try:
            row = conn.execute(
                "SELECT quantity, t1_locked FROM positions WHERE id='old'"
            ).fetchone()
            assert row["quantity"] == 3
            assert row["t1_locked"] == 0
        finally:
            conn.close()

    def test_migration_is_idempotent(self, tmp_path):
        db = str(tmp_path / "idem.db")
        init_db(db)
        init_db(db)  # second run must not raise or duplicate the column
        cols = [
            r["name"] for r in _iter_pragma(db)
        ]
        assert cols.count("t1_locked") == 1


def _iter_pragma(db_file):
    conn = get_conn(db_file)
    try:
        return list(conn.execute("PRAGMA table_info(positions)"))
    finally:
        conn.close()
