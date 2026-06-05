# Phase 2: LLM Chat Integration - Research

**Researched:** 2026-06-05
**Domain:** LLM integration (LiteLLM → OpenRouter → Cerebras), FastAPI async route, structured output, auto-execution pipeline
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Portfolio Context in System Prompt**
- **D-01:** Inject core portfolio data only — cash balance, total portfolio value, positions (ticker, qty, avg cost, current price, unrealized P&L, P&L%), and watchlist ticker list (names only, not prices)
- **D-02:** Portfolio context format: compact text block injected into the system message at the start of each request, assembled fresh from PriceCache + DB at request time
- **D-03:** System prompt persona: "FinAlly, an AI trading assistant" — concise, data-driven, executes trades when asked

**Conversation History**
- **D-04:** Load last 20 messages from `chat_messages` table (10 user+assistant exchanges), ordered ascending by `created_at` so the LLM sees chronological context
- **D-05:** History is per-user (`user_id="default"`); no truncation strategy needed beyond the 20-message cap

**Mock Mode**
- **D-06:** `LLM_MOCK=true` returns a deterministic structured response that exercises the full auto-execution pipeline:
  - `message`: "I've added PYPL to your watchlist and bought 5 shares of AAPL for you."
  - `trades`: `[{"ticker": "AAPL", "side": "buy", "quantity": 5}]`
  - `watchlist_changes`: `[{"ticker": "PYPL", "action": "add"}]`
- **D-07:** Mock response bypasses LiteLLM entirely — construct the `ChatResponse` Pydantic object directly, then run the same auto-execution path as a real response. Tests verify the full pipeline.

### Claude's Discretion
- Pydantic model structure for `ChatRequest` and `ChatResponse` (follow existing route model patterns)
- `actions` JSON field format stored in `chat_messages` (record executed trades + watchlist changes with outcomes)
- Error handling for LLM call failures: return HTTP 500 with `{"error": "LLM unavailable"}` — no retry
- How to call litellm.completion in async context: `asyncio.to_thread` to avoid blocking the event loop (established pattern from MassiveDataSource)
- `reasoning_effort="low"` per the cerebras-inference skill — fast responses

### Deferred Ideas (OUT OF SCOPE)
- None — discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CHAT-01 | `POST /api/chat` sends user message to LLM with current portfolio context + history, returns structured JSON | Route factory pattern (portfolio.py); context assembly from PriceCache + DB; LiteLLM `completion` via cerebras skill |
| CHAT-02 | LLM response schema includes `message`, optional `trades`, optional `watchlist_changes` | Pydantic v2 `ChatResponse` model with `response_format=` structured output (cerebras SKILL.md) |
| CHAT-03 | Backend auto-executes trades (same validation as manual trades) | Extract trade-execution DB logic from `portfolio.py` into reusable helper — NOT via HTTP call |
| CHAT-04 | Backend auto-applies watchlist changes | Reuse `INSERT OR IGNORE` / `DELETE` SQL from `watchlist.py`; mirror cache add/remove via market source |
| CHAT-05 | Chat messages stored in `chat_messages` with executed actions recorded | `chat_messages` table exists in schema; store `actions` JSON on assistant row |
| CHAT-06 | `LLM_MOCK=true` returns deterministic mock without calling OpenRouter | Branch before LiteLLM call; construct `ChatResponse` directly (D-06/D-07) |
</phase_requirements>

## Summary

This phase adds a single endpoint, `POST /api/chat`, that wraps an LLM call in a request/response cycle. The heavy lifting is already de-risked: the `cerebras-inference` skill provides the exact LiteLLM call signature, `litellm>=1.87.1` is already installed and locked, the `chat_messages` table already exists in the schema, and the trade and watchlist execution logic already exist in `portfolio.py` and `watchlist.py`. No new packages are needed.

The core engineering work is **refactoring, not invention**: extract the trade-execution SQL from `portfolio.py`'s `execute_trade` into a reusable helper that both the HTTP route and the chat auto-executor call, so trades from the LLM go through the identical validation path (CHAT-03). The same applies to watchlist mutations. The chat route then becomes: assemble portfolio context → load history → call LiteLLM (or mock) → parse structured `ChatResponse` → loop over trades/watchlist_changes executing each and collecting per-item outcomes → persist user + assistant messages → return the response with outcomes attached.

The single biggest correctness risk is **`litellm.completion` is synchronous and will block the FastAPI event loop** if called directly inside an async route. The established codebase pattern (`MassiveDataSource._poll_once` at `massive_client.py:97`) is `await asyncio.to_thread(blocking_call)` — apply it identically here.

**Primary recommendation:** Refactor `portfolio.py` to expose a connection-level `execute_trade_on_conn(conn, price_cache, ticker, side, quantity) -> dict` helper returning `{"status": "executed"|"failed", ...}`; refactor `watchlist.py` similarly. Build `app/routes/chat.py` with a `create_chat_router(price_cache, market_source, db_path)` factory. Wrap the synchronous `litellm.completion` in `asyncio.to_thread`. Branch on `LLM_MOCK` before the LiteLLM call. Persist both messages, store outcomes in the `actions` JSON of the assistant row.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| LLM inference (prompt → structured JSON) | API / Backend | External (OpenRouter/Cerebras) | LiteLLM call must hold the API key and inject portfolio context server-side; never client-side |
| Portfolio context assembly | API / Backend | Database + in-memory PriceCache | Needs DB (cash, positions) + live prices (cache) — both backend-only |
| Trade auto-execution | API / Backend | Database | Must reuse the same validated DB mutation path as manual trades (CHAT-03) |
| Watchlist auto-execution | API / Backend | Database + market source | DB mutation plus live price-source add/remove |
| Conversation persistence | Database | — | `chat_messages` append + read |
| Mock-mode response | API / Backend | — | Deterministic branch, no external call |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| litellm | 1.87.1 (installed & locked) | Unified LLM client → OpenRouter → Cerebras | Mandated by project (PLAN.md §9) and `cerebras-inference` skill `[CITED: .claude/skills/cerebras/SKILL.md]` |
| pydantic | v2 (already in use via FastAPI) | Request/response models + structured output schema | Already used in portfolio/watchlist routes; `response_format=Model` for structured outputs `[CITED: SKILL.md]` |
| fastapi | >=0.115.0 (installed) | Route factory, JSONResponse error pattern | Established codebase convention `[VERIFIED: backend/pyproject.toml]` |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| asyncio (stdlib) | py3.12 | `asyncio.to_thread()` wrap for blocking `litellm.completion` | Always — `completion` is synchronous `[VERIFIED: massive_client.py:97 established pattern]` |
| sqlite3 (stdlib) | py3.12 | DB access via `get_conn(db_path)` | Context assembly + history load + message persist |
| json (stdlib) | py3.12 | Serialize `actions` field; parse `chat_messages.actions` on load | Persist/read outcomes |
| os (stdlib) | py3.12 | Read `LLM_MOCK` and `OPENROUTER_API_KEY` env | Mock branch + key presence check |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `asyncio.to_thread(completion, ...)` | `litellm.acompletion` (async-native) | `acompletion` exists in litellm and would avoid the thread, BUT the cerebras SKILL.md and the entire codebase standardize on the sync `completion` + `to_thread` pattern. Deviating adds an untested path. **Stick with `to_thread`.** `[ASSUMED]` that `acompletion` accepts the same `response_format`/`extra_body` — not verified, not needed. |
| Pydantic `response_format=Model` structured output | Manual JSON-mode + hand-parse | SKILL.md explicitly documents `response_format=MyModel` + `Model.model_validate_json(content)`. Use it. `[CITED: SKILL.md]` |

**Installation:**
```bash
# No new packages required — litellm>=1.87.1 and pydantic already in pyproject.toml
# Verified installed:
cd backend && uv run python -c "from litellm import completion; from importlib.metadata import version; print(version('litellm'))"
# → 1.87.1
```

**Version verification:** `litellm` confirmed at 1.87.1 in `uv.lock` and importable (`from litellm import completion` succeeds). `[VERIFIED: backend/uv.lock + uv run import]`

## Package Legitimacy Audit

> No external packages are installed in this phase. `litellm` and `pydantic` were already added and locked in Phase 0/1. This section is informational.

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|-------------|-----------|-------------|
| litellm | PyPI | mature (BerriAI) | very high | github.com/BerriAI/litellm | not re-run (pre-installed) | Already locked — no action |
| pydantic | PyPI | mature | very high | github.com/pydantic/pydantic | not re-run (pre-installed) | Already locked — no action |

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

*No new installs this phase, so no legitimacy gate is triggered. If the planner adds any package, run the full gate first.*

## Architecture Patterns

### System Architecture Diagram

```
                          POST /api/chat
                          { "message": "buy me some apple" }
                                  │
                                  ▼
                    ┌──────────────────────────────┐
                    │  create_chat_router(...)      │
                    │  async def chat(body)         │
                    └──────────────┬───────────────┘
                                   │
              ┌────────────────────┼─────────────────────┐
              ▼                    ▼                     ▼
     ┌─────────────────┐  ┌────────────────┐   ┌──────────────────┐
     │ assemble context│  │ load history   │   │  check LLM_MOCK   │
     │ DB: cash,       │  │ chat_messages  │   └────────┬─────────┘
     │ positions       │  │ last 20 ASC    │            │
     │ PriceCache:     │  └────────────────┘   mock?────┤
     │ current_price   │                          │     │ no
     │ watchlist names │                          │     ▼
     └────────┬────────┘                          │  ┌──────────────────────────┐
              │                                    │  │ messages = [system+ctx,   │
              ▼                                    │  │   ...history, user]       │
     build messages[] (system w/ context + hist + user)│  await asyncio.to_thread(│
              │                                    │  │   completion,             │
              │◄───────────────────────────────────┘  │   model=MODEL,            │
              │                                       │   response_format=ChatResp│
              │                                       │   reasoning_effort="low", │
              │                                       │   extra_body=EXTRA_BODY)  │
              │                                       └────────────┬─────────────┘
              ▼                                                    │
     mock: ChatResponse(...)  ◄───────────────────────────────────┘
              │           parse: ChatResponse.model_validate_json(content)
              ▼
     ┌─────────────────────────────────────────────┐
     │ AUTO-EXECUTE (single DB connection, reused)   │
     │  for t in response.trades:                    │
     │     execute_trade_on_conn(conn, cache, t...)  │──► positions, trades,
     │     → {status: executed|failed, price, error} │    cash, snapshot
     │  for w in response.watchlist_changes:         │
     │     apply_watchlist_change_on_conn(conn, w)   │──► watchlist + cache/source
     │     → {status: added|removed}                 │
     └────────────────────┬────────────────────────┘
                          ▼
     ┌─────────────────────────────────────────────┐
     │ PERSIST chat_messages                         │
     │  insert user row  (actions=NULL)              │
     │  insert assistant row (actions=outcomes JSON) │
     └────────────────────┬────────────────────────┘
                          ▼
              return { message, trades:[outcomes],
                       watchlist_changes:[outcomes] }
```

### Component Responsibilities

| Component | File | Responsibility |
|-----------|------|----------------|
| `create_chat_router` | `backend/app/routes/chat.py` (NEW) | Factory; owns the `POST /api/chat` handler, context assembly, LiteLLM/mock branch, auto-exec loop, persistence |
| `ChatRequest` / `ChatResponse` / `TradeInstruction` / `WatchlistChange` | `backend/app/routes/chat.py` (NEW) | Pydantic models; `ChatResponse` is the `response_format` schema |
| `execute_trade_on_conn(...)` | `backend/app/routes/portfolio.py` (REFACTOR) | Connection-level trade exec extracted from `execute_trade`; returns outcome dict instead of HTTP response |
| `apply_watchlist_change_on_conn(...)` | `backend/app/routes/watchlist.py` (REFACTOR) or in chat.py | Connection-level add/remove extracted from route handlers |
| `_assemble_portfolio_context(conn, price_cache) -> str` | `backend/app/routes/chat.py` (NEW) | Builds the compact text block (D-01/D-02) |
| lifespan registration | `backend/app/main.py` (EDIT) | `app.include_router(create_chat_router(price_cache, source, db_path))` |

### Pattern 1: Wrap synchronous `litellm.completion` in `asyncio.to_thread`
**What:** `litellm.completion` is a blocking sync call. Calling it directly in an `async def` route blocks the entire event loop (no other request can be served until the LLM responds).
**When to use:** Every real (non-mock) LLM call in this phase.
**Example:**
```python
# Source: cerebras SKILL.md (call signature) + massive_client.py:97 (to_thread pattern) [CITED/VERIFIED]
from litellm import completion

MODEL = "openrouter/openai/gpt-oss-120b"
EXTRA_BODY = {"provider": {"order": ["cerebras"]}}

response = await asyncio.to_thread(
    completion,
    model=MODEL,
    messages=messages,
    response_format=ChatResponse,      # Pydantic v2 model → structured output
    reasoning_effort="low",            # D: fast responses
    extra_body=EXTRA_BODY,
)
content = response.choices[0].message.content
parsed = ChatResponse.model_validate_json(content)
```

### Pattern 2: Extract execution logic to a connection-level helper (reuse, don't re-HTTP)
**What:** The chat auto-executor must NOT call `POST /api/portfolio/trade` over HTTP (circular, fragile). Instead, extract the DB mutation + validation into a function that takes an open `conn` and returns an outcome dict.
**When to use:** CHAT-03 (trades) and CHAT-04 (watchlist).
**Example:**
```python
# Source: refactor of portfolio.py execute_trade (lines 121-233) [VERIFIED: backend/app/routes/portfolio.py]
def execute_trade_on_conn(
    conn: sqlite3.Connection,
    price_cache: PriceCache,
    ticker: str,
    side: str,
    quantity: float,
) -> dict:
    """Validate + execute a trade on an open connection. Returns outcome dict.

    Returns {"status": "executed", "ticker", "side", "quantity", "price", "trade_id"}
    or {"status": "failed", "ticker", "error": "..."}.
    Does NOT raise on validation failure — failures are returned for the chat response.
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
    # ... same cash/shares validation + upsert + trade log + _record_snapshot as execute_trade ...
    # On success: return {"status": "executed", "ticker": ticker, "side": side,
    #                     "quantity": quantity, "price": current_price, "trade_id": trade_id}
```
Then the existing `execute_trade` HTTP route becomes a thin wrapper that calls this and maps `{"status": "failed", "error": ...}` → `JSONResponse(400)`. This keeps Phase 1 behavior identical while exposing the helper for chat.

> **Planner note:** Refactoring `execute_trade` must not change the existing 400 responses or `test_portfolio.py` expectations. The wrapper must still return `{"error": "Insufficient cash"}` with status 400 for the existing tests to pass.

### Pattern 3: Construct chat `messages[]`
```python
# Roles map directly to chat_messages.role ("user"/"assistant"); LiteLLM uses OpenAI message format.
messages = [{"role": "system", "content": system_prompt_with_context}]
for row in history_rows:           # last 20, ascending by created_at (D-04)
    messages.append({"role": row["role"], "content": row["content"]})
messages.append({"role": "user", "content": body.message})
```

### Pattern 4: Reuse a single DB connection across context + exec + persist
Open one `get_conn(db_path)` per request, use it for context assembly read, then for each auto-exec, then for the two message inserts, then `close()` in `finally`. Note `_record_snapshot` (called by trade exec) does its own `commit()` — that is acceptable but be aware it commits mid-request.

### Anti-Patterns to Avoid
- **Calling `litellm.completion` directly in the async route** — blocks the event loop. Always `asyncio.to_thread`.
- **Auto-executing trades via an HTTP call to `/api/portfolio/trade`** — circular, requires a running server, breaks under ASGI test transport. Use the extracted connection-level helper.
- **Letting one failed trade abort the whole chat response** — per CHAT-03 and PLAN.md §9, a failed trade's error must be *included in the response* so the LLM/user is informed. Collect outcomes; never raise out of the loop on a validation failure.
- **Trusting LLM ticker casing/whitespace** — normalize `ticker.strip().upper()` exactly as the existing routes do before execution.
- **Returning HTTP 200 with raw LLM content on parse failure** — if `model_validate_json` fails, that's a 500-class error (LLM produced invalid structure).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| LLM provider routing / auth | Custom OpenRouter HTTP client | `litellm.completion` + `extra_body={"provider":{"order":["cerebras"]}}` | SKILL.md mandates it; handles retries, headers, key |
| JSON-mode parsing | Manual `json.loads` + key checks | `response_format=ChatResponse` + `ChatResponse.model_validate_json` | Structured outputs guarantee shape; Pydantic validates |
| Trade validation (cash/shares) | New validation in chat.py | Extracted `execute_trade_on_conn` helper from portfolio.py | Single source of truth; CHAT-03 demands identical path |
| Watchlist add/remove SQL | New INSERT/DELETE in chat.py | Extracted helper mirroring watchlist.py | Idempotent `INSERT OR IGNORE` / `DELETE` already correct |
| Async wrapping of blocking calls | Custom thread pool | `asyncio.to_thread` | Stdlib, matches massive_client.py |

**Key insight:** This phase is ~80% reuse. The risk is *re-implementing* trade/watchlist logic slightly differently in chat.py, producing two divergent code paths. Extract-and-share is the whole game.

## Runtime State Inventory

> This phase is additive (new endpoint + small refactor), not a rename/migration. Inventory included because the refactor touches existing files.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | `chat_messages` table already exists in `schema.sql` (id, user_id, role, content, actions, created_at). No new columns needed. | None — schema is ready (verified `backend/app/db/schema.sql:51-59`) |
| Live service config | None — `OPENROUTER_API_KEY` read from `.env` at runtime by litellm; already documented in PLAN.md §5 | None |
| OS-registered state | None | None — verified, no OS registrations involved |
| Secrets/env vars | `OPENROUTER_API_KEY` (litellm reads from env automatically), `LLM_MOCK` (new read in chat.py) | Ensure `.env.example` documents both (already in PLAN.md §5; INFRA-06 owns the file) |
| Build artifacts | None — no package version/name changes; litellm already in lockfile | None |

**Refactor-specific runtime risk:** Extracting `execute_trade_on_conn` from `portfolio.py` changes the internals that `tests/test_portfolio.py` exercises through the HTTP route. Existing tests must continue to pass unchanged. Verified by running `uv run --extra dev pytest tests/test_portfolio.py` after the refactor.

## Common Pitfalls

### Pitfall 1: Blocking the event loop with synchronous `completion`
**What goes wrong:** Under load (or even during a single slow request) the whole FastAPI server stalls; SSE price stream and other endpoints freeze.
**Why it happens:** `litellm.completion` is synchronous; `async def` does not make sync calls non-blocking.
**How to avoid:** `await asyncio.to_thread(completion, ...)` — exactly the `massive_client.py:97` pattern.
**Warning signs:** SSE clients stop receiving ticks while a chat request is in flight; test timeouts.

### Pitfall 2: Re-implementing trade validation in chat.py (path divergence)
**What goes wrong:** Chat trades behave subtly differently from manual trades (e.g., different avg-cost math, missing snapshot).
**Why it happens:** Copy-pasting instead of extracting a shared helper.
**How to avoid:** Single `execute_trade_on_conn` helper called by both the HTTP route and chat auto-exec.
**Warning signs:** Two places that compute weighted average cost; snapshot recorded for manual but not chat trades.

### Pitfall 3: Mock mode partially bypasses the pipeline
**What goes wrong:** Mock returns a canned response but skips auto-execution, so E2E tests don't actually verify trades/watchlist changes happen.
**Why it happens:** Returning early before the auto-exec loop.
**How to avoid:** D-07 — mock constructs the `ChatResponse` object, then falls through into the *same* auto-exec + persist code. Only the LiteLLM call is skipped.
**Warning signs:** `LLM_MOCK=true` chat returns the message but cash is unchanged and PYPL not in watchlist.

### Pitfall 4: Structured output content is a string, not a dict
**What goes wrong:** `response.choices[0].message.content` is a JSON *string*; treating it as a dict raises.
**Why it happens:** Forgetting the parse step.
**How to avoid:** `ChatResponse.model_validate_json(content)` per SKILL.md — never `content["message"]`.
**Warning signs:** `TypeError: string indices must be integers`.

### Pitfall 5: One bad LLM trade aborts the whole response
**What goes wrong:** LLM asks to sell 1000 shares the user doesn't own; an exception escapes the loop and the user gets a 500 with no message.
**Why it happens:** Helper raises on validation failure instead of returning a failure outcome.
**How to avoid:** `execute_trade_on_conn` returns `{"status": "failed", "error": ...}` for validation failures (does not raise). Loop collects outcomes. Only unexpected exceptions propagate.
**Warning signs:** Valid message + invalid trade → 500 instead of 200-with-error-in-outcomes.

### Pitfall 6: Watchlist auto-exec updates DB but not the live price source
**What goes wrong:** AI adds PYPL to watchlist; it appears in `GET /api/watchlist` but has no price (never started in the market source) — or removed tickers keep streaming.
**Why it happens:** The DB-only add/remove doesn't touch `market_source.add_ticker()/remove_ticker()` or `price_cache`.
**How to avoid:** Inspect what `POST /api/watchlist` does today — the current `add_ticker` route (watchlist.py:72-98) only writes the DB and does NOT call the market source. **Mirror existing behavior exactly** (DB-only) to keep parity, unless the planner decides to also wire the source. Document the choice. `[VERIFIED: watchlist.py has no source/cache call]`
**Warning signs:** AI-added ticker shows `price: null` — but note this matches the current manual-add behavior, so it is consistent, not a regression.

> **Planner decision needed:** Current manual watchlist add does NOT start the ticker in the market source (DB-only). For consistency, chat watchlist add should do the same (DB-only). If live prices for AI-added tickers are desired, that is a separate enhancement touching both routes — out of this phase's scope per requirements.

## Code Examples

### ChatResponse structured-output schema (Pydantic v2)
```python
# Source: cerebras SKILL.md (response_format=Model) + PLAN.md §9 schema [CITED]
from __future__ import annotations
from pydantic import BaseModel

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

class ChatRequest(BaseModel):
    message: str
```

### Mock-mode branch (D-06/D-07)
```python
# Source: CONTEXT.md D-06/D-07
import os

def _build_response(messages: list[dict]) -> ChatResponse:
    if os.getenv("LLM_MOCK", "false").lower() == "true":
        return ChatResponse(
            message="I've added PYPL to your watchlist and bought 5 shares of AAPL for you.",
            trades=[TradeInstruction(ticker="AAPL", side="buy", quantity=5)],
            watchlist_changes=[WatchlistChange(ticker="PYPL", action="add")],
        )
    response = await asyncio.to_thread(
        completion, model=MODEL, messages=messages,
        response_format=ChatResponse, reasoning_effort="low", extra_body=EXTRA_BODY,
    )
    return ChatResponse.model_validate_json(response.choices[0].message.content)
# (pseudo — make the function async; mock branch returns before awaiting)
```

### `actions` JSON persisted on the assistant message (Claude's discretion → recommended format)
```python
# Source: CONTEXT.md "specifics" section — recommended outcome format
actions = {
    "trades": [
        {"ticker": "AAPL", "side": "buy", "quantity": 5, "price": 187.50, "status": "executed"}
    ],
    "watchlist_changes": [
        {"ticker": "PYPL", "action": "add", "status": "added"}
    ],
}
# user row:
conn.execute(
    "INSERT INTO chat_messages (id, user_id, role, content, actions, created_at) "
    "VALUES (?, 'default', 'user', ?, NULL, ?)",
    (str(uuid.uuid4()), body.message, now),
)
# assistant row:
conn.execute(
    "INSERT INTO chat_messages (id, user_id, role, content, actions, created_at) "
    "VALUES (?, 'default', 'assistant', ?, ?, ?)",
    (str(uuid.uuid4()), response.message, json.dumps(actions), now),
)
conn.commit()
```

### Load conversation history (D-04)
```python
# Last 20 in chronological order: take the 20 most recent, then reverse to ascending.
rows = conn.execute(
    "SELECT role, content FROM chat_messages WHERE user_id = 'default' "
    "ORDER BY created_at DESC LIMIT 20"
).fetchall()
history = list(reversed(rows))   # ascending by created_at (D-04)
```

### Portfolio context text block (D-01/D-02)
```python
def _assemble_portfolio_context(conn, price_cache) -> str:
    cash = conn.execute("SELECT cash_balance FROM users_profile WHERE id='default'").fetchone()["cash_balance"]
    pos_rows = conn.execute(
        "SELECT ticker, quantity, avg_cost FROM positions WHERE user_id='default'"
    ).fetchall()
    lines, market_value = [], 0.0
    for r in pos_rows:
        cur = price_cache.get_price(r["ticker"]) or 0.0
        pnl = (cur - r["avg_cost"]) * r["quantity"]
        pnl_pct = ((cur - r["avg_cost"]) / r["avg_cost"] * 100) if r["avg_cost"] else 0.0
        market_value += r["quantity"] * cur
        lines.append(f"{r['ticker']} | qty {r['quantity']} | avg {r['avg_cost']:.2f} | "
                     f"cur {cur:.2f} | pnl {pnl:.2f} | pnl% {pnl_pct:.2f}")
    total = cash + market_value
    watch = [w["ticker"] for w in conn.execute(
        "SELECT ticker FROM watchlist WHERE user_id='default' ORDER BY added_at ASC").fetchall()]
    positions_block = "\n".join(lines) if lines else "(no open positions)"
    return (
        f"Cash: ${cash:.2f}\nTotal portfolio value: ${total:.2f}\n"
        f"Positions (ticker | qty | avg_cost | current_price | pnl | pnl%):\n{positions_block}\n"
        f"Watchlist: {', '.join(watch)}"
    )
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Raw OpenAI SDK + manual provider headers | LiteLLM unified `completion` with `extra_body` provider routing | n/a (project decision) | Project standard; do not deviate |
| JSON mode + `json.loads` + manual validation | Pydantic `response_format=Model` structured outputs | litellm modern versions | Reliable parsing; SKILL.md documents it |

**Deprecated/outdated:**
- Do not use `litellm.completion(..., functions=...)` (legacy function-calling). Use `response_format=` structured output per SKILL.md.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `litellm.acompletion` accepts the same `response_format`/`extra_body` as `completion` | Alternatives | LOW — we deliberately use sync `completion` + `to_thread`, so acompletion is never called |
| A2 | OpenRouter+Cerebras honors `response_format=PydanticModel` for `openai/gpt-oss-120b` and returns valid JSON in `message.content` | Code Examples | MEDIUM — SKILL.md documents this pattern as supported; if the model returns malformed JSON, `model_validate_json` raises → handle as 500 ("LLM unavailable"). Mock mode (CHAT-06) and all tests run with `LLM_MOCK=true`, so CI is unaffected. |
| A3 | Existing `test_portfolio.py` will pass unchanged after extracting `execute_trade_on_conn` | Runtime State Inventory | MEDIUM — mitigated by running the portfolio test suite as a verification gate in the plan |
| A4 | Chat watchlist add should be DB-only (matching current manual-add behavior, no market-source start) | Pitfall 6 | LOW — consistent with existing behavior; documented as a planner decision |

**If a real OpenRouter call is desired in any test:** it requires a live `OPENROUTER_API_KEY` and network. All automated tests for this phase should use `LLM_MOCK=true`.

## Open Questions

1. **Should chat-added watchlist tickers start streaming live prices?**
   - What we know: Current manual `POST /api/watchlist` (watchlist.py:72-98) is DB-only and does NOT call `market_source.add_ticker()`. So AI-added tickers would show `price: null` until the source is restarted — same as manual adds today.
   - What's unclear: Whether parity (DB-only) is acceptable, or whether the AI should also start the ticker.
   - Recommendation: Match existing behavior (DB-only) to stay in scope. If live prices for new tickers are wanted, file it as a follow-up touching both the manual route and chat — out of CHAT-04 scope.

2. **Where does the `execute_trade_on_conn` / `apply_watchlist_change_on_conn` helper live?**
   - What we know: Cleanest is to add the helper to `portfolio.py` / `watchlist.py` and have both the HTTP route and chat import it.
   - Recommendation: Put trade helper in `portfolio.py` (refactor `execute_trade` to call it); put watchlist helper in `watchlist.py`. `chat.py` imports both. Avoids circular imports (chat depends on portfolio/watchlist, not vice versa).

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| litellm | Real LLM call | ✓ | 1.87.1 (locked + importable) | — |
| pydantic | Models / structured output | ✓ | v2 (via fastapi) | — |
| OPENROUTER_API_KEY | Real (non-mock) chat | ⚠ runtime-dependent | — | `LLM_MOCK=true` for tests/dev (CHAT-06) |
| OpenRouter / Cerebras network | Real chat | ⚠ runtime | — | `LLM_MOCK=true` |

**Missing dependencies with no fallback:** none.
**Missing dependencies with fallback:** Live LLM access is not needed for development or automated tests — `LLM_MOCK=true` provides a complete deterministic path (CHAT-06). The MEMORY note flags the OpenRouter key as pending rotation; this does not block this phase since tests use mock mode.

## Validation Architecture

> `workflow.nyquist_validation: true` in config — section required.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.3+ with pytest-asyncio (`asyncio_mode = "auto"`) |
| Config file | `backend/pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `cd backend && uv run --extra dev pytest tests/test_chat.py -x` |
| Full suite command | `cd backend && uv run --extra dev pytest -v` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CHAT-01 | `POST /api/chat` returns structured JSON `{message, trades, watchlist_changes}` (mock mode) | integration | `pytest tests/test_chat.py::TestChat::test_chat_returns_structured_response -x` | ❌ Wave 0 |
| CHAT-02 | Response shape has `message`, optional `trades`, optional `watchlist_changes` | integration | `pytest tests/test_chat.py::TestChat::test_response_schema_shape -x` | ❌ Wave 0 |
| CHAT-03 | Mock trade auto-executes: cash drops, AAPL position appears; failed trade returns error outcome | integration | `pytest tests/test_chat.py::TestChat::test_mock_trade_executes -x` | ❌ Wave 0 |
| CHAT-03 | LLM trade with insufficient cash returns `{status: failed, error}` in outcomes, NOT a 500 | integration | `pytest tests/test_chat.py::TestChat::test_failed_trade_in_outcomes -x` | ❌ Wave 0 |
| CHAT-04 | Mock watchlist add: PYPL appears in `GET /api/watchlist` | integration | `pytest tests/test_chat.py::TestChat::test_mock_watchlist_add -x` | ❌ Wave 0 |
| CHAT-05 | Both user + assistant rows persisted; assistant row has `actions` JSON with outcomes | integration | `pytest tests/test_chat.py::TestChat::test_messages_persisted -x` | ❌ Wave 0 |
| CHAT-05 | History loaded on subsequent request (assert prior message influences messages[] — via DB row count) | integration | `pytest tests/test_chat.py::TestChat::test_history_loaded -x` | ❌ Wave 0 |
| CHAT-06 | `LLM_MOCK=true` returns deterministic response without network (no key needed) | integration | `pytest tests/test_chat.py::TestChat::test_mock_mode_deterministic -x` | ❌ Wave 0 |
| (regression) | `test_portfolio.py` still green after `execute_trade_on_conn` refactor | integration | `pytest tests/test_portfolio.py -x` | ✅ exists |

### Sampling Rate
- **Per task commit:** `cd backend && uv run --extra dev pytest tests/test_chat.py -x`
- **Per wave merge:** `cd backend && uv run --extra dev pytest -v` (full suite — catches portfolio/watchlist regressions from the refactor)
- **Phase gate:** Full suite green + `ruff check app/ tests/` before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `backend/tests/test_chat.py` — covers CHAT-01 through CHAT-06; all tests set `LLM_MOCK=true` via `monkeypatch.setenv`
- [ ] `backend/tests/conftest.py` — extend `app_client` fixture (or add a `chat_client` fixture) to register `create_chat_router(price_cache, market_source, db_path)`. Note: the fixture must supply a `market_source` (or `None`) matching the chat factory signature; if chat watchlist add is DB-only it may not need the source — confirm factory signature in plan.
- [ ] No framework install needed — pytest + pytest-asyncio already configured.

## Security Domain

> `security_enforcement` not present in config → treat as enabled. AI-integration phase.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Single-user, no auth by design (PLAN.md) |
| V3 Session Management | no | No sessions |
| V4 Access Control | no | Single hardcoded `user_id="default"` |
| V5 Input Validation | yes | Pydantic `ChatRequest`/`ChatResponse` validate shapes; normalize ticker `strip().upper()`; trade validation (cash/shares/positive qty) in `execute_trade_on_conn`; LLM output is validated via `response_format` + `model_validate_json` |
| V6 Cryptography | no | No crypto; `OPENROUTER_API_KEY` handled by litellm from env, never logged |
| V14 Configuration | yes | `OPENROUTER_API_KEY` from `.env` (gitignored, INFRA-06); never echo key in logs or responses |

### Known Threat Patterns for FastAPI + LLM auto-execution

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Prompt-injection causes unintended trade | Tampering | Stakes are fake money (simulated); trades still pass full cash/share validation. Failures returned, not silently dropped. Acceptable per PLAN.md §9 (deliberate agentic design). |
| SQL injection via LLM-supplied ticker | Tampering | All DB access uses parameterized queries (existing pattern); ticker normalized + length-checked. Never string-format SQL. |
| API key leakage in logs/responses | Info Disclosure | litellm reads key from env; do not log request `messages` at INFO that could include key; never return key in error bodies. Error response is generic `{"error": "LLM unavailable"}`. |
| Event-loop DoS from blocking LLM call | DoS | `asyncio.to_thread` keeps the server responsive during inference. |
| Unbounded history / context growth | DoS | 20-message cap (D-04) bounds prompt size. |
| Malformed LLM JSON crashes route | DoS | `model_validate_json` failure → caught → HTTP 500 `{"error": "LLM unavailable"}` (no retry, per discretion). |

## Sources

### Primary (HIGH confidence)
- `.claude/skills/cerebras/SKILL.md` — exact LiteLLM call: `MODEL`, `EXTRA_BODY`, `reasoning_effort`, `response_format=Model`, `Model.model_validate_json` [CITED]
- `backend/app/routes/portfolio.py` (lines 121-233) — trade execution logic, JSONResponse 400 error pattern, snapshot recording [VERIFIED]
- `backend/app/routes/watchlist.py` (lines 72-119) — add/remove SQL (`INSERT OR IGNORE`, `DELETE`), DB-only behavior [VERIFIED]
- `backend/app/market/massive_client.py` (line 97) — `await asyncio.to_thread(blocking)` established pattern [VERIFIED]
- `backend/app/db/schema.sql` (lines 51-59) — `chat_messages` table already present [VERIFIED]
- `backend/app/db/connection.py` — `get_conn(db_path)`, `sqlite3.Row` factory [VERIFIED]
- `backend/app/main.py` (lines 48-90) — lifespan router registration via factory pattern [VERIFIED]
- `backend/tests/conftest.py` + `tests/test_portfolio.py` — test fixture + integration test conventions [VERIFIED]
- `backend/pyproject.toml` + `uv.lock` — litellm 1.87.1 locked; pytest-asyncio auto-mode [VERIFIED]
- `planning/PLAN.md §9` — LLM integration spec: structured schema, auto-execution, mock mode, system prompt [CITED]

### Secondary (MEDIUM confidence)
- litellm 1.87.1 import verified via `uv run python -c "from litellm import completion"` [VERIFIED]

### Tertiary (LOW confidence)
- None — all claims grounded in codebase files or the project skill.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — litellm already locked/installed; SKILL.md gives exact call
- Architecture: HIGH — reuses verified portfolio/watchlist/lifespan patterns
- Pitfalls: HIGH — derived directly from inspecting existing code (to_thread, JSONResponse, DB-only watchlist)
- Structured-output runtime behavior with Cerebras: MEDIUM (A2) — mitigated entirely by mock-mode testing

**Research date:** 2026-06-05
**Valid until:** 2026-07-05 (stable; litellm pinned, codebase patterns fixed)
