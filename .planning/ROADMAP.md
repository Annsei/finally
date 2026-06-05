---
milestone: v1.0
name: Complete Trading Workstation
phase_count: 5
start_phase: 1
---

# Roadmap: FinAlly v1.0 — Complete Trading Workstation

**5 phases** | **37 requirements mapped** | All covered ✓

## Phase Overview

| # | Phase | Goal | Requirements | Success Criteria |
|---|-------|------|--------------|-----------------|
| 1 | Backend App Layer | FastAPI app, SQLite DB, all API endpoints working | BACK-01–11 | 4 |
| 2 | LLM Chat Integration | 3/3 | Complete   | 2026-06-05 |
| 3 | Frontend Foundation | Next.js project, SSE connection, watchlist panel, sparklines | FE-01–08 | 4 |
| 4 | Frontend Portfolio & Trading | Charts, heatmap, P&L chart, positions table, trade bar, chat UI | FE-09–15 | 4 |
| 5 | Infrastructure & E2E | Dockerfile, scripts, docker-compose, E2E tests | INFRA-01–07 | 4 |

---

## Phase 1: Backend App Layer ✓ COMPLETE (2026-06-05)

**Goal:** Build the complete FastAPI application with SQLite persistence and all REST API endpoints, wiring together the existing market data subsystem.

**Requirements:** BACK-01, BACK-02, BACK-03, BACK-04, BACK-05, BACK-06, BACK-07, BACK-08, BACK-09, BACK-10, BACK-11

**Success Criteria:**

1. ✓ `GET /api/health` returns `{"status": "ok"}` with 200
2. ✓ Fresh SQLite DB is auto-created with seed data (default user, 10 watchlist tickers) on first request
3. ✓ `POST /api/portfolio/trade` correctly validates cash/shares, updates positions, records trade, and snapshots portfolio
4. ✓ All API endpoints return correct responses; background snapshot task fires every 30 seconds

**Plans:** 5/5 complete | **Tests:** 89 passing | **Verification:** passed 2026-06-05

**Dependencies:** `backend/app/market/` (complete) — use PriceCache, MarketDataSource, create_market_data_source, create_stream_router

**Key constraints:**

- Database file at `db/finally.db` (volume mount point for Docker)
- `user_id="default"` hardcoded throughout
- Lazy DB init: check on startup, create if missing
- No auth, no sessions — single-user app
- `uv` manages Python deps in `backend/pyproject.toml`

---

## Phase 2: LLM Chat Integration

**Goal:** Implement the AI chat endpoint using LiteLLM → OpenRouter → Cerebras with structured JSON output, auto-execution of trades and watchlist changes, and deterministic mock mode for testing.

**Requirements:** CHAT-01, CHAT-02, CHAT-03, CHAT-04, CHAT-05, CHAT-06

**Success Criteria:**

1. `POST /api/chat` returns structured JSON with `message`, optional `trades`, optional `watchlist_changes`
2. Trades in the response are auto-executed (same validation path as manual trades) and failures included in response
3. `LLM_MOCK=true` returns deterministic responses without calling OpenRouter
4. Conversation history loaded from `chat_messages` table; new messages persisted with executed actions in `actions` JSON field

**Plans:** 3/3 plans complete

Plans:
**Wave 1**

- [x] 02-01-PLAN.md — Extract execute_trade_on_conn and apply_watchlist_change_on_conn helper functions

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 02-02-PLAN.md — Implement backend/app/routes/chat.py with full POST /api/chat handler

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 02-03-PLAN.md — Register chat router in main.py, add test fixtures, write test_chat.py suite

**Dependencies:** Phase 1 complete (portfolio, watchlist, trade endpoints available)

**Key constraints:**

- LiteLLM with `openrouter/openai/gpt-oss-120b` model via OpenRouter
- Structured outputs (Pydantic schema or JSON schema) for reliable parsing
- System prompt: "FinAlly, an AI trading assistant" with portfolio context injected
- No streaming — full response returned as JSON (Cerebras fast enough)
- OPENROUTER_API_KEY from `.env` file

---

## Phase 3: Frontend Foundation

**Goal:** Bootstrap the Next.js TypeScript project with SSE integration, the watchlist panel with live price flashing and sparklines, and the dark terminal theme.

**Requirements:** FE-01, FE-02, FE-03, FE-04, FE-05, FE-06, FE-07, FE-08

**Success Criteria:**

1. `npm run build` produces static export in `out/`; FastAPI serves it at `/`
2. Prices in watchlist panel flash green/red on each SSE update and fade in ~500ms
3. Sparklines accumulate progressively from SSE stream since page load
4. Header shows live portfolio value, cash balance, and connection status dot (green/yellow/red)

**Dependencies:** Phase 1 complete (SSE stream endpoint available)

**Key constraints:**

- Static export: `output: 'export'` in `next.config.js` (no server-side rendering)
- All API calls to same origin `/api/*` — no CORS needed
- Tailwind CSS for styling; custom dark theme colors defined
- Canvas-based chart library (Lightweight Charts or Recharts) for sparklines
- Price flash: add CSS class on update, remove after 500ms via `setTimeout`

---

## Phase 4: Frontend Portfolio & Trading

**Goal:** Build the portfolio visualization (heatmap treemap + P&L chart), positions table, trade bar, and AI chat panel.

**Requirements:** FE-09, FE-10, FE-11, FE-12, FE-13, FE-14, FE-15

**Success Criteria:**

1. Portfolio heatmap renders as treemap: rectangles sized by weight, green (profit) or red (loss)
2. P&L chart fetches from `GET /api/portfolio/history` and renders total value over time
3. Trade bar executes buy/sell: cash/position updates reflected in UI immediately
4. Chat panel sends messages, shows loading state, displays inline trade/watchlist confirmations

**Dependencies:** Phase 3 complete (Next.js project, SSE, Tailwind, chart library established)

**Key constraints:**

- Treemap: use a library (e.g., react-treemap, d3-hierarchy) or implement with flex layout
- P&L chart: polling `GET /api/portfolio/history` every 30s is acceptable (not SSE)
- Trade bar: optimistic update on submit, reconcile on API response
- Chat panel: no streaming — show loading indicator until full response arrives

---

## Phase 5: Infrastructure & E2E Tests

**Goal:** Build the multi-stage Dockerfile, start/stop scripts for Mac and Windows, docker-compose wrapper, and Playwright E2E test suite.

**Requirements:** INFRA-01, INFRA-02, INFRA-03, INFRA-04, INFRA-05, INFRA-06, INFRA-07

**Success Criteria:**

1. `docker build` succeeds and produces a working container serving the app on port 8000
2. `scripts/start_mac.sh` builds (if needed) and runs container; app accessible at http://localhost:8000
3. Playwright E2E tests pass with `LLM_MOCK=true`: fresh start, watchlist CRUD, buy/sell, AI chat
4. `.env.example` documents all env vars; `.env` is gitignored

**Dependencies:** Phases 1–4 complete (full application working)

**Key constraints:**

- Multi-stage build: Stage 1 Node 20 slim (build Next.js), Stage 2 Python 3.12 slim (uv + FastAPI)
- Frontend build output copied to `backend/static/` in Docker image
- Volume mount: `-v finally-data:/app/db` for SQLite persistence
- E2E tests in `test/` with `docker-compose.test.yml`; Playwright container separate from app container
- Start scripts idempotent (safe to run multiple times)

---

## Requirement Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| BACK-01 | 1 | Complete |
| BACK-02 | 1 | Complete |
| BACK-03 | 1 | Complete |
| BACK-04 | 1 | Complete |
| BACK-05 | 1 | Complete |
| BACK-06 | 1 | Complete |
| BACK-07 | 1 | Complete |
| BACK-08 | 1 | Complete |
| BACK-09 | 1 | Complete |
| BACK-10 | 1 | Complete |
| BACK-11 | 1 | Complete |
| CHAT-01 | 2 | Pending |
| CHAT-02 | 2 | Pending |
| CHAT-03 | 2 | Pending |
| CHAT-04 | 2 | Pending |
| CHAT-05 | 2 | Pending |
| CHAT-06 | 2 | Pending |
| FE-01 | 3 | Pending |
| FE-02 | 3 | Pending |
| FE-03 | 3 | Pending |
| FE-04 | 3 | Pending |
| FE-05 | 3 | Pending |
| FE-06 | 3 | Pending |
| FE-07 | 3 | Pending |
| FE-08 | 3 | Pending |
| FE-09 | 4 | Pending |
| FE-10 | 4 | Pending |
| FE-11 | 4 | Pending |
| FE-12 | 4 | Pending |
| FE-13 | 4 | Pending |
| FE-14 | 4 | Pending |
| FE-15 | 4 | Pending |
| INFRA-01 | 5 | Pending |
| INFRA-02 | 5 | Pending |
| INFRA-03 | 5 | Pending |
| INFRA-04 | 5 | Pending |
| INFRA-05 | 5 | Pending |
| INFRA-06 | 5 | Pending |
| INFRA-07 | 5 | Pending |

**Coverage:**

- v1 requirements: 37 total
- Mapped to phases: 37
- Unmapped: 0 ✓

---
*Roadmap created: 2026-06-05*
*Last updated: 2026-06-05 — Phase 2 planned (3 plans)*
*Milestone: v1.0 — Complete Trading Workstation*
