# FinAlly — AI Trading Workstation

A visually stunning AI-powered trading workstation that streams live market data, simulates portfolio trading, and integrates an LLM chat assistant that can analyze positions and execute trades via natural language.

Built entirely by coding agents as a capstone project for an agentic AI coding course.

## Features

- **Live price streaming** via SSE with green/red flash animations
- **Simulated portfolio** — $10k virtual cash, market orders, instant fills
- **Portfolio visualizations** — heatmap (treemap), P&L chart, positions table
- **AI chat assistant** — analyzes holdings, suggests and auto-executes trades
- **Watchlist management** — track tickers manually or via AI
- **Dark terminal aesthetic** — Bloomberg-inspired, data-dense layout

## Architecture

Single Docker container serving everything on port 8000:

- **Frontend**: Next.js (static export) with TypeScript and Tailwind CSS
- **Backend**: FastAPI (Python/uv) with SSE streaming
- **Database**: SQLite with lazy initialization
- **AI**: LiteLLM → OpenRouter (Cerebras inference) with structured outputs
- **Market data**: Built-in GBM simulator (default) or Massive API (optional)

## Quick Start

```bash
# Clone and configure
cp .env.example .env
# Add your OPENROUTER_API_KEY to .env

# Run with Docker
docker build -t finally .
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally

# Open http://localhost:8000
```

### Quick start with scripts

```bash
# macOS/Linux — builds the image if needed, starts the container, waits for health
./scripts/start_mac.sh --open
./scripts/stop_mac.sh        # stop (data volume preserved)
```

```powershell
# Windows PowerShell
.\scripts\start_windows.ps1 -Open
.\scripts\stop_windows.ps1
```

Or with Docker Compose: `docker compose up --build -d`

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | Yes | OpenRouter API key for AI chat |
| `MASSIVE_API_KEY` | No | Massive (Polygon.io) key for real market data; omit to use simulator |
| `LLM_MOCK` | No | Set `true` for deterministic mock LLM responses (testing) |

## Project Structure

```
finally/
├── frontend/    # Next.js static export
├── backend/     # FastAPI uv project
├── planning/    # Project documentation and agent contracts
├── test/        # Playwright E2E tests + docker-compose.test.yml
├── db/          # SQLite volume mount target (finally.db created at runtime)
└── scripts/     # Start/stop helpers (macOS/Linux + Windows)
```

## Testing

```bash
# Backend unit tests (pytest)
cd backend && uv sync --extra dev && uv run pytest

# Frontend unit tests (Jest)
cd frontend && npm test

# E2E tests (Playwright; runs the app with LLM_MOCK=true and a fresh DB)
cd test && docker compose -f docker-compose.test.yml up --build --abort-on-container-exit
```

See [test/README.md](test/README.md) for running the E2E suite against a local instance.

## Acknowledgements

Charts are rendered with [TradingView Lightweight Charts™](https://www.tradingview.com/lightweight-charts/)
(the in-chart attribution logo is disabled; attribution lives here instead).

## License

See [LICENSE](LICENSE).
