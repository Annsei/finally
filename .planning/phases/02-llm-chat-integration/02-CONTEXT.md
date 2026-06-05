# Phase 2: LLM Chat Integration - Context

**Gathered:** 2026-06-05
**Status:** Ready for planning

<domain>
## Phase Boundary

Implement `POST /api/chat` — the AI chat endpoint. On each request: assemble portfolio context (cash, positions with live P&L, watchlist ticker list), load last 20 messages from `chat_messages`, call LiteLLM → OpenRouter → Cerebras with structured output, auto-execute any trades and watchlist changes in the response, persist the exchange to `chat_messages`, and return the full JSON response. When `LLM_MOCK=true`, skip the LLM call entirely and return a deterministic response that exercises the full auto-execution pipeline.

</domain>

<decisions>
## Implementation Decisions

### Portfolio Context in System Prompt
- **D-01:** Inject core portfolio data only — cash balance, total portfolio value, positions (ticker, qty, avg cost, current price, unrealized P&L, P&L%), and watchlist ticker list (names only, not prices)
- **D-02:** Portfolio context format: compact text block injected into the system message at the start of each request, assembled fresh from PriceCache + DB at request time
- **D-03:** System prompt persona: "FinAlly, an AI trading assistant" — concise, data-driven, executes trades when asked

### Conversation History
- **D-04:** Load last 20 messages from `chat_messages` table (10 user+assistant exchanges), ordered ascending by `created_at` so the LLM sees chronological context
- **D-05:** History is per-user (`user_id="default"`); no truncation strategy needed beyond the 20-message cap

### Mock Mode
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

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### LLM Integration
- `planning/PLAN.md §9` — LLM integration: how it works, structured output schema, auto-execution, mock mode, system prompt guidance
- `.claude/skills/cerebras/SKILL.md` — cerebras-inference skill: exact imports, MODEL constant, EXTRA_BODY, how to call for structured outputs via Pydantic

### API Contract
- `planning/PLAN.md §8` — API endpoints table: `POST /api/chat` spec
- `planning/PLAN.md §7` — Database schema: `chat_messages` table definition (id, user_id, role, content, actions JSON, created_at)

### Project Requirements
- `.planning/REQUIREMENTS.md` — CHAT-01 through CHAT-06: the 6 requirements this phase must satisfy
- `.planning/ROADMAP.md §Phase 2` — success criteria and key constraints

### Existing Code Patterns
- `backend/app/routes/portfolio.py` — reference implementation: route factory, Pydantic models, JSONResponse error pattern, DB access
- `backend/app/routes/watchlist.py` — reference for watchlist mutation pattern (add/remove) — reuse for watchlist_changes auto-execution
- `backend/app/db/connection.py` — `get_conn(db_path)` and `init_db(db_path)` — DB access pattern
- `backend/app/main.py` — lifespan pattern: how to register the chat router

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `PriceCache.get(ticker)` → `PriceUpdate | None` — use to get current price per position for P&L calculation in context assembly
- `PriceCache.get_all()` — not needed for core context (user chose core-only, not watchlist prices)
- `get_conn(db_path)` — per-request SQLite connection, `row_factory=sqlite3.Row`
- Portfolio P&L calculation logic in `backend/app/routes/portfolio.py` — reuse or extract helper for context assembly
- Watchlist add/remove SQL patterns in `backend/app/routes/watchlist.py` — copy for auto-executing `watchlist_changes`
- Trade execution SQL logic in `backend/app/routes/portfolio.py` (weighted avg cost upsert) — copy for auto-executing `trades`

### Established Patterns
- `create_chat_router(price_cache, db_path) -> APIRouter` — follow factory pattern from portfolio/watchlist routes
- `asyncio.to_thread()` for blocking calls — established in `MassiveDataSource._poll_once`; use the same pattern for `litellm.completion`
- `JSONResponse(status_code=400, content={"error": "..."})` — NOT HTTPException for error responses
- `from __future__ import annotations` + full type annotations on all signatures
- Pydantic models for request/response bodies (already used in portfolio/watchlist routes)
- `logger = logging.getLogger(__name__)` per-module logger

### Integration Points
- `main.py` lifespan: add `from app.routes.chat import create_chat_router` and `app.include_router(create_chat_router(price_cache, db_path))`
- Trade auto-execution in chat must call the same underlying logic as `POST /api/portfolio/trade` (not the HTTP endpoint — the DB logic directly) to avoid circular HTTP calls
- Watchlist auto-execution: same SQL INSERT/DELETE as watchlist router; must also call `price_cache` add/remove if relevant (check existing watchlist route for whether it updates the price source)

</code_context>

<specifics>
## Specific Ideas

- System prompt format (from user selection): compact text block with cash, total value, positions table (ticker | qty | avg_cost | current_price | pnl | pnl_pct), and watchlist as comma-separated list
- Mock response exactly as designed: AAPL buy 5 shares + PYPL watchlist add — this exercises both auto-execution paths in one shot
- `chat_messages.actions` field stores the outcome: `{"trades": [{"ticker": "AAPL", "side": "buy", "quantity": 5, "price": 187.50, "status": "executed"}], "watchlist_changes": [{"ticker": "PYPL", "action": "add", "status": "added"}]}`

</specifics>

<deferred>
## Deferred Ideas

- None — discussion stayed within phase scope

</deferred>

---

*Phase: 2-LLM Chat Integration*
*Context gathered: 2026-06-05*
