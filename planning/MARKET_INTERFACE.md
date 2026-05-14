# Market Data Interface — Architecture Design

> Design document for the market data layer's internal contract. This is the
> seam that makes the simulator ([`MARKET_SIMULATOR.md`](MARKET_SIMULATOR.md))
> and the Massive client ([`MASSIVE_API.md`](MASSIVE_API.md)) interchangeable,
> and the seam that fans price updates out to the frontend via SSE.

The PLAN.md ground rules this document expands on:

- One process, one container, port 8000, FastAPI + uv.
- SSE (not WebSocket) for server → client price push.
- Env-var-driven source selection: `MASSIVE_API_KEY` present → Massive,
  absent → simulator. No other knobs.
- Single-user model today; the interface should not paint itself into a
  corner if we later add real users.

---

## 1. Why a seam at all

Without an interface boundary, the SSE endpoint would call simulator code
directly and we'd have to fork the route to also call Massive. With the seam:

- **One** background task owns price updates regardless of source.
- **One** in-memory price cache. SSE, REST `/api/portfolio`, and the LLM
  context builder all read from the same source of truth.
- **One** code path for SSE, the price flash effect on the frontend, and the
  P&L snapshot job.

The seam is also the easiest place to add unit tests: the cache can be
populated synthetically and the dependent code exercised without spinning up
either real source.

---

## 2. Layer diagram

```
┌────────────────────────────────────────────────────────────┐
│  FastAPI process                                           │
│                                                            │
│  ┌─────────────────┐    ┌──────────────────────────────┐   │
│  │  MarketDataSrc  │    │  GET /api/stream/prices      │   │
│  │  (one of)       │    │  ── SSE endpoint ──          │   │
│  │                 │    │                              │   │
│  │  SimulatorSrc   │    │  reads PriceCache snapshot,  │   │
│  │       OR        │    │  emits events every ~500ms   │   │
│  │  MassiveSrc     │    └──────────────┬───────────────┘   │
│  │                 │                   │                   │
│  │  writes → cache │◀──────────────────┘                   │
│  └────────┬────────┘                                       │
│           │                                                │
│           ▼                                                │
│  ┌────────────────────────────────────────────────────┐    │
│  │  PriceCache                                        │    │
│  │  {ticker → {price, prev_price, ts, change_pct}}    │    │
│  │  in-memory, single instance                        │    │
│  └────────────────────────────────────────────────────┘    │
│           ▲                                                │
│           │ read by                                        │
│           │                                                │
│  ┌────────┴──────────────────────────────────────────┐     │
│  │  REST routes:                                     │     │
│  │  /api/portfolio  (mark-to-market positions)       │     │
│  │  /api/watchlist  (decorate tickers with prices)   │     │
│  │  /api/portfolio/trade  (use latest price as fill) │     │
│  │  /api/chat  (portfolio snapshot fed to the LLM)   │     │
│  └───────────────────────────────────────────────────┘     │
└────────────────────────────────────────────────────────────┘
```

Two things to internalize from the diagram:

- **Nobody outside the source talks to the source.** The cache is the public
  read interface for the rest of the app.
- **The trade endpoint reads from the same cache.** A market-order fill is
  exactly `cache[ticker].price` at the moment the request arrives. This makes
  the simulator and Massive paths trade-equivalent.

---

## 3. The `MarketDataSource` protocol

```python
from typing import Protocol
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class PriceTick:
    ticker: str
    price: float
    prev_price: float | None
    timestamp: float           # epoch seconds, server clock
    change_percent: float | None  # % change since session open, when known

class MarketDataSource(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def ensure_tickers(self, tickers: set[str]) -> None: ...
    def latest(self, ticker: str) -> PriceTick | None: ...
    def snapshot(self) -> dict[str, PriceTick]: ...
```

Method-by-method:

- **`start()`** — kicks off the background work. For the simulator, this
  starts the GBM tick task. For Massive, this starts the REST poll loop.
  Idempotent: safe to call twice; the second call is a no-op.
- **`stop()`** — graceful shutdown, cancels the background task. Called from
  FastAPI's `shutdown` event so the test suite and reload cycles don't leak
  tasks. Idempotent.
- **`ensure_tickers(tickers)`** — declares the set of symbols the app cares
  about right now. Called by the watchlist add/remove handlers and on
  startup with the current DB watchlist. The simulator uses this to spin up
  GBM state for new tickers (with a sensible seed price); the Massive client
  uses it to know what to put in `ticker.any_of` on the next poll.
- **`latest(ticker)`** — synchronous read of the current cache entry. Returns
  `None` if the ticker is unknown. Used by trade execution and the LLM
  context builder.
- **`snapshot()`** — synchronous copy of the full cache. Used by the SSE
  endpoint and `GET /api/watchlist`.

Both `latest` and `snapshot` are intentionally **synchronous** — the cache is
a pure dict; no I/O. Making them `async` would force every caller into the
async tax for no benefit.

### Why a Protocol, not an ABC

`Protocol` lets `SimulatorSource` and `MassiveSource` declare their conformance
structurally — useful for tests that supply a fake source without needing to
import the base class. We can switch to an ABC later if we want runtime
isinstance checks; today's code doesn't need them.

---

## 4. `PriceCache`

```python
class PriceCache:
    def __init__(self) -> None:
        self._data: dict[str, PriceTick] = {}
        self._lock = asyncio.Lock()  # only for write-write coordination

    def get(self, ticker: str) -> PriceTick | None: ...
    def all(self) -> dict[str, PriceTick]: ...
    async def set(self, tick: PriceTick) -> None: ...
    async def bulk_set(self, ticks: list[PriceTick]) -> None: ...
```

Design notes:

- **In-memory only.** No Redis, no SQLite-for-cache. Process restart =
  re-seed from the next source tick (≤500ms for simulator, ≤15s for Massive).
- **GIL is enough for atomicity of `dict` ops.** The lock is only there to
  serialize *multi-ticker* writes so a concurrent `snapshot()` doesn't see a
  half-applied batch. Readers are lock-free.
- **No subscribe/notify mechanism.** SSE pulls from `snapshot()` on a timer
  (every ~500ms). That is dramatically simpler than push fan-out, and at our
  scale (1 user, ~10 tickers, ~500ms cadence) the cost is invisible. If we
  ever care about precise tick-aligned pushes, we add an `asyncio.Event` or
  pub/sub bus later; today's design specifically does not pay for that.

---

## 5. Lifecycle and wiring

```python
# backend/app/main.py (sketch)
from fastapi import FastAPI

app = FastAPI()
cache = PriceCache()
source: MarketDataSource = build_source(cache)   # § 6 below

@app.on_event("startup")
async def _startup() -> None:
    init_db_if_needed()
    initial_tickers = read_watchlist_tickers()
    await source.ensure_tickers(initial_tickers)
    await source.start()
    start_snapshot_job(cache)   # 30s portfolio snapshot writer

@app.on_event("shutdown")
async def _shutdown() -> None:
    await source.stop()
```

Boot order matters:

1. DB init runs first (lazy create + seed if file missing).
2. Watchlist read seeds `ensure_tickers` so the very first SSE event already
   has prices.
3. Source starts; ticks flow into the cache.
4. The 30s portfolio-snapshot background task starts last, so its first
   snapshot has real prices to mark to.

---

## 6. Source selection

A single factory, one branch:

```python
def build_source(cache: PriceCache) -> MarketDataSource:
    key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if key:
        return MassiveSource(cache=cache, api_key=key)
    return SimulatorSource(cache=cache)
```

No fallback chain, no auto-degrade. If `MASSIVE_API_KEY` is set but invalid,
`MassiveSource.start()` raises and the process fails fast — the operator sees
the misconfiguration immediately rather than getting silent simulator data.

---

## 7. The SSE endpoint

```python
@app.get("/api/stream/prices")
async def stream_prices(request: Request):
    async def gen():
        # initial: full snapshot so client warm-starts without waiting for ticks
        yield format_event(cache.all(), kind="snapshot")
        last_seen: dict[str, float] = {t: pt.price for t, pt in cache.all().items()}

        while True:
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.5)  # 500ms cadence
            current = cache.all()
            deltas = [
                pt for t, pt in current.items()
                if last_seen.get(t) != pt.price
            ]
            if deltas:
                yield format_event(deltas, kind="tick")
                for pt in deltas:
                    last_seen[pt.ticker] = pt.price
            else:
                yield ": keepalive\n\n"   # SSE comment, keeps proxies happy

    return StreamingResponse(gen(), media_type="text/event-stream")
```

### SSE event shape

Two event types, both as JSON in the SSE `data:` field:

**`snapshot`** (sent once on connect):

```json
{
  "type": "snapshot",
  "server_time": 1715712345.678,
  "prices": [
    {"ticker": "AAPL", "price": 190.42, "prev_price": null, "change_percent": 0.81, "ts": 1715712345.6}
  ]
}
```

**`tick`** (sent on changed prices):

```json
{
  "type": "tick",
  "server_time": 1715712346.180,
  "prices": [
    {"ticker": "AAPL", "price": 190.55, "prev_price": 190.42, "change_percent": 0.88, "ts": 1715712346.1}
  ]
}
```

Design points:

- **Only changed tickers in `tick` events** — fewer bytes, makes the
  green/red flash logic on the frontend trivial (the message itself is the
  flash trigger).
- **Send a keepalive comment when nothing changed** so corporate proxies and
  load balancers don't close the connection after 30–60s of silence.
- **`server_time`** lets the frontend detect clock skew and is useful in
  debugging.
- **No retry directive.** `EventSource` reconnects automatically with a 3s
  default; that's fine.
- **No event IDs / `Last-Event-ID`.** We don't replay history — the cache is
  always current, and the snapshot on reconnect re-syncs the client.

### Backpressure

A slow client cannot wedge the server because there is **only one client**
(single-user app). The 500ms tick interval and small payload (a dozen
tickers ≈ 1KB) means total bandwidth is trivial. If we ever go multi-user,
the right move is to drop ticks for slow connections, not to buffer them.

---

## 8. How the rest of the backend uses the layer

| Caller | What it does |
|--------|--------------|
| `GET /api/watchlist` | `[ticker, price] = [(t, cache.get(t)) for t in db_watchlist]` |
| `GET /api/portfolio` | mark each position to `cache.get(ticker).price` |
| `POST /api/portfolio/trade` | fill at `cache.get(ticker).price`; reject if `None` |
| Portfolio snapshot job (30s) | sum positions × `cache.get(...)` + cash → row in `portfolio_snapshots` |
| `POST /api/chat` | inject `cache.all()` into the LLM context block |

The same data feeds every consumer. There is no "live price" path and "delayed
price" path — both sources land in the same cache.

---

## 9. Error model

| Scenario | Source behavior | Cache behavior | API behavior |
|----------|-----------------|----------------|--------------|
| Source startup fails | Re-raise from `start()` | Stays empty | Process exits; operator sees stack trace |
| Single poll fails (Massive) | Log WARN, schedule next poll | Stale entries retained | SSE keeps sending keepalives |
| Ticker unknown to source | `ensure_tickers` no-ops if not addable | No entry written | `latest()` returns `None`; `/trade` returns 400 |
| 429 rate-limited | Back off 2s, retry once | Stale entries retained | SSE keeps sending keepalives |
| Network down for >30s | Sustained WARN logs | Stale entries retained | UI shows "yellow" connection dot via `server_time` staleness |

The simulator has essentially none of these failure modes — it cannot fail
between `start()` and `stop()`. That's by design: the simulator is the
"always works" path.

---

## 10. Test strategy at this layer

1. **Conformance tests** parametrized over both `SimulatorSource` and a
   `FakeMassiveSource` (which intercepts httpx via `respx`). Each must
   satisfy: `start` → cache populated within 1s; `ensure_tickers({"NEW"})` →
   `NEW` shows up within 1s; `stop` cancels background tasks cleanly.
2. **SSE endpoint tests** use a hand-rolled `PriceCache` seeded with fixed
   values, no real source. Asserts: first event is `snapshot`; tick events
   only fire when prices change; keepalives appear when prices are stable;
   disconnect cancels the generator within one tick.
3. **Trade endpoint integration test** asserts that a buy at `cache.set(...,
   price=100)` debits exactly `100 × qty` from cash, exercising the
   "cache is the source of truth" contract.

---

## 11. What this design deliberately doesn't do

- **No tiered fallback** (Massive → simulator on failure). If Massive is set,
  the user wants Massive; silent fallback would hide a misconfiguration.
- **No price history in the cache.** Sparklines accumulate on the **frontend**
  from the SSE stream since page load, per PLAN.md. Server-side history lives
  in `portfolio_snapshots` (portfolio-level) and on the upstream
  (per-ticker, via `aggs/.../range/...` if we ever want it).
- **No multi-process scaling.** SSE long-lived connections + in-memory cache
  is a single-process design. If we ever shard, the cache moves to Redis and
  the source becomes a sidecar — but that is a real rewrite, not an
  extension, and we explicitly defer it.
- **No subscribe/topic primitive.** SSE pulls from `snapshot()`. We do not
  build a pub/sub layer for a 1-user app.

---

## 12. Decisions log (for future agents)

| Decision | Chosen | Considered alternative | Why |
|----------|--------|------------------------|-----|
| Source interface kind | `Protocol` | `ABC` | structural typing, easier fakes |
| Cache type | `dict` in-memory | Redis | YAGNI for one process |
| Push mechanism | Polling `snapshot()` every 500ms | per-tick event bus | trivial to reason about |
| Event payload | Only changed tickers in `tick` | full snapshot every tick | bandwidth, flash trigger is "did this ticker appear" |
| Source selection | Env var, fail-fast | auto-degrade to sim on error | a silent fallback hides bugs |
| Trade fill price | `cache.get(ticker).price` | separate "last trade" endpoint per call | one source of truth |
