---
plan: 01A
title: Database Foundation
wave: 1
depends_on: []
phase: 1
requirements_addressed: [BACK-02]
files_modified:
  - backend/app/db/__init__.py
  - backend/app/db/connection.py
  - backend/app/db/schema.sql
  - backend/app/db/seed.py
autonomous: true
---

# Plan 01A: Database Foundation

## Objective

Create the `backend/app/db/` package with SQLite connection utilities, schema SQL, and seed data. This is the persistence foundation all API routes depend on.

## Tasks

<task id="01A-1">
<title>Create backend/app/db/ package with connection utilities</title>

<read_first>
- backend/app/market/cache.py (pattern: module docstring, logger setup, __all__ exports)
- backend/app/market/__init__.py (pattern: public API via __init__.py with __all__)
- planning/PLAN.md §7 (schema spec — all 6 tables, column names and types)
- .planning/phases/01-backend-app-layer/01-CONTEXT.md (DB access decisions)
</read_first>

<action>
Create `backend/app/db/__init__.py` that exports: `init_db`, `get_conn`, `DB_PATH`.

Create `backend/app/db/connection.py` with:
- `DB_PATH: str = os.getenv("DB_PATH", "db/finally.db")` module-level constant
- `def get_conn(db_path: str = DB_PATH) -> sqlite3.Connection` — opens a connection with `check_same_thread=False`, sets `row_factory = sqlite3.Row`, executes `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON`; returns the connection (caller closes it)
- `def init_db(db_path: str = DB_PATH) -> None` — calls `get_conn`, reads `schema.sql` (relative to this file's directory), executes the SQL with `executescript`, runs seed if tables are empty, closes connection; idempotent (CREATE TABLE IF NOT EXISTS)

Follow conventions: `from __future__ import annotations`, full type annotations, `logger = logging.getLogger(__name__)`, private helper `_needs_seed(conn) -> bool` checks `SELECT COUNT(*) FROM users_profile`.
</action>

<acceptance_criteria>
- `backend/app/db/connection.py` exists and contains `def get_conn(` and `def init_db(`
- `backend/app/db/__init__.py` exports `init_db`, `get_conn`, `DB_PATH`
- `get_conn()` sets `conn.row_factory = sqlite3.Row`
- `PRAGMA journal_mode=WAL` is executed in `get_conn`
- `DB_PATH` reads from `os.getenv("DB_PATH", "db/finally.db")`
</acceptance_criteria>
</task>

<task id="01A-2">
<title>Write schema.sql with all 6 tables</title>

<read_first>
- planning/PLAN.md §7 (authoritative schema specification — 6 tables, all columns)
- .planning/phases/01-backend-app-layer/01-CONTEXT.md
</read_first>

<action>
Create `backend/app/db/schema.sql` with `CREATE TABLE IF NOT EXISTS` for all 6 tables exactly as specified in planning/PLAN.md §7:

1. `users_profile` — id TEXT PK, cash_balance REAL DEFAULT 10000.0, created_at TEXT
2. `watchlist` — id TEXT PK, user_id TEXT DEFAULT 'default', ticker TEXT, added_at TEXT; UNIQUE(user_id, ticker)
3. `positions` — id TEXT PK, user_id TEXT DEFAULT 'default', ticker TEXT, quantity REAL, avg_cost REAL, updated_at TEXT; UNIQUE(user_id, ticker)
4. `trades` — id TEXT PK, user_id TEXT DEFAULT 'default', ticker TEXT, side TEXT, quantity REAL, price REAL, executed_at TEXT
5. `portfolio_snapshots` — id TEXT PK, user_id TEXT DEFAULT 'default', total_value REAL, recorded_at TEXT
6. `chat_messages` — id TEXT PK, user_id TEXT DEFAULT 'default', role TEXT, content TEXT, actions TEXT, created_at TEXT
</action>

<acceptance_criteria>
- `backend/app/db/schema.sql` contains exactly 6 `CREATE TABLE IF NOT EXISTS` statements
- `users_profile` has `cash_balance REAL`
- `watchlist` has `UNIQUE(user_id, ticker)`
- `positions` has `UNIQUE(user_id, ticker)` and `avg_cost REAL`
- `trades` has `side TEXT` and `quantity REAL`
- `portfolio_snapshots` has `total_value REAL` and `recorded_at TEXT`
- `chat_messages` has `actions TEXT` column
</acceptance_criteria>
</task>

<task id="01A-3">
<title>Write seed.py with default user and watchlist</title>

<read_first>
- backend/app/market/seed_prices.py (SEED_PRICES dict — the 10 default tickers)
- planning/PLAN.md §7 (seed data spec: user id="default", cash=10000, 10 watchlist entries)
- backend/app/db/connection.py (get_conn pattern)
</read_first>

<action>
Create `backend/app/db/seed.py` with:
- `def seed_db(conn: sqlite3.Connection) -> None` — inserts default data if tables are empty
- Insert `users_profile`: `id="default"`, `cash_balance=10000.0`, `created_at=datetime.utcnow().isoformat()`
- Insert 10 `watchlist` rows: one per ticker from `SEED_PRICES.keys()` (import from `app.market.seed_prices`), `user_id="default"`, `id=str(uuid.uuid4())`, `added_at=datetime.utcnow().isoformat()`
- Use `INSERT OR IGNORE INTO` for idempotency
- Logger call: `logger.info("Seeded default user and %d watchlist tickers", len(tickers))`
</action>

<acceptance_criteria>
- `backend/app/db/seed.py` exists and contains `def seed_db(`
- Uses `INSERT OR IGNORE INTO users_profile`
- Uses `INSERT OR IGNORE INTO watchlist`
- Imports `SEED_PRICES` from `app.market.seed_prices`
- Uses `uuid.uuid4()` for id generation
</acceptance_criteria>
</task>

## Verification

- `python -c "from app.db import init_db; init_db('test.db')"` exits 0
- `test.db` contains 6 tables after `init_db`
- `users_profile` row with `id='default'` and `cash_balance=10000.0` exists after seed
- 10 rows in `watchlist` for `user_id='default'` after seed

## Must Haves

- [ ] All 6 tables created with correct schema (no missing columns)
- [ ] `init_db` is idempotent — calling twice does not raise errors or duplicate seed data
- [ ] `get_conn` returns connection with `row_factory = sqlite3.Row` and WAL mode active
