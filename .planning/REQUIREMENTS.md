# Requirements: FinAlly — AI Trading Workstation

**Defined:** 2026-06-05
**Core Value:** A user opens the app and immediately sees live prices streaming, can trade with one click, and can ask the AI to manage their portfolio — all without any setup, login, or configuration.

## v1 Requirements

### Backend App Layer

- [ ] **BACK-01**: Backend initializes FastAPI app with lifespan that starts/stops market data source and registers all routers
- [ ] **BACK-02**: Backend lazily initializes SQLite database (schema + seed data) on first request if not already initialized
- [ ] **BACK-03**: `GET /api/health` returns 200 with status ok
- [ ] **BACK-04**: `GET /api/portfolio` returns current positions, cash balance, total value, and unrealized P&L per position
- [ ] **BACK-05**: `POST /api/portfolio/trade` executes market buy/sell order: validates cash (buy) or shares (sell), updates positions and trades table, records portfolio snapshot
- [ ] **BACK-06**: `GET /api/portfolio/history` returns portfolio value snapshots over time for the P&L chart
- [ ] **BACK-07**: `GET /api/watchlist` returns current watchlist tickers with latest prices from price cache
- [ ] **BACK-08**: `POST /api/watchlist` adds a ticker to the watchlist
- [ ] **BACK-09**: `DELETE /api/watchlist/{ticker}` removes a ticker from the watchlist
- [ ] **BACK-10**: Background task records portfolio value snapshot to database every 30 seconds
- [ ] **BACK-11**: FastAPI serves Next.js static export files from the static/ directory on all non-API routes

### LLM Chat

- [ ] **CHAT-01**: `POST /api/chat` sends user message to LLM with current portfolio context and conversation history, returns structured JSON response
- [ ] **CHAT-02**: LLM response schema includes `message`, optional `trades` array, and optional `watchlist_changes` array
- [ ] **CHAT-03**: Backend auto-executes any trades specified in LLM response (same validation as manual trades)
- [ ] **CHAT-04**: Backend auto-applies watchlist changes specified in LLM response
- [ ] **CHAT-05**: Chat messages (user and assistant) stored in `chat_messages` table with executed actions recorded
- [ ] **CHAT-06**: When `LLM_MOCK=true`, backend returns deterministic mock responses instead of calling OpenRouter

### Frontend — Core Layout

- [ ] **FE-01**: Next.js TypeScript project configured with static export (`output: 'export'`), served by FastAPI
- [ ] **FE-02**: Header shows live portfolio total value (updating from SSE), connection status indicator (green/yellow/red dot), and cash balance
- [ ] **FE-03**: Dark terminal theme with backgrounds `#0d1117`/`#1a1a2e`, accent yellow `#ecad0a`, blue `#209dd7`, purple `#753991`
- [ ] **FE-04**: App uses native `EventSource` to connect to `/api/stream/prices` SSE endpoint

### Frontend — Watchlist Panel

- [ ] **FE-05**: Watchlist panel shows all watched tickers with current price, daily change %, and sparkline mini-chart
- [ ] **FE-06**: Prices flash green (uptick) or red (downtick) for ~500ms via CSS transition on each price update
- [ ] **FE-07**: Sparklines accumulate price history from SSE since page load (fill in progressively)
- [ ] **FE-08**: Clicking a ticker in the watchlist selects it for the main chart area

### Frontend — Charts & Portfolio

- [ ] **FE-09**: Main chart area shows a larger price-over-time chart for the selected ticker
- [ ] **FE-10**: Portfolio heatmap (treemap) shows positions sized by portfolio weight, colored by P&L (green = profit, red = loss)
- [ ] **FE-11**: P&L chart shows total portfolio value over time using data from `GET /api/portfolio/history`
- [ ] **FE-12**: Positions table shows ticker, quantity, avg cost, current price, unrealized P&L, and % change

### Frontend — Trading & Chat

- [ ] **FE-13**: Trade bar has ticker input, quantity input, buy button, and sell button; executes market orders instantly via `POST /api/portfolio/trade`
- [ ] **FE-14**: AI chat panel has message input, scrolling conversation history, and loading indicator while waiting for LLM response
- [ ] **FE-15**: Chat panel shows inline confirmations for trades executed and watchlist changes made by the AI

### Infrastructure

- [ ] **INFRA-01**: Multi-stage Dockerfile: Stage 1 builds Next.js static export (Node 20), Stage 2 serves FastAPI + static (Python 3.12)
- [ ] **INFRA-02**: `docker-compose.yml` convenience wrapper for running the container
- [ ] **INFRA-03**: `scripts/start_mac.sh` builds (if needed) and runs the Docker container with volume, port, and env file
- [ ] **INFRA-04**: `scripts/stop_mac.sh` stops and removes the container (preserves data volume)
- [ ] **INFRA-05**: Windows PowerShell equivalents: `scripts/start_windows.ps1` and `scripts/stop_windows.ps1`
- [ ] **INFRA-06**: `.env.example` committed with documented variables; `.env` gitignored
- [ ] **INFRA-07**: Playwright E2E test suite in `test/` with `docker-compose.test.yml` covering key user flows with `LLM_MOCK=true`

## v2 Requirements

### Advanced Trading Features

- **TRADE-01**: Limit order support
- **TRADE-02**: Stop-loss orders
- **TRADE-03**: Order history with status tracking

### Multi-User

- **USER-01**: Authentication (login/signup)
- **USER-02**: Per-user portfolios and watchlists

## Out of Scope

| Feature | Reason |
|---------|--------|
| User authentication / login | No multi-user needed; single user per container |
| WebSocket bidirectional comms | SSE one-way push is sufficient |
| Limit / stop orders | Market orders only — eliminates order book complexity |
| Real brokerage integration | Simulated portfolio only |
| Mobile-native app | Desktop-first responsive web |
| Cloud deployment Terraform | Stretch goal, not in core build |
| Video / advanced media | Text + charts only |
| Sparkline history across sessions | Ephemeral by design — fills from page load |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| BACK-01 through BACK-11 | Phase 1 | Pending |
| CHAT-01 through CHAT-06 | Phase 2 | Pending |
| FE-01 through FE-04 | Phase 3 | Pending |
| FE-05 through FE-08 | Phase 3 | Pending |
| FE-09 through FE-12 | Phase 4 | Pending |
| FE-13 through FE-15 | Phase 4 | Pending |
| INFRA-01 through INFRA-07 | Phase 5 | Pending |

**Coverage:**
- v1 requirements: 37 total
- Mapped to phases: 37
- Unmapped: 0 ✓

---
*Requirements defined: 2026-06-05*
*Last updated: 2026-06-05 after initial definition*
