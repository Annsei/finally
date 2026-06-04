<!-- refreshed: 2026-06-04 -->
# Directory Structure

**Analysis Date:** 2026-06-04

## Current vs Planned State

This codebase is partially implemented. The market data backend (`backend/app/market/`) is **complete** and production-quality. The frontend, Docker infrastructure, database layer, REST routes, and LLM integration are **planned but absent**.

## Annotated Directory Tree

```
finally/                              ← Project root
├── backend/                          ← FastAPI uv project (Python) [PARTIAL]
│   ├── app/                          ← Application package
│   │   ├── __init__.py               ← Package marker
│   │   └── market/                   ← Market data subsystem [COMPLETE]
│   │       ├── __init__.py           ← Public exports for the subsystem
│   │       ├── interface.py          ← MarketDataSource ABC
│   │       ├── models.py             ← PriceUpdate frozen dataclass
│   │       ├── cache.py              ← PriceCache (thread-safe in-memory store)
│   │       ├── simulator.py          ← GBMSimulator + SimulatorDataSource
│   │       ├── massive_client.py     ← MassiveDataSource (Polygon.io REST)
│   │       ├── factory.py            ← create_market_data_source() factory
│   │       ├── stream.py             ← create_stream_router() FastAPI SSE router
│   │       └── seed_prices.py        ← Starting prices, GBM params, sector correlations
│   ├── tests/                        ← pytest test suite [COMPLETE for market/]
│   │   ├── __init__.py
│   │   ├── conftest.py               ← Shared fixtures
│   │   └── market/                   ← Tests mirroring app/market/
│   │       ├── __init__.py
│   │       ├── test_cache.py         ← PriceCache unit tests
│   │       ├── test_factory.py       ← Factory env-var switching tests
│   │       ├── test_massive.py       ← MassiveDataSource mock tests
│   │       ├── test_models.py        ← PriceUpdate dataclass tests
│   │       ├── test_simulator.py     ← GBMSimulator unit tests
│   │       ├── test_simulator_source.py ← SimulatorDataSource integration tests
│   │       └── test_stream.py        ← SSE endpoint integration tests
│   ├── market_data_demo.py           ← Rich terminal demo: `uv run market_data_demo.py`
│   ├── pyproject.toml                ← uv project config + dependencies
│   ├── uv.lock                       ← Locked dependency tree
│   ├── README.md                     ← Backend usage notes
│   └── CLAUDE.md                     ← Backend-specific AI instructions
│
├── frontend/                         ← Next.js TypeScript project [NOT YET IMPLEMENTED]
│   └── (planned — static export served by FastAPI)
│
├── db/                               ← Runtime SQLite volume mount target
│   └── .gitkeep                      ← Directory exists in repo; finally.db is gitignored
│
├── scripts/                          ← Docker start/stop scripts [NOT YET IMPLEMENTED]
│   ├── start_mac.sh
│   ├── stop_mac.sh
│   ├── start_windows.ps1
│   └── stop_windows.ps1
│
├── test/                             ← Playwright E2E tests [NOT YET IMPLEMENTED]
│   └── (planned — docker-compose.test.yml + Playwright)
│
├── planning/                         ← Human-readable project docs (PLAN.md etc.)
├── .planning/                        ← GSD machine-readable planning state
│   └── codebase/                     ← This codebase map
├── .claude/                          ← Claude Code configuration (agents, commands, hooks)
├── .github/                          ← GitHub Actions CI workflows
│   └── workflows/
│       ├── claude-code-review.yml
│       └── claude.yml
│
├── Dockerfile                        ← Multi-stage Docker build [NOT YET IMPLEMENTED]
├── docker-compose.yml                ← Dev/optional convenience [NOT YET IMPLEMENTED]
├── .env                              ← Environment variables (gitignored)
├── .gitignore
├── CLAUDE.md                         ← Root-level AI instructions
├── README.md
└── LICENSE
```

## Key Locations

| What | Where |
|------|-------|
| Market data abstract interface | `backend/app/market/interface.py` |
| GBM price simulator | `backend/app/market/simulator.py` |
| Polygon.io REST client | `backend/app/market/massive_client.py` |
| Thread-safe price cache | `backend/app/market/cache.py` |
| SSE FastAPI router | `backend/app/market/stream.py` |
| Factory (simulator vs real) | `backend/app/market/factory.py` |
| Seed prices + GBM config | `backend/app/market/seed_prices.py` |
| Public subsystem exports | `backend/app/market/__init__.py` |
| All market tests | `backend/tests/market/` |
| Python deps + lockfile | `backend/pyproject.toml`, `backend/uv.lock` |
| Project plan | `planning/PLAN.md` |
| Market data summary | `planning/MARKET_DATA_SUMMARY.md` |

## Where to Add New Code

**New API routes** (portfolio, watchlist, chat, health):
- Create `backend/app/routes/` or `backend/app/<domain>.py`
- Register router in the FastAPI app entry point (`backend/app/main.py` — to be created)
- Follow the `create_stream_router(deps)` factory pattern from `stream.py`

**New market data sources:**
- Implement `MarketDataSource` ABC from `backend/app/market/interface.py`
- Update `create_market_data_source()` in `backend/app/market/factory.py`
- Add env-var-based selection logic

**Database schema and access:**
- Create `backend/app/db/` with `schema.sql`, `seed.py`, and connection utilities
- Use the lazy-init pattern (check + create on first request)
- SQLite file lives at `db/finally.db` (volume-mounted at runtime)

**Frontend components:**
- Create `frontend/` as a Next.js project (`npx create-next-app`)
- Use `output: 'export'` in `next.config.ts` for static export
- Build output goes to `frontend/out/`; Dockerfile copies it into the backend's static dir

**Tests for new routes:**
- Mirror the source structure: `backend/tests/` parallels `backend/app/`
- Use `httpx.ASGITransport` + FastAPI app for route integration tests (see `test_stream.py`)

## Naming Conventions

| Kind | Convention | Example |
|------|-----------|---------|
| Python files | `snake_case.py` | `massive_client.py`, `seed_prices.py` |
| Python classes | `PascalCase` | `PriceCache`, `GBMSimulator`, `SimulatorDataSource` |
| Python constants | `UPPER_SNAKE_CASE` | `SEED_PRICES`, `DEFAULT_PARAMS`, `CORRELATION_GROUPS` |
| Factory functions | `create_` prefix | `create_market_data_source()`, `create_stream_router()` |
| Private methods | `_` prefix | `_run_loop()`, `_poll_once()`, `_fetch_snapshots()` |
| Test classes | `Test` prefix + `PascalCase` | `TestPriceCache`, `TestGBMSimulator` |
| Test files | `test_<module>.py` | `test_cache.py`, `test_stream.py` |

## Special Directories

| Directory | Purpose |
|-----------|---------|
| `backend/.venv/` | uv-managed virtual environment (gitignored) |
| `backend/.pytest_cache/` | pytest cache (gitignored) |
| `db/` | Runtime SQLite volume mount; `finally.db` is gitignored via `.gitignore` |
| `.planning/codebase/` | GSD codebase map (this document and siblings) |
| `.claude/agents/` | Project-local GSD subagent definitions |
| `.claude/commands/gsd/` | GSD slash command implementations |

---

*Structure analysis: 2026-06-04*
