# Phase 2: LLM Chat Integration - Pattern Map

**Mapped:** 2026-06-05
**Files analyzed:** 5 (2 NEW, 3 MODIFIED)
**Analogs found:** 5 / 5

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `backend/app/routes/chat.py` (NEW) | route/controller | request-response (LLM + auto-exec) | `backend/app/routes/portfolio.py` | role-match (exact route-factory structure; different data flow) |
| `backend/app/routes/portfolio.py` (REFACTOR) | route/service | CRUD | itself (`execute_trade` lines 121-233) | exact (in-place extract) |
| `backend/app/routes/watchlist.py` (REFACTOR/reference) | route/service | CRUD | itself (`add_ticker`/`remove_ticker` lines 72-119) | exact (in-place extract) |
| `backend/app/main.py` (EDIT) | config/wiring | event-driven (lifespan) | itself (lines 77-85) | exact (copy router-registration block) |
| `backend/tests/test_chat.py` (NEW) | test | request-response | `backend/tests/test_portfolio.py` | exact (same fixture + integration style) |

**Note on `create_chat_router` signature:** The canonical refs and research disagree on arity. Existing routers are `create_X_router(price_cache, db_path)`. Research suggests `create_chat_router(price_cache, market_source, db_path)` to allow watchlist source mirroring — but Pitfall 6 / Open Question 1 conclude chat watchlist-add should be DB-only (matching the existing manual route which does NOT touch the market source). **Recommendation for planner: use `create_chat_router(price_cache, db_path)` to match existing routers and the DB-only decision, dropping `market_source`.** The conftest fixture then needs no source argument. If the planner instead wires the source, all three (factory, `main.py`, conftest) must pass it consistently.

## Pattern Assignments

### `backend/app/routes/chat.py` (NEW — route, request-response + auto-exec)

**Analog:** `backend/app/routes/portfolio.py`

**Imports + module setup** — copy header structure from `portfolio.py` lines 12-26, add litellm/json/os/asyncio:
```python
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.db.connection import get_conn
from app.market.cache import PriceCache
from app.routes.portfolio import execute_trade_on_conn          # NEW helper (see refactor below)
from app.routes.watchlist import apply_watchlist_change_on_conn # NEW helper (see refactor below)

logger = logging.getLogger(__name__)

MODEL = "openrouter/openai/gpt-oss-120b"
EXTRA_BODY = {"provider": {"order": ["cerebras"]}}
```
> `from litellm import completion` should be imported lazily inside the non-mock branch (or at top guarded) so tests with `LLM_MOCK=true` never require litellm network/key. Top-level import is fine since litellm is installed; the mock branch must simply not call `completion`.

**Pydantic models** — mirror `TradeRequest` (portfolio.py lines 29-32) / `AddTickerRequest` (watchlist.py lines 28-29). `ChatResponse` is BOTH the response body AND the `response_format` schema:
```python
class ChatRequest(BaseModel):
    message: str

class TradeInstruction(BaseModel):
    ticker: str
    side: str           # "buy" | "sell"
    quantity: float

class WatchlistChange(BaseModel):
    ticker: str
    action: str         # "add" | "remove"

class ChatResponse(BaseModel):
    message: str
    trades: list[TradeInstruction] = []
    watchlist_changes: list[WatchlistChange] = []
```

**Factory pattern** — copy exactly from `portfolio.py` lines 63-73 and `watchlist.py` lines 32-42:
```python
def create_chat_router(price_cache: PriceCache, db_path: str) -> APIRouter:
    router = APIRouter(prefix="/api/chat", tags=["chat"])

    @router.post("/")
    async def chat(body: ChatRequest, request: Request) -> dict:
        ...

    return router
```
> Prefix note: PLAN.md §8 specifies `POST /api/chat` (no trailing path). Use `prefix="/api/chat"` + `@router.post("/")` to match how portfolio/watchlist mount (clients hit `/api/chat/` — verify against frontend contract; if exact `/api/chat` with no slash is required, use `prefix="/api"` + `@router.post("/chat")`).

**LiteLLM call wrapped in `asyncio.to_thread`** — VERIFIED pattern from `massive_client.py:97` + cerebras SKILL.md:
```python
# Mock branch FIRST (CHAT-06 / D-07) — construct ChatResponse directly, fall through to auto-exec
if os.getenv("LLM_MOCK", "false").lower() == "true":
    parsed = ChatResponse(
        message="I've added PYPL to your watchlist and bought 5 shares of AAPL for you.",
        trades=[TradeInstruction(ticker="AAPL", side="buy", quantity=5)],
        watchlist_changes=[WatchlistChange(ticker="PYPL", action="add")],
    )
else:
    from litellm import completion
    try:
        response = await asyncio.to_thread(
            completion,
            model=MODEL,
            messages=messages,
            response_format=ChatResponse,
            reasoning_effort="low",
            extra_body=EXTRA_BODY,
        )
        parsed = ChatResponse.model_validate_json(response.choices[0].message.content)
    except Exception:
        logger.exception("LLM call/parse failed")
        return JSONResponse(status_code=500, content={"error": "LLM unavailable"})
```

**Auto-execution loop + error-as-outcome** — uses extracted helpers; one bad trade must NOT raise (Pitfall 5):
```python
conn = get_conn(db_path)
try:
    # ... context assembly + history load (reads) on same conn ...
    trade_outcomes = [
        execute_trade_on_conn(conn, price_cache, t.ticker, t.side, t.quantity)
        for t in parsed.trades
    ]
    watch_outcomes = [
        apply_watchlist_change_on_conn(conn, w.ticker, w.action)
        for w in parsed.watchlist_changes
    ]
    actions = {"trades": trade_outcomes, "watchlist_changes": watch_outcomes}
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO chat_messages (id, user_id, role, content, actions, created_at) "
        "VALUES (?, 'default', 'user', ?, NULL, ?)",
        (str(uuid.uuid4()), body.message, now),
    )
    conn.execute(
        "INSERT INTO chat_messages (id, user_id, role, content, actions, created_at) "
        "VALUES (?, 'default', 'assistant', ?, ?, ?)",
        (str(uuid.uuid4()), parsed.message, json.dumps(actions), now),
    )
    conn.commit()
    return {"message": parsed.message, "trades": trade_outcomes, "watchlist_changes": watch_outcomes}
finally:
    conn.close()
```

**Context assembly + history load** — see Shared Patterns below. Build `messages[]` as: `[{"role":"system","content":system_prompt+context}, *history, {"role":"user","content":body.message}]`.

---

### `backend/app/routes/portfolio.py` (REFACTOR — extract `execute_trade_on_conn`)

**Analog:** itself — the existing `execute_trade` body, lines 143-227.

**Extraction target:** Lines 121-233 (`execute_trade`) contain validation (lines 128-141), the DB mutation block (lines 143-218), and the success dict (lines 220-227). Extract everything EXCEPT the `JSONResponse` wrapping into a connection-level helper.

**New helper signature + return contract** (returns outcome dicts, never raises on validation failure):
```python
def execute_trade_on_conn(
    conn: sqlite3.Connection,
    price_cache: PriceCache,
    ticker: str,
    side: str,
    quantity: float,
) -> dict:
    """Validate + execute a trade on an open connection. Returns outcome dict.

    Success: {"status": "executed", "ticker", "side", "quantity", "price", "trade_id"}
    Failure: {"status": "failed", "ticker", "error": "..."}  (does NOT raise)
    """
    ticker = ticker.upper()
    side = side.lower()
    current_price = price_cache.get_price(ticker)
    if current_price is None:
        return {"status": "failed", "ticker": ticker, "error": "Ticker not found in price cache"}
    if side not in {"buy", "sell"}:
        return {"status": "failed", "ticker": ticker, "error": "Side must be 'buy' or 'sell'"}
    if quantity <= 0:
        return {"status": "failed", "ticker": ticker, "error": "Quantity must be greater than 0"}
    # ... lines 145-218 body verbatim: cash check (return failed dict instead of JSONResponse),
    #     weighted-avg upsert (lines 167-178), sell path (lines 180-207), trade log (lines 210-213),
    #     commit, _record_snapshot(conn, price_cache) (line 218) ...
    return {"status": "executed", "ticker": ticker, "side": side,
            "quantity": quantity, "price": current_price, "trade_id": trade_id}
```

**Weighted-average-cost upsert — copy verbatim, this is the load-bearing math (lines 167-178):**
```python
conn.execute(
    """
    INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at)
    VALUES (?, 'default', ?, ?, ?, ?)
    ON CONFLICT(user_id, ticker) DO UPDATE SET
        avg_cost = (avg_cost * quantity + excluded.avg_cost * excluded.quantity)
                   / (quantity + excluded.quantity),
        quantity = quantity + excluded.quantity,
        updated_at = excluded.updated_at
    """,
    (position_id, ticker, quantity, current_price, now),
)
```

**HTTP route becomes a thin wrapper** — CRITICAL: `test_portfolio.py` lines 50-58 assert `status 400` + `{"error": "Insufficient cash"}` exactly. The wrapper must map the helper's `{"status":"failed","error":E}` back to `JSONResponse(400, {"error": E})` and `{"status":"executed",...}` to the existing `{"status":"ok",...}` shape (note: existing route returns `"status": "ok"`, lines 220-221 — preserve that key for the HTTP path):
```python
@router.post("/trade")
async def execute_trade(body: TradeRequest, request: Request) -> dict:
    conn = get_conn(db_path)
    try:
        outcome = execute_trade_on_conn(conn, price_cache, body.ticker, body.quantity_args...)
    finally:
        conn.close()
    if outcome["status"] == "failed":
        return JSONResponse(status_code=400, content={"error": outcome["error"]})
    return {"status": "ok", **{k: outcome[k] for k in ("ticker","side","quantity","price","trade_id")}}
```
> Verification gate (research A3): run `cd backend && uv run --extra dev pytest tests/test_portfolio.py -x` after refactor; all existing expectations (lines 50-68) must stay green unchanged.

---

### `backend/app/routes/watchlist.py` (REFACTOR — extract `apply_watchlist_change_on_conn`)

**Analog:** itself — `add_ticker` (lines 72-98) and `remove_ticker` (lines 100-119).

**Extraction:** Combine the `INSERT OR IGNORE` (lines 91-93) and `DELETE` (lines 111-114) into one connection-level helper that branches on action. DB-only — does NOT touch market source (mirrors current behavior, Pitfall 6 / Open Question 1):
```python
def apply_watchlist_change_on_conn(conn: sqlite3.Connection, ticker: str, action: str) -> dict:
    ticker = ticker.strip().upper()
    action = action.lower()
    if not ticker:
        return {"status": "failed", "ticker": ticker, "error": "Ticker must not be empty"}
    if action == "add":
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) VALUES (?, 'default', ?, ?)",
            (str(uuid.uuid4()), ticker, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return {"status": "added", "ticker": ticker, "action": "add"}
    if action == "remove":
        conn.execute(
            "DELETE FROM watchlist WHERE user_id = 'default' AND ticker = ?",
            (ticker,),
        )
        conn.commit()
        return {"status": "removed", "ticker": ticker, "action": "remove"}
    return {"status": "failed", "ticker": ticker, "error": "Action must be 'add' or 'remove'"}
```
> The existing `add_ticker`/`remove_ticker` HTTP routes can be left as-is (chat imports the new helper) OR thinned to call it. Leaving them as-is is lowest-risk; extracting + reusing avoids divergence. Planner's call — either keeps watchlist tests green.

---

### `backend/app/main.py` (EDIT — register chat router in lifespan)

**Analog:** itself — the portfolio/watchlist registration block, lines 77-85.

**Copy this exact block pattern, add a third stanza:**
```python
# Chat router  (mirror lines 83-85)
from app.routes.chat import create_chat_router
chat_router = create_chat_router(price_cache, db_path)
app.include_router(chat_router)
```
Insert after line 85 (the watchlist registration), inside `lifespan`, before the snapshot-task creation (line 88). `price_cache` and `db_path` are already in scope.

---

### `backend/tests/test_chat.py` (NEW — integration tests, CHAT-01..06)

**Analog:** `backend/tests/test_portfolio.py` (lines 1-69) for structure; `conftest.py` for the fixture.

**Test class structure** — copy from test_portfolio.py lines 9-25:
```python
from __future__ import annotations
import pytest

@pytest.mark.asyncio
class TestChat:
    async def test_chat_returns_structured_response(self, chat_client):
        resp = await chat_client.post("/api/chat/", json={"message": "buy apple"})
        assert resp.status_code == 200
        data = resp.json()
        assert "message" in data
        assert "trades" in data and "watchlist_changes" in data
```
All tests set mock mode via `monkeypatch.setenv("LLM_MOCK", "true")` — but note the fixture must set it BEFORE the route reads `os.getenv` at request time (env is read per-request in the handler, so `monkeypatch.setenv` in the fixture or test body works).

**Fixture requirement (conftest.py extension — Wave 0 gap):** Extend `conftest.py` (lines 35-39) to register the chat router. Add to the existing `app_client` fixture OR add a parallel `chat_client` fixture:
```python
# In conftest.py, after line 39:
from app.routes.chat import create_chat_router
test_app.include_router(create_chat_router(price_cache, db_file))
# And set mock mode so chat tests never hit the network:
monkeypatch.setenv("LLM_MOCK", "true")
```
> The existing fixture already seeds `price_cache` with all `SEED_PRICES` (conftest lines 31-33), so the mock AAPL buy has a price. Adding `create_chat_router(price_cache, db_file)` matches the 2-arg signature recommendation above.

**Coverage map (from RESEARCH Test Map):** `test_chat_returns_structured_response` (CHAT-01), `test_response_schema_shape` (CHAT-02), `test_mock_trade_executes` + `test_failed_trade_in_outcomes` (CHAT-03), `test_mock_watchlist_add` (CHAT-04), `test_messages_persisted` + `test_history_loaded` (CHAT-05), `test_mock_mode_deterministic` (CHAT-06).

---

## Shared Patterns

### Route factory + dependency injection
**Source:** `portfolio.py` lines 63-73, `watchlist.py` lines 32-42
**Apply to:** `chat.py`
```python
def create_X_router(price_cache: PriceCache, db_path: str) -> APIRouter:
    router = APIRouter(prefix="/api/X", tags=["X"])
    @router.<method>("/...")
    async def handler(body: Model, request: Request) -> dict:
        ...
    return router
```

### Per-request connection lifecycle
**Source:** `portfolio.py` lines 78-79/118-119; `watchlist.py` lines 47-48/69-70
**Apply to:** all `chat.py` DB access — open ONE `get_conn(db_path)` per request, use for context read + auto-exec + message inserts, `close()` in `finally`. (Research Pattern 4.) Note: `_record_snapshot` and the watchlist helper each `commit()` mid-request — acceptable.

### Error responses use JSONResponse, NOT HTTPException
**Source:** `portfolio.py` lines 135/138/141/157/189; `watchlist.py` lines 83/86
**Apply to:** `chat.py` LLM-failure path → `JSONResponse(status_code=500, content={"error": "LLM unavailable"})`. Trade/watchlist failures are NOT HTTP errors here — they go into the response `actions` as `{"status":"failed","error":...}` (Pitfall 5).

### Ticker / side normalization
**Source:** `portfolio.py` lines 128-129 (`.upper()`/`.lower()`); `watchlist.py` line 80 (`.strip().upper()`)
**Apply to:** every LLM-supplied ticker before DB/cache use — `ticker.strip().upper()`, `side.lower()`, `action.lower()`. (Anti-pattern: trusting LLM casing.)

### Parameterized SQL only (V5 input validation)
**Source:** every `conn.execute(..., (params,))` in portfolio.py / watchlist.py
**Apply to:** all chat inserts/reads — never string-format SQL with LLM ticker. `chat_messages` insert columns: `id, user_id, role, content, actions, created_at` (schema.sql lines 51-59).

### `asyncio.to_thread` for blocking calls
**Source:** `massive_client.py:97` — `await asyncio.to_thread(self._fetch_snapshots)`
**Apply to:** the `litellm.completion` call in `chat.py` (only in the non-mock branch).

### Lifespan router registration
**Source:** `main.py` lines 77-85
**Apply to:** chat router registration (3-line stanza, same shape).

### UTC ISO timestamps + UUID PKs
**Source:** `portfolio.py` line 151 (`datetime.now(timezone.utc).isoformat()`), `str(uuid.uuid4())` throughout
**Apply to:** `chat_messages` row inserts.

### Portfolio context assembly (D-01/D-02)
**Source:** P&L math reused from `portfolio.py` `get_portfolio` lines 91-112 (`unrealized_pnl`, `pnl_pct`, `current_price = price_cache.get_price(ticker) or 0.0`)
**Apply to:** `_assemble_portfolio_context(conn, price_cache) -> str` in chat.py — build a compact text block: cash, total value, per-position line `ticker | qty | avg_cost | current_price | pnl | pnl%`, and watchlist as comma-separated tickers (`SELECT ticker FROM watchlist WHERE user_id='default' ORDER BY added_at ASC`, mirrors watchlist.py line 50). Use the same `price_cache.get_price(ticker) or 0.0` guard.

### Conversation history load (D-04)
**Source:** ordering idiom mirrors `portfolio.py` history query (lines 240-248, `ORDER BY ... ASC LIMIT`)
**Apply to:** `SELECT role, content FROM chat_messages WHERE user_id='default' ORDER BY created_at DESC LIMIT 20` then `list(reversed(rows))` for ascending chronological order.

### Cerebras LLM call signature
**Source:** `.claude/skills/cerebras/SKILL.md` lines 24-43
**Apply to:** `MODEL = "openrouter/openai/gpt-oss-120b"`, `EXTRA_BODY = {"provider": {"order": ["cerebras"]}}`, `completion(..., response_format=ChatResponse, reasoning_effort="low", extra_body=EXTRA_BODY)`, then `ChatResponse.model_validate_json(response.choices[0].message.content)`.

## No Analog Found

None — every new file has a strong existing analog. The only genuinely new code is the LiteLLM call (fully specified by the cerebras SKILL.md) and the mock branch (fully specified by D-06/D-07).

## Metadata

**Analog search scope:** `backend/app/routes/`, `backend/app/market/`, `backend/app/main.py`, `backend/tests/`, `backend/app/db/`, `.claude/skills/cerebras/`
**Files scanned:** 8 (portfolio.py, watchlist.py, main.py, conftest.py, test_portfolio.py, massive_client.py, SKILL.md, backend/CLAUDE.md)
**Pattern extraction date:** 2026-06-05
