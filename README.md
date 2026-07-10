# FinAlly — AI Trading Workstation

FinAlly is an AI-assisted paper-trading workstation and classroom arena. It
streams a simulated or Massive-backed market, executes manual and AI-directed
orders, runs declarative strategies and backtests, and lets external bots join
the same leaderboard through guarded API keys.

The project is a modular monolith built as an agentic-AI coding course
capstone. The default configuration is a trusted, loopback-only local demo.

## Current capabilities

- US and A-share runtime profiles with profile-specific currency, lots, fees,
  sessions, T+1 and direction colors;
- live SSE prices, candlestick/volume charts, market events and analytics;
- market, symbol, journal, arena/player, strategy, run-library and developer
  pages;
- market, limit, stop and stop-limit orders with fills, costs and risk rails;
- portfolio history, realized/unrealized P&L and trade journal;
- standing rules, live declarative strategies, templates and persisted
  backtests;
- LLM chat actions, event briefs and deterministic offline mock mode;
- lightweight classroom identities, seasons/leaderboard and external bot API
  keys with ticker/order/daily guardrails and an audit ledger.

## Architecture and supported scale

One Docker image serves the static Next.js frontend and FastAPI backend on port
8000. SQLite stores durable state; market cache, rate limiting and background
evaluators are process-local.

FinAlly supports one process/replica with a persistent volume. It does not yet
support horizontal scaling. Read [planning/CURRENT.md](planning/CURRENT.md) and
[planning/OPERATIONS.md](planning/OPERATIONS.md) before shared deployment.

## Quick start

```bash
cp .env.example .env
# For an offline demo, set LLM_MOCK=true in .env.
# For live chat, set OPENROUTER_API_KEY instead.

docker compose up --build -d
curl --fail http://127.0.0.1:8000/api/ready
```

Open <http://127.0.0.1:8000>. Docker binds only to localhost by default.

Start the isolated A-share service alongside US:

```bash
docker compose --profile cn up --build -d
# US: http://127.0.0.1:8000  CN: http://127.0.0.1:8001
```

The two services use different project-scoped SQLite volumes. Do not switch one
existing volume between market profiles.

### Start scripts

```bash
./scripts/start_mac.sh --open
./scripts/stop_mac.sh
```

```powershell
.\scripts\start_windows.ps1 -Open
.\scripts\stop_windows.ps1
```

The scripts rebuild using Docker's cache, replace containers gracefully, wait
for readiness and fail non-zero when startup is unhealthy. Override
`FINALLY_BIND_HOST` only for a secured classroom server.

## Configuration

`.env.example` is the complete public configuration contract. Important groups
are:

- runtime/security: `FINALLY_RUNTIME_MODE`, `FINALLY_HOST`, server/admin
  secrets, single-replica acknowledgement;
- integrations: `OPENROUTER_API_KEY`, `MASSIVE_API_KEY`, `LLM_MOCK`;
- market mechanics: profile, commission, session timing and quote freshness;
- orchestration: host bind address, US/CN ports and standalone-script names.

Shared `classroom-server` mode requires explicit secrets, persistent storage
and exactly one replica. See [planning/SECURITY.md](planning/SECURITY.md).

## Testing

```bash
# Backend unit/integration tests + coverage
cd backend
uv sync --extra dev
uv run pytest --cov=app

# Frontend unit tests, type/build checks
cd ../frontend
npm ci
npm test -- --watchAll=false
npx tsc --noEmit
npm run lint
npm run build

# Full US and CN browser suites
cd ../test
docker compose -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from playwright
docker compose -f docker-compose.cn.test.yml up --build --abort-on-container-exit --exit-code-from playwright
```

See [test/README.md](test/README.md) for quick smoke subsets and artifacts.

## Documentation map

- [Current state and architecture](planning/CURRENT.md)
- [Operations, backup and restore](planning/OPERATIONS.md)
- [Security boundary](planning/SECURITY.md)
- [API compatibility policy](planning/API.md)
- [Active audit remediation](planning/AUDIT_REMEDIATION_PLAN.md)
- [Examples and bot tutorial](examples/README.md)

`planning/PLAN.md`, `.planning/**` and `planning/archive/**` are historical
design inputs, not the current status source.

## Acknowledgements

Charts use [TradingView Lightweight Charts](https://www.tradingview.com/lightweight-charts/).

## License

See [LICENSE](LICENSE).
