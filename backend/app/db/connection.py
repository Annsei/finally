"""SQLite connection utilities for FinAlly.

Provides per-request connection management with WAL mode and dict-like row access.
Database initialization is idempotent — safe to call on every startup.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH: str = os.getenv("DB_PATH", "db/finally.db")

_SCHEMA_FILE = Path(__file__).parent / "schema.sql"


def get_conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection configured for FinAlly use.

    Settings applied:
    - ``row_factory = sqlite3.Row`` for dict-like row access
    - ``PRAGMA journal_mode=WAL`` for concurrent read throughput
    - ``PRAGMA foreign_keys=ON`` for referential integrity

    The caller is responsible for closing the connection.

    Args:
        db_path: Path to the SQLite database file. Defaults to DB_PATH.

    Returns:
        An open :class:`sqlite3.Connection`.
    """
    # Ensure the parent directory exists (important for the default db/ path)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _needs_seed(conn: sqlite3.Connection) -> bool:
    """Return True if users_profile is empty (database has not been seeded yet)."""
    row = conn.execute("SELECT COUNT(*) FROM users_profile").fetchone()
    return row[0] == 0


def init_db(db_path: str = DB_PATH) -> None:
    """Initialize the database: create schema and seed default data if empty.

    Idempotent — uses ``CREATE TABLE IF NOT EXISTS`` so calling this multiple
    times does not raise errors or duplicate data.

    Args:
        db_path: Path to the SQLite database file. Defaults to DB_PATH.
    """
    logger.info("Initializing database at %s", db_path)
    conn = get_conn(db_path)
    try:
        schema_sql = _SCHEMA_FILE.read_text(encoding="utf-8")
        conn.executescript(schema_sql)
        logger.debug("Schema applied successfully")

        if _needs_seed(conn):
            logger.info("Database is empty — running seed")
            from app.db.seed import seed_db  # local import to avoid circular at module level

            seed_db(conn)
        else:
            logger.debug("Database already seeded — skipping")
    finally:
        conn.close()
