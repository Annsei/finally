# FinAlly — AI Trading Workstation

## Current Milestone: v1.0 — Complete Trading Workstation

**Goal:** Build the complete FinAlly trading workstation on top of the existing market data subsystem — backend API layer, SQLite database, Next.js frontend, LLM chat integration, and Docker deployment.

**Target features:**
- Backend app layer: FastAPI main.py, SQLite lazy-init, all REST API endpoints, portfolio snapshot background task
- LLM chat: structured output via LiteLLM/OpenRouter/Cerebras, auto-execute trades and watchlist changes, mock mode for tests
- Frontend: Next.js static export with streaming watchlist, sparklines, charts, portfolio heatmap, positions table, trade bar, AI chat panel
- Infrastructure: Multi-stage Dockerfile, start/stop scripts, Playwright E2E tests

---

## What This Is

FinAlly (Finance Ally) is a visually stunning AI-powered trading workstation built as a capstone project for an agentic AI coding course. It streams live market data, lets users trade a simulated $10,000 portfolio via market orders, and integrates an LLM chat assistant (powered by Cerebras/OpenRouter) that can analyze positions and execute trades on the user's behalf. It runs as a single Docker container — one command to start, a browser to use.

The project is built entirely by AI coding agents, demonstrating how orchestrated agents can produce a production-quality full-stack application. Agents interact through files in `planning/`.

## Core Value

A user opens the app and immediately sees live prices streaming, can trade with one click, and can ask the AI to manage their portfolio — all without any setup, login, or configuration.

## Requirements

### Validated

These are implemented and passing in the current codebase (`backend/app/market/`):

- ✓ Live price streaming via SSE from `GET /api/stream/prices` — market data subsystem complete
- ✓ GBM price simulator with realistic seed prices, correlated sector moves, and random events — `simulator.py`
- ✓ Polygon.io REST API client (optional, env-var driven, polls every 15s free tier) — `massive_client.py`
- ✓ Thread-safe in-memory price cache (`PriceCache`) shared across producers and consumers — `cache.py`
- ✓ Abstract `MarketDataSource` interface (Strategy pattern) — both implementations conform — `interface.py`
- ✓ Factory-based source selection via `MASSIVE_API_KEY` env var — `factory.py`
- ✓ 75+ unit + integration tests passing; market data subsystem fully covered — `backend/tests/market/`

### Active

These are specified in `planning/PLAN.md` and remain to be built:

**Backend — App Layer**
- [ ] FastAPI application entry point (`backend/app/main.py`) with lifespan: PriceCache init, market source start/stop, router registration, static file serving
- [ ] SQLite database at `db/finally.db` with lazy initialization — schema: users_profile, watchlist, positions, trades, portfolio_snapshots, chat_messages
- [ ] `GET /api/health` — health check endpoint
- [ ] `GET /api/portfolio` — current positions, cash, total value, unrealized P&L
- [ ] `POST /api/portfolio/trade` — execute market order (buy/sell), validate cash/shares, update positions + trades, snapshot portfolio
- [ ] `GET /api/portfolio/history` — portfolio value snapshots for P&L chart
- [ ] `GET /api/watchlist` — current tickers with latest prices
- [ ] `POST /api/watchlist` — add ticker
- [ ] `DELETE /api/watchlist/{ticker}` — remove ticker
- [ ] `POST /api/chat` — LLM chat with structured output (message + trades + watchlist_changes), auto-execute actions
- [ ] Background task: portfolio snapshot every 30 seconds
- [ ] LLM mock mode (`LLM_MOCK=true`) for deterministic testing

**Frontend**
- [ ] Next.js TypeScript project with static export (`output: 'export'`)
- [ ] SSE consumer via native `EventSource` to `/api/stream/prices`
- [ ] Watchlist panel: ticker, price (flash green/red on change), daily change %, sparkline mini-chart
- [ ] Main chart area: larger chart for selected ticker (price over time)
- [ ] Portfolio heatmap: treemap sized by weight, colored by P&L
- [ ] P&L chart: total portfolio value over time from `portfolio_snapshots`
- [ ] Positions table: ticker, quantity, avg cost, current price, unrealized P&L, % change
- [ ] Trade bar: ticker + quantity inputs, buy/sell buttons, instant fill
- [ ] AI chat panel: message input, scrolling history, loading indicator, inline action confirmations
- [ ] Header: live portfolio total value, connection status dot (green/yellow/red), cash balance
- [ ] Dark theme: `#0d1117` / `#1a1a2e` backgrounds, accent yellow `#ecad0a`, blue `#209dd7`, purple `#753991`
- [ ] Price flash animation: brief background highlight on price change, fades ~500ms

**Infrastructure**
- [ ] Multi-stage Dockerfile: Node 20 (build Next.js) → Python 3.12 (serve FastAPI + static)
- [ ] `docker-compose.yml` convenience wrapper
- [ ] `scripts/start_mac.sh` and `scripts/stop_mac.sh` (and Windows PowerShell equivalents)
- [ ] `.env.example` committed; `.env` gitignored
- [ ] Playwright E2E test suite in `test/` with `docker-compose.test.yml`

### Out of Scope

- User authentication / login / signup — hardcoded single user (`user_id="default"`)
- OAuth or social login — email/password sufficient; actually no login at all for v1
- WebSocket real-time bidirectional communication — SSE is sufficient for one-way push
- Limit orders, stop orders, partial fills — market orders only, instant fill
- Real P&L tracking across sessions (only from page load) for sparklines — ephemeral by design
- Real money / real brokerage integration — simulated portfolio only
- Mobile-native app — desktop-first responsive web
- Multi-user support — single user per container instance (schema supports it for future)
- Cloud deployment Terraform — stretch goal only, not in core build
- Video or advanced media — text + charts only

## Context

- **Capstone course:** This is the final project for an agentic AI coding course. The demo value (AI agents building a professional trading app) is as important as the functionality itself.
- **Market data complete:** `backend/app/market/` is production-quality with 75+ tests. The foundation is solid.
- **Tech decisions locked:** FastAPI, Next.js static export, SQLite, SSE, LiteLLM/OpenRouter/Cerebras, single Docker container — all decided and documented in `planning/PLAN.md § 3`.
- **OpenRouter API key:** Present in `.env` (gitignored). Cerebras inference via `openrouter/openai/gpt-oss-120b` model. Use LiteLLM with structured outputs.
- **Database lazy-init:** Backend initializes SQLite on first request — no migration step, no separate setup.
- **SSE client reconnect:** Browser `EventSource` handles reconnection automatically; server doesn't need to track clients.

## Constraints

- **Tech stack**: FastAPI + Next.js (static export) + SQLite + SSE — no changes; single container, single port 8000
- **Single user**: No auth layer; `user_id="default"` hardcoded throughout
- **Market orders only**: No order book, no limit orders — eliminates complexity
- **LLM**: LiteLLM → OpenRouter → Cerebras (`openrouter/openai/gpt-oss-120b`), structured JSON output required
- **Docker**: Single container, single port, volume-mounted SQLite — must work with `docker run` one-liner
- **Dependencies**: Python managed by `uv`; frontend by npm; no additional services (no Redis, no Postgres, no message queue)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| SSE over WebSockets | One-way push is all we need; simpler, no bidirectional complexity | — Pending |
| Static Next.js export | Single origin, no CORS, one port, one container | — Pending |
| SQLite over Postgres | No auth = no multi-user = no database server needed | — Pending |
| Single Docker container | Students run one command; no service orchestration | — Pending |
| uv for Python | Fast, reproducible lockfile; what students should learn | ✓ Good (already in use) |
| Market orders only | Eliminates order book, limit order logic, partial fills | — Pending |
| LiteLLM + OpenRouter | Model-agnostic, Cerebras for fast inference, structured outputs | — Pending |
| Cerebras inference | Fast enough for non-streaming chat (loading indicator sufficient) | — Pending |
| GBM simulator default | No API key required for demo; realistic enough for course purposes | ✓ Good (implemented) |
| Strategy pattern for market data | Swap simulator ↔ real data via env var, zero downstream changes | ✓ Good (implemented) |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd:transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-06-05 after milestone v1.0 initialization*
