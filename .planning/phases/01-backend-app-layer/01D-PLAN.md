---
plan: 01D
title: Watchlist API (GET, POST, DELETE /api/watchlist)
wave: 2
depends_on: [01A, 01B]
phase: 1
requirements_addressed: [BACK-07, BACK-08, BACK-09]
files_modified:
  - backend/app/routes/watchlist.py
  - backend/app/main.py
autonomous: true
---

# Plan 01D: Watchlist API

## Objective

Build all three watchlist endpoints. The GET endpoint combines DB watchlist rows with live prices from PriceCache (no price stored in DB — always from cache).

## Tasks

<task id="01D-1">
<title>Create routes/watchlist.py with all three watchlist endpoints</title>

<read_first>
- backend/app/db/connection.py (get_conn, DB_PATH)
- backend/app/market/cache.py (PriceCache.get(ticker), get_all(), get_price(ticker))
- backend/app/routes/portfolio.py (factory pattern, error handling pattern)
- planning/PLAN.md §7 (watchlist table schema), §8 (watchlist endpoints spec)
- .planning/phases/01-backend-app-layer/01-CONTEXT.md
</read_first>

<action>
Create `backend/app/routes/watchlist.py` with factory `create_watchlist_router(price_cache: PriceCache, db_path: str) -> APIRouter`.

Router: `prefix="/api/watchlist"`, `tags=["watchlist"]`

**GET /** → `get_watchlist(request: Request) -> dict`:
1. `conn = get_conn(db_path)`
2. `SELECT ticker, added_at FROM watchlist WHERE user_id='default' ORDER BY added_at ASC`
3. For each ticker, enrich with price from `price_cache.get(ticker)`:
   - `price`: `update.price if update else None`
   - `change_percent`: `update.change_percent if update else None`
   - `direction`: `update.direction if update else None` ("up"/"down"/"flat")
4. Return `{"tickers": [{"ticker", "added_at", "price", "change_percent", "direction"} ...]}`
5. Close connection

**POST /** → `add_ticker(body: AddTickerRequest, request: Request) -> dict`:
- `AddTickerRequest(ticker: str)` Pydantic model
- Validate: uppercase ticker, max 10 chars — 400 if invalid
- `INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) VALUES (...)`
- If ticker was already present: return 200 `{"status": "ok", "ticker": ticker}` (idempotent)
- After insert, also call `price_cache` — if ticker not in cache, it will remain None until market data provides it (acceptable)
- Return `{"status": "ok", "ticker": ticker.upper()}`

**DELETE /{ticker}** → `remove_ticker(ticker: str, request: Request) -> dict`:
- Uppercase ticker
- `DELETE FROM watchlist WHERE user_id='default' AND ticker=?`
- Return `{"status": "ok", "ticker": ticker}` — 200 even if ticker wasn't in watchlist (idempotent)
</action>

<acceptance_criteria>
- `backend/app/routes/watchlist.py` exists with `def create_watchlist_router(`
- `GET /api/watchlist` returns `{"tickers": [...]}` with price data for known tickers
- `POST /api/watchlist` with `{"ticker": "PYPL"}` adds ticker and returns 200
- `DELETE /api/watchlist/PYPL` removes ticker and returns 200
- `POST /api/watchlist` with already-existing ticker returns 200 (no error, idempotent)
- `DELETE /api/watchlist/NOTEXIST` returns 200 (idempotent)
</acceptance_criteria>
</task>

<task id="01D-2">
<title>Register watchlist router in main.py</title>

<read_first>
- backend/app/main.py (include_router calls — portfolio registered in 01C)
- backend/app/routes/watchlist.py (create_watchlist_router factory)
</read_first>

<action>
In `backend/app/main.py` lifespan (alongside portfolio router registration):
```python
from app.routes.watchlist import create_watchlist_router
watchlist_router = create_watchlist_router(price_cache, db_path)
app.include_router(watchlist_router)
```

Also: update the market data source start to use the actual watchlist from DB instead of hardcoded `SEED_PRICES.keys()`. After `init_db()`:
```python
conn = get_conn(db_path)
rows = conn.execute("SELECT ticker FROM watchlist WHERE user_id='default'").fetchall()
tickers = [row["ticker"] for row in rows]
conn.close()
await source.start(tickers if tickers else list(SEED_PRICES.keys()))
```
This ensures if the user has customized their watchlist and restarts, the simulator runs with their tickers.
</action>

<acceptance_criteria>
- `app.include_router(watchlist_router)` present in `backend/app/main.py`
- `GET /api/watchlist` returns 200 with tickers list when server running
- `POST /api/watchlist` `{"ticker": "TSLA"}` returns 200
- `DELETE /api/watchlist/TSLA` returns 200
- Market source started with tickers from DB (not hardcoded), falling back to SEED_PRICES
</acceptance_criteria>
</task>

## Verification

- `GET /api/watchlist` returns 200 with 10 tickers (default seed) plus price data
- `POST /api/watchlist` `{"ticker": "PYPL"}` → ticker appears in next `GET /api/watchlist`
- `DELETE /api/watchlist/PYPL` → ticker removed from next `GET /api/watchlist`
- Tickers with no price data (just added, not in cache yet) have `price: null` — no error

## Must Haves

- [ ] Watchlist GET always uses in-memory price cache (not DB prices)
- [ ] Add ticker is idempotent (INSERT OR IGNORE)
- [ ] Remove ticker is idempotent (DELETE, no 404)
- [ ] Ticker normalization: uppercase always
