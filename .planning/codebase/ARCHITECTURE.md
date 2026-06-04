<!-- refreshed: 2026-06-04 -->
# Architecture

**Analysis Date:** 2026-06-04

## System Overview

```text
┌─────────────────────────────────────────────────────────────┐
│  Browser Client                                              │
│  Next.js static export (EventSource + fetch /api/*)         │
│  [frontend/ — NOT YET IMPLEMENTED]                          │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTP / SSE (port 8000)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│  FastAPI  (Python / uvicorn)                                 │
│  [backend/ — PARTIALLY IMPLEMENTED]                         │
│                                                              │
│  ├── /api/stream/prices   SSE endpoint                       │
│  │   `backend/app/market/stream.py`                          │
│  │                                                           │
│  ├── /api/*               REST endpoints (planned)           │
│  │   portfolio, watchlist, chat, health                      │
│  │                                                           │
│  └── /*                   Static file serving                │
│       (serves Next.js build output — planned)                │
└──────┬───────────────────────────┬──────────────────────────┘
       │                           │
       ▼                           ▼
┌──────────────────┐    ┌──────────────────────────────────────┐
│  SQLite DB       │    │  Market Data Subsystem               │
│  `db/finally.db` │    │  `backend/app/market/`               │
│  (planned)       │    │                                       │
│                  │    │  MarketDataSource (ABC)               │
│  users_profile   │    │  `interface.py`                       │
│  watchlist       │    │      │                                │
│  positions       │    │      ├── SimulatorDataSource          │
│  trades          │    │      │   `simulator.py`               │
│  portfolio_snap- │    │      │   (GBM + asyncio loop)         │
│    shots         │    │      │                                │
│  chat_messages   │    │      └── MassiveDataSource            │
└──────────────────┘    │          `massive_client.py`          │
                        │          (Polygon.io REST polling)    │
                        │                  │                    │
                        │                  ▼                    │
                        │          PriceCache                   │
                        │          `cache.py`                   │
                        │          (thread-safe in-memory)      │
                        │                  │                    │
                        │          ┌───────┴──────────┐         │
                        │          │                  │         │
                        │          ▼                  ▼         │
                        │    SSE streaming       Portfolio      │
                        │    `stream.py`         valuation      │
                        │                        (planned)      │
                        └──────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| `MarketDataSource` | Abstract interface for price producers | `backend/app/market/interface.py` |
| `GBMSimulator` | Generates correlated price moves via Geometric Brownian Motion | `backend/app/market/simulator.py` |
| `SimulatorDataSource` | Wraps GBMSimulator in asyncio loop, writes to PriceCache | `backend/app/market/simulator.py` |
| `MassiveDataSource` | Polls Polygon.io REST API, writes to PriceCache | `backend/app/market/massive_client.py` |
| `PriceCache` | Thread-safe in-memory store of latest prices; version counter for SSE | `backend/app/market/cache.py` |
| `PriceUpdate` | Immutable frozen dataclass: ticker, price, previous_price, timestamp, change, direction | `backend/app/market/models.py` |
| `create_market_data_source` | Factory: selects SimulatorDataSource or MassiveDataSource via env var | `backend/app/market/factory.py` |
| `create_stream_router` | FastAPI router factory for SSE endpoint at `/api/stream/prices` | `backend/app/market/stream.py` |
| Seed data | Realistic starting prices and GBM params per ticker; correlation groups | `backend/app/market/seed_prices.py` |
| FastAPI app | REST + SSE + static serving on port 8000 | `backend/app/` (entry point planned) |
| Next.js frontend | Static-exported SPA (Bloomberg-style UI) | `frontend/` (NOT YET IMPLEMENTED) |
| SQLite database | Persistent storage: portfolio, trades, watchlist, chat | `db/finally.db` (planned) |

## Pattern Overview

**Overall:** Strategy pattern for market data, combined with Producer-Cache-Consumer for price distribution.

**Key Characteristics:**
- All market data flows through `PriceCache` — producers write, consumers read; no direct coupling between producers and consumers
- `MarketDataSource` ABC enforces the same lifecycle API (`start/stop/add_ticker/remove_ticker/get_tickers`) on both implementations so downstream code is source-agnostic
- Router factories (`create_stream_router`) accept dependencies by parameter rather than module-level globals, enabling isolation in tests
- `PriceCache.version` (monotonic counter) allows the SSE generator to detect changes without locking the full cache per poll cycle
- Asyncio background tasks own the produce side; threading `Lock` guards the cache for sync access from any thread (e.g., portfolio valuation)

## Layers

**Data Models:**
- Purpose: Immutable value objects describing domain state
- Location: `backend/app/market/models.py`
- Contains: `PriceUpdate` frozen dataclass
- Depends on: Python standard library only
- Used by: `PriceCache`, SSE stream, downstream portfolio code (planned)

**Market Data Interface:**
- Purpose: Abstract contract isolating producers from consumers
- Location: `backend/app/market/interface.py`
- Contains: `MarketDataSource` ABC with `start/stop/add_ticker/remove_ticker/get_tickers`
- Depends on: nothing (pure ABC)
- Used by: `factory.py`, `SimulatorDataSource`, `MassiveDataSource`

**Market Data Producers:**
- Purpose: Generate price data and write it to PriceCache on a schedule
- Location: `backend/app/market/simulator.py` (GBM), `backend/app/market/massive_client.py` (Polygon.io)
- Contains: `GBMSimulator` (math core), `SimulatorDataSource` (asyncio wrapper), `MassiveDataSource` (REST poller)
- Depends on: `interface.py`, `cache.py`, `seed_prices.py`, `numpy`, `massive`
- Used by: FastAPI app startup (planned)

**Shared Cache:**
- Purpose: Single point of truth for current prices; decouples producers and consumers
- Location: `backend/app/market/cache.py`
- Contains: `PriceCache` with thread-safe `update/get/get_all/remove/version`
- Depends on: `models.py`, `threading.Lock`
- Used by: all producers, SSE stream, portfolio valuation (planned)

**API Layer:**
- Purpose: HTTP + SSE endpoints for clients
- Location: `backend/app/market/stream.py` (SSE implemented); REST routes planned
- Contains: `create_stream_router` FastAPI router factory
- Depends on: `cache.py`, FastAPI
- Used by: FastAPI application

**Seed / Configuration Data:**
- Purpose: Starting prices, per-ticker GBM parameters, sector correlation groups
- Location: `backend/app/market/seed_prices.py`
- Contains: `SEED_PRICES`, `TICKER_PARAMS`, `DEFAULT_PARAMS`, `CORRELATION_GROUPS`, correlation coefficients
- Depends on: nothing
- Used by: `GBMSimulator`

## Data Flow

### SSE Price Stream (Implemented)

1. FastAPI app creates `PriceCache()` and calls `create_market_data_source(cache)` (`backend/app/market/factory.py`)
2. Factory reads `MASSIVE_API_KEY` env var; returns `SimulatorDataSource` or `MassiveDataSource`
3. App calls `await source.start(tickers)` — starts asyncio background task
4. **Simulator path:** `GBMSimulator.step()` called every 500ms; computes correlated GBM moves using Cholesky decomposition of sector correlation matrix; writes results to `PriceCache.update()` (`backend/app/market/simulator.py`)
5. **Massive path:** `_poll_once()` called every 15s (default); runs `RESTClient.get_snapshot_all()` in thread pool via `asyncio.to_thread`; writes results to `PriceCache.update()` (`backend/app/market/massive_client.py`)
6. Browser connects to `GET /api/stream/prices` — long-lived SSE connection (`backend/app/market/stream.py`)
7. `_generate_events` async generator polls `price_cache.version` every 500ms; on version change, calls `price_cache.get_all()` and yields JSON `data:` event
8. Browser `EventSource` receives events; auto-reconnects with 1000ms retry if disconnected

### Planned Flows (Not Yet Implemented)

**Trade Execution:**
1. Browser POST `/api/portfolio/trade` → validate cash/shares → execute → update `positions` + `trades` tables → snapshot portfolio value → return result

**AI Chat:**
1. Browser POST `/api/chat` → load portfolio context + chat history → call LiteLLM/OpenRouter → parse structured JSON response → auto-execute trades/watchlist changes → store in `chat_messages` → return to client

**State Management:**
- Market prices: in-memory `PriceCache` (fast, ephemeral)
- Portfolio/trades/watchlist/chat: SQLite at `db/finally.db` (persistent, volume-mounted)

## Key Abstractions

**`MarketDataSource` ABC:**
- Purpose: Isolates market data consumers from whether prices come from simulation or real API
- Examples: `backend/app/market/simulator.py` (`SimulatorDataSource`), `backend/app/market/massive_client.py` (`MassiveDataSource`)
- Pattern: Strategy — swap implementations by changing factory return value based on env var

**`PriceCache`:**
- Purpose: Thread-safe shared state between async producers and any consumers (sync or async)
- Examples: `backend/app/market/cache.py`
- Pattern: Shared mutable store with lock + monotonic version counter for efficient change detection

**Router Factories:**
- Purpose: Create FastAPI routers with injected dependencies (no module-level singletons)
- Examples: `create_stream_router(price_cache)` in `backend/app/market/stream.py`
- Pattern: Factory function returning `APIRouter`; enables test isolation

**`PriceUpdate` Dataclass:**
- Purpose: Immutable snapshot of a single price tick, with computed properties for change direction
- Examples: `backend/app/market/models.py`
- Pattern: Frozen dataclass (`slots=True`) with `to_dict()` for JSON serialization

## Entry Points

**Market Data Subsystem:**
- Location: `backend/app/market/__init__.py`
- Triggers: Imported by FastAPI app at startup
- Responsibilities: Exports `PriceUpdate`, `PriceCache`, `MarketDataSource`, `create_market_data_source`, `create_stream_router`

**Demo Script:**
- Location: `backend/market_data_demo.py`
- Triggers: `uv run market_data_demo.py`
- Responsibilities: Runs a Rich terminal dashboard with live GBM prices for 60 seconds

**FastAPI App Entry Point:**
- Location: Planned (`backend/app/main.py` or similar — NOT YET IMPLEMENTED)
- Triggers: `uvicorn app.main:app`
- Responsibilities: Initialize PriceCache, create and start MarketDataSource, register all routers, mount static frontend files, handle lifespan events

## Architectural Constraints

- **Threading model:** Asyncio event loop runs FastAPI + SSE generator + market data background tasks. `PriceCache` uses `threading.Lock` to be safe for synchronous callers (e.g., portfolio valuation code that may not be async). Do not block the event loop — use `asyncio.to_thread()` for blocking I/O (pattern established in `MassiveDataSource._poll_once`).
- **Global state:** `PriceCache` is a singleton created at app startup and passed by reference to all consumers. No module-level price globals.
- **Circular imports:** None detected. Internal imports follow a strict one-direction flow: `models` → `cache` → `interface` → `simulator`/`massive_client` → `factory` → `stream`.
- **Single-user:** All database rows include `user_id` defaulting to `"default"`. No auth layer. Multi-user is a schema-level future option only.
- **Single container, single port:** Both API and static frontend served on port 8000. No CORS needed.
- **No main FastAPI app yet:** Only the `app/market/` subsystem is implemented. The FastAPI app entry point, database layer, portfolio/watchlist/chat routes, and LLM integration are all planned but absent.

## Anti-Patterns

### Blocking the Event Loop

**What happens:** Calling a synchronous blocking function directly in an async context (e.g., `self._client.get_snapshot_all(...)` directly inside an `async def`).
**Why it's wrong:** Blocks the entire asyncio event loop, stalling SSE streams and all other requests.
**Do this instead:** Wrap sync calls with `await asyncio.to_thread(self._fetch_snapshots)` as done in `backend/app/market/massive_client.py:97`.

### Module-Level Singleton Dependencies

**What happens:** Creating `PriceCache()` or `FastAPI()` at module import time and importing them into other modules.
**Why it's wrong:** Makes test isolation impossible — tests can't inject their own cache instances.
**Do this instead:** Use factory functions that accept dependencies as parameters, as done with `create_stream_router(price_cache)` in `backend/app/market/stream.py:18`.

### Accessing Private Simulator State in Tests

**What happens:** Tests accessing `source._sim._prices` or other private attributes directly.
**Why it's wrong:** Couples tests to internal implementation; breaks when internals change.
**Do this instead:** Use the public API — `source.get_tickers()`, `cache.get_price("AAPL")`, etc. Public methods were added (`GBMSimulator.get_tickers()`) specifically to enable this.

## Error Handling

**Strategy:** Log-and-continue in background tasks; don't re-raise exceptions that would kill the loop.

**Patterns:**
- `SimulatorDataSource._run_loop`: catches `Exception`, logs with `logger.exception`, continues — ensures one bad tick doesn't stop the simulator
- `MassiveDataSource._poll_once`: catches `Exception`, logs with `logger.error`, returns without re-raising — network errors and 429s are tolerated; the next poll will retry
- `MassiveDataSource._poll_once` inner loop: catches `AttributeError`/`TypeError` per snapshot with a warning — one malformed snapshot doesn't abort the batch
- Asyncio cancellation: `CancelledError` is caught in `stop()` and in the SSE generator to allow clean shutdown

## Cross-Cutting Concerns

**Logging:** Standard library `logging` module throughout. Each module creates `logger = logging.getLogger(__name__)`. Log levels: `DEBUG` for hot-path detail (per-tick events), `INFO` for lifecycle events (start/stop, client connect/disconnect), `WARNING` for recoverable data issues, `ERROR` for poll failures.

**Validation:** None in the market data subsystem — inputs are trusted internal calls. Planned: trade endpoint will validate sufficient cash for buys, sufficient shares for sells.

**Authentication:** None. Single-user system with `user_id="default"` hardcoded.

---

*Architecture analysis: 2026-06-04*
