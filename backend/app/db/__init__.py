"""Database package for FinAlly.

Public API:
    init_db  - Initialize schema and seed default data (idempotent)
    get_conn - Open a configured SQLite connection (caller closes it)
    DB_PATH  - Default database path (from DB_PATH env var or "db/finally.db")
"""

from .connection import DB_PATH, get_conn, init_db

__all__ = [
    "init_db",
    "get_conn",
    "DB_PATH",
]
