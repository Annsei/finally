# External Integrations

**Analysis Date:** 2026-06-04

## APIs & External Services

**LLM / AI Inference:**
- OpenRouter — API gateway routing to Cerebras inference provider
  - SDK/Client: `litellm` (Python package; must be added via `uv add litellm pydantic`)
  - Model: `openrouter/openai/gpt-oss-120b`
  - Provider override: `{"provider": {"order": ["cerebras"]}}` in `extra_body`
  - Reasoning: `reasoning_effort="low"`
  - Structured outputs: `response_format=MyBaseModelSubclass` (Pydantic model)
  - Auth: `OPENROUTER_API_KEY` env var (required)
  - Skill reference: `.claude/skills/cerebras/SKILL.md`
  - Implementation location: `backend/app/` (planned — not yet implemented)

**Market Data:**
- Massive (Polygon.io wrapper) — real-time US stock price data
  - SDK/Client: `massive>=1.0.0` Python package; uses `RESTClient` and `SnapshotMarketType`
  - Implementation: `backend/app/market/massive_client.py` (`MassiveDataSource` class)
  - Auth: `MASSIVE_API_KEY` env var (optional)
  - Polling: `GET /v2/snapshot/locale/us/markets/stocks/tickers` — all watched tickers in one call
  - Rate limits: free tier 5 req/min → 15s default poll interval; paid tiers 2-5s
  - Fallback: if `MASSIVE_API_KEY` absent or empty, `SimulatorDataSource` is used instead
  - Factory: `backend/app/market/factory.py` (`create_market_data_source()`)

## Data Storage

**Databases:**
- SQLite — primary data store; single file, no server required
  - File location: `db/finally.db` (gitignored; created at runtime)
  - Connection: Python stdlib `sqlite3` module; no ORM
  - Initialization: lazy — backend creates schema and seeds data on first request if file missing
  - Schema location: `backend/db/` (planned)
  - Volume mount: `/app/db` inside Docker container → named volume `finally-data`
  - Tables planned: `users_profile`, `watchlist`, `positions`, `trades`, `portfolio_snapshots`, `chat_messages`

**File Storage:**
- Local filesystem only (SQLite file on Docker volume)

**Caching:**
- In-memory `PriceCache` — thread-safe dict of `PriceUpdate` dataclasses (`backend/app/market/cache.py`)
  - Not persisted across restarts
  - Version counter for SSE change detection
  - Single instance shared across the application

## Authentication & Identity

**Auth Provider:**
- None — no user authentication
- Single hardcoded user: `user_id = "default"` in all database tables
- All database schemas include `user_id` column for future multi-user migration

## Monitoring & Observability

**Error Tracking:**
- None — no external error tracking service configured

**Logs:**
- Python `logging` module used throughout backend
  - `backend/app/market/massive_client.py` — logs poll results, errors, ticker changes
  - `backend/app/market/simulator.py` — logs random shock events, start/stop
  - `backend/app/market/stream.py` — logs SSE client connect/disconnect
  - Log levels: `INFO` for lifecycle events, `DEBUG` for per-tick data, `ERROR` for failures
  - No structured logging format configured; defaults to Python's standard formatter

## CI/CD & Deployment

**Hosting:**
- Docker container — single container on port 8000
- Target platforms: AWS App Runner, Render, or any container platform
- Terraform for App Runner noted as stretch goal in `deploy/` (not yet created)

**CI Pipeline:**
- GitHub Actions (`.github/` directory exists; workflow files not examined)
- Test command: `uv run --extra dev pytest -v`

## Environment Configuration

**Required env vars:**
- `OPENROUTER_API_KEY` — OpenRouter API key; LLM chat is non-functional without it

**Optional env vars:**
- `MASSIVE_API_KEY` — Polygon.io market data; omit to use built-in GBM simulator
- `LLM_MOCK` — set `true` for deterministic mock LLM responses during tests

**Secrets location:**
- `.env` file at project root (gitignored)
- Mounted into Docker container via `--env-file .env` flag
- `.env.example` referenced in README as template (not yet committed to repo)

## Real-Time Streaming

**SSE Endpoint:**
- `GET /api/stream/prices` — server-sent events stream
  - Implementation: `backend/app/market/stream.py` (`create_stream_router()` factory)
  - Media type: `text/event-stream`
  - Push interval: ~500ms (version-based change detection)
  - Event format: `data: {"AAPL": {ticker, price, previous_price, timestamp, change, change_percent, direction}, ...}`
  - Reconnect directive: `retry: 1000` (1 second)
  - Client: native browser `EventSource` API (no WebSocket)
  - Headers: `X-Accel-Buffering: no` to disable nginx proxy buffering

## Webhooks & Callbacks

**Incoming:**
- None

**Outgoing:**
- None

---

*Integration audit: 2026-06-04*
