---
plan: 01E
title: Backend Route Tests
wave: 3
depends_on: [01B, 01C, 01D]
phase: 1
requirements_addressed: [BACK-01, BACK-02, BACK-03, BACK-04, BACK-05, BACK-06, BACK-07, BACK-08, BACK-09, BACK-10, BACK-11]
files_modified:
  - backend/tests/test_health.py
  - backend/tests/test_portfolio.py
  - backend/tests/test_watchlist.py
  - backend/tests/conftest.py
autonomous: true
---

# Plan 01E: Backend Route Tests

## Objective

Write integration tests for all Phase 1 API endpoints using the existing `httpx.ASGITransport` pattern from `test_stream.py`. Tests use a temporary SQLite DB (tmp_path fixture). All tests pass with `uv run --extra dev pytest -v`.

## Tasks

<task id="01E-1">
<title>Update tests/conftest.py with app fixture using temp SQLite DB</title>

<read_first>
- backend/tests/conftest.py (existing fixtures)
- backend/tests/market/test_stream.py (httpx.ASGITransport + AsyncClient pattern)
- backend/app/main.py (app object, how it uses DB_PATH env var)
- backend/app/db/connection.py (DB_PATH constant, init_db)
</read_first>

<action>
Add to `backend/tests/conftest.py` (or create if it only has market fixtures):

```python
import os
import tempfile
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

@pytest_asyncio.fixture
async def app_client(tmp_path):
    """FastAPI test client with isolated temp SQLite DB."""
    db_file = str(tmp_path / "test.db")
    os.environ["DB_PATH"] = db_file
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    del os.environ["DB_PATH"]
```

Note: The app's lifespan will run during the `async with AsyncClient` context, initializing the temp DB and starting/stopping the market source.

Alternative if lifespan causes issues with the test client: use `app_client` with `lifespan="off"` parameter to `ASGITransport` and manually call `init_db(db_file)` in the fixture. Choose whichever approach works cleanly with the existing test infrastructure.
</action>

<acceptance_criteria>
- `backend/tests/conftest.py` contains `async def app_client(tmp_path)` fixture
- Fixture sets `os.environ["DB_PATH"]` to a temp path
- Fixture yields an `httpx.AsyncClient`
- `uv run --extra dev pytest tests/test_health.py -v` exits 0
</acceptance_criteria>
</task>

<task id="01E-2">
<title>Write tests/test_health.py</title>

<read_first>
- backend/tests/market/test_stream.py (test structure and AsyncClient usage)
- backend/app/routes/health.py (endpoint being tested)
</read_first>

<action>
Create `backend/tests/test_health.py`:

```python
import pytest

class TestHealthEndpoint:
    async def test_health_returns_ok(self, app_client):
        response = await app_client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    async def test_health_content_type_json(self, app_client):
        response = await app_client.get("/api/health")
        assert "application/json" in response.headers["content-type"]
```
</action>

<acceptance_criteria>
- `backend/tests/test_health.py` exists with `TestHealthEndpoint` class
- `uv run --extra dev pytest tests/test_health.py -v` exits 0 with 2 tests passing
</acceptance_criteria>
</task>

<task id="01E-3">
<title>Write tests/test_portfolio.py</title>

<read_first>
- backend/app/routes/portfolio.py (endpoints being tested)
- backend/tests/market/test_stream.py (async test pattern)
- planning/PLAN.md §7 (expected portfolio state after seed)
</read_first>

<action>
Create `backend/tests/test_portfolio.py` with `TestPortfolioEndpoints`:

Tests:
1. `test_get_portfolio_fresh_db` — GET /api/portfolio returns `cash=10000.0`, `positions=[]`, `total_value=10000.0`
2. `test_trade_buy_reduces_cash` — POST trade buy AAPL 1 share; GET portfolio shows reduced cash and AAPL in positions
3. `test_trade_buy_insufficient_cash` — POST trade buy with quantity * price > cash → 400 `{"error": "Insufficient cash"}`
4. `test_trade_sell_without_position` — POST trade sell AAPL when no AAPL owned → 400
5. `test_trade_buy_then_sell` — Buy 2 shares, sell 1; verify position quantity is 1
6. `test_portfolio_history_empty` — GET /api/portfolio/history returns `{"snapshots": []}` on fresh DB
7. `test_portfolio_history_after_trade` — After a buy, history has at least 1 snapshot

Note: Since price cache may not have prices in test, inject a mock price. Either mock `price_cache.get_price` or add a test ticker to the cache in a fixture. Use `monkeypatch` or directly set price cache values.
</action>

<acceptance_criteria>
- `backend/tests/test_portfolio.py` exists with `TestPortfolioEndpoints`
- `test_get_portfolio_fresh_db` passes (cash=10000.0)
- `test_trade_buy_insufficient_cash` passes (400 error)
- `uv run --extra dev pytest tests/test_portfolio.py -v` exits 0
</acceptance_criteria>
</task>

<task id="01E-4">
<title>Write tests/test_watchlist.py</title>

<read_first>
- backend/app/routes/watchlist.py (endpoints being tested)
- backend/tests/conftest.py (app_client fixture)
</read_first>

<action>
Create `backend/tests/test_watchlist.py` with `TestWatchlistEndpoints`:

Tests:
1. `test_get_watchlist_returns_10_default_tickers` — GET /api/watchlist returns list with 10 tickers (from seed)
2. `test_add_ticker` — POST `{"ticker": "PYPL"}` → 200, then GET shows PYPL in list
3. `test_add_ticker_uppercase_normalization` — POST `{"ticker": "pypl"}` → stored as "PYPL"
4. `test_add_existing_ticker_idempotent` — POST AAPL twice → 200 both times, no duplicate in GET
5. `test_remove_ticker` — Add PYPL, then DELETE /api/watchlist/PYPL → 200, GET no longer shows PYPL
6. `test_remove_nonexistent_ticker` — DELETE /api/watchlist/NOTEXIST → 200 (idempotent)
7. `test_watchlist_has_price_fields` — GET response includes `price`, `change_percent`, `direction` keys (may be null)
</action>

<acceptance_criteria>
- `backend/tests/test_watchlist.py` exists with `TestWatchlistEndpoints`
- `test_get_watchlist_returns_10_default_tickers` passes
- `test_add_ticker` passes
- `test_remove_nonexistent_ticker` passes (idempotent)
- `uv run --extra dev pytest tests/test_watchlist.py -v` exits 0
</acceptance_criteria>
</task>

<task id="01E-5">
<title>Run full test suite and fix any failures</title>

<read_first>
- All test files created above
- backend/tests/market/ (existing tests — must not regress)
</read_first>

<action>
Run the full test suite:
```bash
cd backend && uv run --extra dev pytest -v
```

Fix any failures. The existing 75 market tests must still pass. Common issues to address:
- Import order problems (add `from __future__ import annotations`)
- DB_PATH env variable isolation between tests
- Price cache mock for trade tests (use `unittest.mock.patch` or pytest's `monkeypatch`)
- Async fixture scoping issues (use `@pytest_asyncio.fixture`)
</action>

<acceptance_criteria>
- `cd backend && uv run --extra dev pytest -v` exits 0
- All 75 existing market tests pass (no regressions)
- At least 15 new route tests pass
- `uv run --extra dev ruff check app/ tests/` exits 0 (no lint errors)
</acceptance_criteria>
</task>

## Verification

- `cd backend && uv run --extra dev pytest -v` — all tests pass (0 failures)
- Existing 75 market tests not regressed
- New route tests cover: health, portfolio GET, trade buy/sell, portfolio history, watchlist CRUD

## Must Haves

- [ ] All tests use isolated tmp_path SQLite DB (no shared state between tests)
- [ ] 75 existing market tests still pass
- [ ] New tests cover all BACK-01 through BACK-11 functionality
- [ ] ruff check passes (no lint errors in new files)
