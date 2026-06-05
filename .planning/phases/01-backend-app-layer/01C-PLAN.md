---
plan: 01C
title: Portfolio API (GET /api/portfolio, POST /api/portfolio/trade, GET /api/portfolio/history)
wave: 2
depends_on: [01A, 01B]
phase: 1
requirements_addressed: [BACK-04, BACK-05, BACK-06, BACK-10]
files_modified:
  - backend/app/routes/portfolio.py
  - backend/app/main.py
autonomous: true
---

# Plan 01C: Portfolio API

## Objective

Build the three portfolio endpoints and the background 30-second portfolio snapshot task. Portfolio value = cash + sum(position.qty × current_price from PriceCache).

## Tasks

<task id="01C-1">
<title>Create routes/portfolio.py with GET /api/portfolio</title>

<read_first>
- backend/app/db/connection.py (get_conn, DB_PATH pattern)
- backend/app/market/cache.py (PriceCache.get_price(ticker), get_all())
- planning/PLAN.md §7 (positions table schema), §8 (GET /api/portfolio response spec)
- .planning/phases/01-backend-app-layer/01-CONTEXT.md (portfolio response shape decision)
</read_first>

<action>
Create `backend/app/routes/portfolio.py` with a factory function `create_portfolio_router(price_cache: PriceCache, db_path: str) -> APIRouter`.

Router: `prefix="/api/portfolio"`, `tags=["portfolio"]`

`GET /` → `get_portfolio(request: Request) -> dict`:
1. `conn = get_conn(db_path); conn.row_factory = sqlite3.Row`
2. Fetch user: `SELECT cash_balance FROM users_profile WHERE id = 'default'`
3. Fetch all positions: `SELECT ticker, quantity, avg_cost FROM positions WHERE user_id = 'default'`
4. For each position, get current_price from `price_cache.get_price(ticker)` (use 0.0 if None)
5. Calculate `unrealized_pnl = (current_price - avg_cost) * quantity`
6. Calculate `pnl_pct = ((current_price - avg_cost) / avg_cost * 100) if avg_cost > 0 else 0.0`
7. Calculate `total_value = cash_balance + sum(qty * current_price for all positions)`
8. Return: `{"cash": cash_balance, "total_value": total_value, "positions": [{"ticker", "quantity", "avg_cost", "current_price", "unrealized_pnl", "pnl_pct"} ...]}`
9. Close connection in finally block

Use `conn.close()` in a `finally` block after each request handler.
</action>

<acceptance_criteria>
- `backend/app/routes/portfolio.py` contains `def create_portfolio_router(`
- `GET /api/portfolio` returns JSON with keys: `cash`, `total_value`, `positions`
- Each position dict has keys: `ticker`, `quantity`, `avg_cost`, `current_price`, `unrealized_pnl`, `pnl_pct`
- Returns 200 on fresh DB (no positions: `positions=[]`, `cash=10000.0`)
</acceptance_criteria>
</task>

<task id="01C-2">
<title>Add POST /api/portfolio/trade — market order execution</title>

<read_first>
- backend/app/routes/portfolio.py (same router, add to it)
- backend/app/db/connection.py (get_conn)
- planning/PLAN.md §7 (positions, trades tables), §8 (trade endpoint spec)
- .planning/phases/01-backend-app-layer/01-CONTEXT.md (trade validation and error format)
</read_first>

<action>
Add Pydantic model in portfolio.py:
```
class TradeRequest(BaseModel):
    ticker: str
    quantity: float
    side: str  # "buy" or "sell"
```

Add `POST /` → `execute_trade(body: TradeRequest, request: Request) -> dict`:
1. Get `current_price = price_cache.get_price(body.ticker.upper())` — if None, return 400 `{"error": "Ticker not found in price cache"}`
2. Validate `side` in `{"buy", "sell"}` — 400 if invalid
3. Validate `quantity > 0` — 400 if not
4. `conn = get_conn(db_path)`
5. Fetch `cash_balance` from `users_profile WHERE id='default'`
6. **BUY:** Check `cash_balance >= body.quantity * current_price`; if not, 400 `{"error": "Insufficient cash"}`
   - Deduct cash: `UPDATE users_profile SET cash_balance = cash_balance - ? WHERE id='default'`
   - Upsert position: use `INSERT INTO positions ... ON CONFLICT(user_id, ticker) DO UPDATE SET quantity = quantity + excluded.quantity, avg_cost = (avg_cost * quantity + excluded.avg_cost * excluded.quantity) / (quantity + excluded.quantity), updated_at = excluded.updated_at`
   - Insert trade record: `INSERT INTO trades (...) VALUES (...)`
7. **SELL:** Fetch current `quantity` from positions; check `quantity >= body.quantity`; 400 if not
   - Add cash: `UPDATE users_profile SET cash_balance = cash_balance + ? WHERE id='default'`
   - Update position: reduce quantity, remove row if quantity reaches 0 (`DELETE FROM positions WHERE user_id='default' AND ticker=? AND quantity <= 0`)
   - Insert trade record
8. Call `_record_snapshot(conn, price_cache)` immediately after trade
9. Commit and close
10. Return `{"status": "ok", "ticker": ticker, "side": side, "quantity": qty, "price": price, "trade_id": uuid}`

All validation errors: HTTP 400 with `{"error": "message"}`.
</action>

<acceptance_criteria>
- `POST /api/portfolio/trade` with `{"ticker":"AAPL","quantity":1,"side":"buy"}` returns 200 and deducts cash
- `POST /api/portfolio/trade` with quantity exceeding cash returns 400 `{"error": "Insufficient cash"}`
- `POST /api/portfolio/trade` sell with more shares than owned returns 400
- `POST /api/portfolio/trade` buy with unknown ticker returns 400 `{"error": "Ticker not found in price cache"}`
- Trade record inserted in `trades` table after successful buy
- Portfolio snapshot recorded in `portfolio_snapshots` after each trade
</acceptance_criteria>
</task>

<task id="01C-3">
<title>Add GET /api/portfolio/history endpoint</title>

<read_first>
- backend/app/routes/portfolio.py (same router)
- planning/PLAN.md §7 (portfolio_snapshots table), §8 (history endpoint spec)
</read_first>

<action>
Add `GET /history` → `get_portfolio_history(request: Request) -> dict`:
1. `conn = get_conn(db_path)`
2. `SELECT total_value, recorded_at FROM portfolio_snapshots WHERE user_id='default' ORDER BY recorded_at ASC LIMIT 500`
3. Return `{"snapshots": [{"total_value": float, "recorded_at": str} ...]}`
4. Close connection

Add private helper `_record_snapshot(conn: sqlite3.Connection, price_cache: PriceCache) -> None`:
1. Fetch `cash_balance` from `users_profile`
2. Fetch all `positions` (ticker, quantity)
3. Calculate `total_value = cash_balance + sum(qty * price_cache.get_price(t) or 0 for t, qty in positions)`
4. `INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at) VALUES (...)`
</action>

<acceptance_criteria>
- `GET /api/portfolio/history` returns `{"snapshots": [...]}` with HTTP 200
- Each snapshot has keys `total_value` (float) and `recorded_at` (ISO string)
- Returns empty snapshots list `{"snapshots": []}` on fresh DB (before any trades)
</acceptance_criteria>
</task>

<task id="01C-4">
<title>Add 30-second background portfolio snapshot task to main.py</title>

<read_first>
- backend/app/main.py (lifespan context manager)
- backend/app/market/simulator.py (_run_loop pattern — asyncio task with CancelledError handling)
- backend/app/routes/portfolio.py (_record_snapshot helper)
- planning/PLAN.md §8 (background snapshot task spec)
</read_first>

<action>
In `backend/app/main.py`, add background snapshot task inside the lifespan:

1. Import `_record_snapshot` from `app.routes.portfolio` or extract to `app.db.snapshots`
2. Create async task function `_snapshot_loop(price_cache, db_path, interval=30)`:
   ```
   while True:
       try:
           conn = get_conn(db_path)
           _record_snapshot(conn, price_cache)
           conn.close()
       except Exception:
           logger.exception("Snapshot loop error")
       await asyncio.sleep(interval)
   ```
3. In lifespan, after `await source.start(...)`:
   ```
   snapshot_task = asyncio.create_task(_snapshot_loop(price_cache, db_path))
   app.state.snapshot_task = snapshot_task
   ```
4. In lifespan cleanup (after yield):
   ```
   snapshot_task.cancel()
   try:
       await snapshot_task
   except asyncio.CancelledError:
       pass
   ```
</action>

<acceptance_criteria>
- `backend/app/main.py` contains `asyncio.create_task` for snapshot loop
- Snapshot loop has try/except with `logger.exception` and `asyncio.CancelledError` handling
- `asyncio.sleep(30)` or configurable interval in the loop body
- Snapshot task is cancelled in lifespan cleanup block
</acceptance_criteria>
</task>

<task id="01C-5">
<title>Register portfolio router in main.py</title>

<read_first>
- backend/app/main.py (include_router calls)
- backend/app/routes/portfolio.py (create_portfolio_router factory)
</read_first>

<action>
In `backend/app/main.py` lifespan (after price_cache and db_path are set):
```python
from app.routes.portfolio import create_portfolio_router
portfolio_router = create_portfolio_router(price_cache, db_path)
app.include_router(portfolio_router)
```

`db_path` should come from `os.getenv("DB_PATH", "db/finally.db")` in main.py (same value as in connection.py).
</action>

<acceptance_criteria>
- `app.include_router(portfolio_router)` present in `backend/app/main.py`
- `GET /api/portfolio` returns 200 when server is running
- `POST /api/portfolio/trade` returns 200 for valid buy with sufficient cash
- `GET /api/portfolio/history` returns 200
</acceptance_criteria>
</task>

## Verification

- `GET /api/portfolio` returns `{"cash": 10000.0, "total_value": 10000.0, "positions": []}`
- `POST /api/portfolio/trade` `{"ticker":"AAPL","quantity":1,"side":"buy"}` → cash decreases by current AAPL price, position appears
- `GET /api/portfolio/history` → returns snapshots (at least 1 after trade)
- Insufficient-cash buy returns HTTP 400

## Must Haves

- [ ] Portfolio total_value = cash + sum of (qty × current_price) for all positions
- [ ] Trade validation: buy checks cash, sell checks quantity
- [ ] Portfolio snapshot recorded after every trade
- [ ] Background snapshot every 30 seconds (asyncio task, not blocking)
- [ ] All validation errors return HTTP 400 with `{"error": "message"}`
