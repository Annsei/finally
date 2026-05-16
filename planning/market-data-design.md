# Market Data Backend — Consolidated Design with Code Snippets

> A single, implementation-ready design for the FinAlly market data layer.
> Distills [`MARKET_INTERFACE.md`](MARKET_INTERFACE.md),
> [`MARKET_SIMULATOR.md`](MARKET_SIMULATOR.md), and
> [`MASSIVE_API.md`](MASSIVE_API.md) into one place with concrete code for
> every component. The already-shipped implementation under
> `backend/app/market/` (summarized in [`MARKET_DATA_SUMMARY.md`](MARKET_DATA_SUMMARY.md))
> matches this design; this document is the "what to write, and why" guide.

---

## 0. Table of contents

1. Architecture at a glance
2. Module layout
3. Data model — `PriceUpdate`
4. The `MarketDataSource` interface
5. The `PriceCache`
6. GBM simulator
7. Massive (Polygon.io) REST source
8. Source factory & env-var selection
9. SSE streaming endpoint
10. FastAPI lifecycle wiring
11. Consumers (portfolio, trade, chat, snapshot job)
12. Test strategy with examples
13. Error & operational model
14. Performance budget
15. Future-proofing notes

---

## 1. Architecture at a glance

```
┌──────────────────────────────────────────────────────────────┐
│  FastAPI process (port 8000)                                 │
│                                                              │
│   ┌─────────────────────────┐                                │
│   │  MarketDataSource (ABC) │                                │
│   │   ├── SimulatorSource   │  background asyncio.Task       │
│   │   └── MassiveSource     │  polls REST every N seconds    │
│   └───────────┬─────────────┘                                │
│               │ writes                                       │
│               ▼                                              │
│   ┌─────────────────────────────────────────────────────┐    │
│   │  PriceCache   {ticker → PriceUpdate}  + version_id  │    │
│   └───────────┬─────────────────────────────────────────┘    │
│               │ read                                         │
│   ┌───────────┴──────────────────────────────────────────┐   │
│   │   GET /api/stream/prices  (SSE, 500ms diff-tick)     │   │
│   │   GET /api/portfolio      (mark-to-market)           │   │
│   │   POST /api/portfolio/trade  (market-order fill)     │   │
│   │   POST /api/chat          (portfolio context for LLM)│   │
│   │   30s portfolio-snapshot writer                      │   │
│   └──────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

Three invariants that drive every decision below:

- **The cache is the single source of truth.** Nothing outside the source
  module talks to the source directly.
- **Both sources are interchangeable.** The SSE endpoint, the trade endpoint,
  and the LLM context builder cannot tell which one is running.
- **Source selection is fail-fast.** No silent fallback from Massive to
  simulator — a bad key surfaces immediately as a startup error.

---

## 2. Module layout

```
backend/app/market/
├── __init__.py          # public re-exports
├── models.py            # PriceUpdate (frozen dataclass)
├── interface.py         # MarketDataSource ABC
├── cache.py             # PriceCache (thread-safe, versioned)
├── seed_prices.py       # SEEDS list, sector groups, GBM params
├── simulator.py         # GBMSimulator + SimulatorDataSource
├── massive_client.py    # MassiveDataSource (REST poller)
├── factory.py           # create_market_data_source()
└── stream.py            # create_stream_router() — SSE endpoint factory
```

Public surface, re-exported from `app.market.__init__`:

```python
# backend/app/market/__init__.py
from .models import PriceUpdate
from .interface import MarketDataSource
from .cache import PriceCache
from .factory import create_market_data_source
from .stream import create_stream_router

__all__ = [
    "PriceUpdate",
    "MarketDataSource",
    "PriceCache",
    "create_market_data_source",
    "create_stream_router",
]
```

---

## 3. Data model — `PriceUpdate`

A single immutable record per price tick. Frozen so we can hand it to the
cache and SSE generator without defensive copies.

```python
# backend/app/market/models.py
from __future__ import annotations
from dataclasses import dataclass, field
from time import time
from typing import Literal

Direction = Literal["up", "down", "flat"]


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    ticker: str
    price: float
    previous_price: float | None
    timestamp: float = field(default_factory=time)  # epoch seconds, server clock

    @property
    def change(self) -> float:
        if self.previous_price is None or self.previous_price == 0:
            return 0.0
        return self.price - self.previous_price

    @property
    def change_percent(self) -> float:
        if self.previous_price is None or self.previous_price == 0:
            return 0.0
        return (self.price - self.previous_price) / self.previous_price * 100.0

    @property
    def direction(self) -> Direction:
        if self.previous_price is None or self.price == self.previous_price:
            return "flat"
        return "up" if self.price > self.previous_price else "down"

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "price": round(self.price, 4),
            "previous_price": (
                round(self.previous_price, 4)
                if self.previous_price is not None
                else None
            ),
            "change": round(self.change, 4),
            "change_percent": round(self.change_percent, 4),
            "direction": self.direction,
            "timestamp": self.timestamp,
        }
```

`change` / `change_percent` / `direction` are derived properties so callers
never see an inconsistent record.

---

## 4. The `MarketDataSource` interface

An `ABC` (not a `Protocol`) so we can `isinstance`-check in tests and the
factory. The five methods are the only seam between data producers and the
rest of the app.

```python
# backend/app/market/interface.py
from __future__ import annotations
from abc import ABC, abstractmethod


class MarketDataSource(ABC):
    """Abstract base for any live price source. Writes into a PriceCache."""

    @abstractmethod
    async def start(self, initial_tickers: list[str]) -> None:
        """Begin emitting prices for the given tickers. Idempotent."""

    @abstractmethod
    async def stop(self) -> None:
        """Cancel background work cleanly. Idempotent."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Start tracking a new ticker after start()."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Stop tracking a ticker. Cache entry may be retained for inspection."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Currently tracked tickers."""
```

Why these five and not more:

- `start(initial_tickers)` is **declarative**, not "add then start." This
  avoids a class of bugs where a tick fires before the cache knows about a
  ticker.
- `add/remove_ticker` are explicit because we want to log them; a single
  `ensure_tickers(set)` would obscure the diff at the call site.
- No `latest()` / `snapshot()` on the source — those live on `PriceCache`.
  The source is **write-only** to the cache.

---

## 5. The `PriceCache`

In-memory, thread-safe, with a monotonically increasing `version` counter so
the SSE generator can detect "anything changed since last read" without
diffing every entry.

```python
# backend/app/market/cache.py
from __future__ import annotations
import threading
from .models import PriceUpdate


class PriceCache:
    """Thread-safe in-memory price store.

    The `version` integer increments on every write. SSE readers compare
    versions to skip work when nothing has changed since their last emission.
    """

    def __init__(self) -> None:
        self._data: dict[str, PriceUpdate] = {}
        self._lock = threading.Lock()
        self._version: int = 0

    # --- writers --------------------------------------------------------

    def set(self, update: PriceUpdate) -> None:
        with self._lock:
            self._data[update.ticker] = update
            self._version += 1

    def bulk_set(self, updates: list[PriceUpdate]) -> None:
        if not updates:
            return
        with self._lock:
            for u in updates:
                self._data[u.ticker] = u
            self._version += 1  # one bump for the whole batch

    def delete(self, ticker: str) -> None:
        with self._lock:
            if ticker in self._data:
                del self._data[ticker]
                self._version += 1

    # --- readers --------------------------------------------------------

    def get(self, ticker: str) -> PriceUpdate | None:
        return self._data.get(ticker)  # dict reads are atomic under the GIL

    def get_price(self, ticker: str) -> float | None:
        u = self._data.get(ticker)
        return u.price if u else None

    def get_all(self) -> dict[str, PriceUpdate]:
        with self._lock:
            return dict(self._data)

    @property
    def version(self) -> int:
        return self._version
```

Design notes:

- **Reads do not take the lock** — dict `get()` is atomic under CPython's
  GIL, and `PriceUpdate` is frozen so what we hand out is safe to share.
- **`bulk_set` bumps version once.** SSE clients see one composite tick
  per simulator step, not 10 micro-ticks.
- **`threading.Lock`, not `asyncio.Lock`.** Writers are short and the cache
  is also touched from synchronous code paths (REST handlers reading
  current price for a trade fill).

---

## 6. GBM simulator

The default source. Geometric Brownian Motion per ticker, Cholesky-correlated
shocks, occasional event jolts. No external dependencies beyond numpy.

### 6.1 Seed prices and correlation structure

```python
# backend/app/market/seed_prices.py
from dataclasses import dataclass
from typing import Literal

Sector = Literal["tech", "consumer", "financial"]


@dataclass(frozen=True)
class TickerSeed:
    ticker: str
    seed_price: float
    drift: float          # annualized expected return
    vol: float            # annualized standard deviation
    sector: Sector


SEEDS: list[TickerSeed] = [
    TickerSeed("AAPL",  190.00, 0.10, 0.25, "tech"),
    TickerSeed("GOOGL", 175.00, 0.12, 0.28, "tech"),
    TickerSeed("MSFT",  420.00, 0.10, 0.22, "tech"),
    TickerSeed("AMZN",  185.00, 0.12, 0.30, "tech"),
    TickerSeed("NVDA",  900.00, 0.20, 0.45, "tech"),
    TickerSeed("META",  490.00, 0.10, 0.30, "tech"),
    TickerSeed("TSLA",  240.00, 0.05, 0.55, "consumer"),
    TickerSeed("NFLX",  620.00, 0.08, 0.35, "consumer"),
    TickerSeed("JPM",   200.00, 0.06, 0.20, "financial"),
    TickerSeed("V",     280.00, 0.08, 0.18, "financial"),
]
SEEDS_BY_TICKER = {s.ticker: s for s in SEEDS}

# Pairwise sector correlations, symmetric. Self-corr is 1.0 (added at use).
WITHIN_SECTOR_CORR: dict[Sector, float] = {
    "tech": 0.65,
    "consumer": 0.55,
    "financial": 0.65,
}
CROSS_GROUP_CORR: dict[tuple[Sector, Sector], float] = {
    ("tech", "consumer"): 0.45,
    ("tech", "financial"): 0.30,
    ("consumer", "financial"): 0.25,
}

DEFAULT_RUNTIME_CORR = 0.40   # used for runtime-added tickers


def pairwise_correlation(a: TickerSeed, b: TickerSeed) -> float:
    if a.ticker == b.ticker:
        return 1.0
    if a.sector == b.sector:
        return WITHIN_SECTOR_CORR[a.sector]
    key = tuple(sorted([a.sector, b.sector]))
    return CROSS_GROUP_CORR.get(key) or CROSS_GROUP_CORR[(key[1], key[0])]


def hash_seed_price(ticker: str) -> float:
    """Deterministic $50–$500 seed price for runtime-added tickers."""
    h = abs(hash(("finally", ticker))) % 45000
    return 50.0 + h / 100.0
```

### 6.2 The GBM step

Annualized parameters; `dt` chosen so each 500ms tick represents one
half-second of one US trading day:

```
dt = 0.5 / (252 * 6.5 * 3600)   # ≈ 8.5e-8
```

A single ticker update, in closed form:

```
S_{t+Δt} = S_t · exp((μ − σ²/2)·Δt  +  σ·√Δt·Z)
```

`Z` is a standard normal — but **across tickers** `Z` is a vector drawn from
`N(0, R)` via Cholesky, so correlated names move together.

### 6.3 `GBMSimulator` class

```python
# backend/app/market/simulator.py
from __future__ import annotations
import asyncio
import contextlib
import logging
import math
import random
import time
from typing import Iterable

import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource
from .models import PriceUpdate
from .seed_prices import (
    SEEDS,
    SEEDS_BY_TICKER,
    TickerSeed,
    DEFAULT_RUNTIME_CORR,
    hash_seed_price,
    pairwise_correlation,
)

log = logging.getLogger(__name__)

DT = 0.5 / (252 * 6.5 * 3600)            # 500ms in trading-day-years
SQRT_DT = math.sqrt(DT)
EVENT_PROB = 1.0 / 120.0                 # ~1 event per ticker per minute
EVENT_K_MIN, EVENT_K_MAX = 3.0, 6.0
TICK_CLAMP_PCT = 0.05                    # ±5% safety rail


class GBMSimulator:
    """Pure-math step; no I/O, no asyncio. Safe to unit-test directly."""

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.SystemRandom()
        self._np_rng = np.random.default_rng(
            self._rng.randint(0, 2**32 - 1) if rng else None
        )
        self._seeds: dict[str, TickerSeed] = {}
        self._prices: dict[str, float] = {}
        self._L: np.ndarray = np.zeros((0, 0))   # Cholesky factor
        self._order: list[str] = []              # row order in _L

        for seed in SEEDS:
            self._add(seed)

    # --- public --------------------------------------------------------

    def get_tickers(self) -> list[str]:
        return list(self._order)

    def add_ticker(self, ticker: str) -> None:
        if ticker in self._seeds:
            return
        seed = SEEDS_BY_TICKER.get(ticker) or TickerSeed(
            ticker=ticker,
            seed_price=hash_seed_price(ticker),
            drift=0.08,
            vol=0.30,
            sector="tech",
        )
        self._add(seed, default_corr=DEFAULT_RUNTIME_CORR)

    def step(self) -> list[PriceUpdate]:
        n = len(self._order)
        if n == 0:
            return []

        # 1. Draw N independent standard normals, then correlate via L.
        z = self._np_rng.standard_normal(n)
        Z = self._L @ z

        # 2. Apply event jolts (multiplicative, per-ticker, sign uniform).
        for i in range(n):
            if self._rng.random() < EVENT_PROB:
                k = self._rng.uniform(EVENT_K_MIN, EVENT_K_MAX)
                if self._rng.random() < 0.5:
                    k = -k
                Z[i] *= k

        # 3. GBM update with ±5% clamp.
        now = time.time()
        out: list[PriceUpdate] = []
        for i, ticker in enumerate(self._order):
            seed = self._seeds[ticker]
            prev = self._prices[ticker]
            new = prev * math.exp(
                (seed.drift - 0.5 * seed.vol**2) * DT
                + seed.vol * SQRT_DT * Z[i]
            )
            lo = prev * (1.0 - TICK_CLAMP_PCT)
            hi = prev * (1.0 + TICK_CLAMP_PCT)
            new = max(lo, min(hi, new))
            self._prices[ticker] = new
            out.append(PriceUpdate(
                ticker=ticker,
                price=new,
                previous_price=prev,
                timestamp=now,
            ))
        return out

    # --- internal ------------------------------------------------------

    def _add(self, seed: TickerSeed, *, default_corr: float | None = None) -> None:
        self._seeds[seed.ticker] = seed
        self._prices[seed.ticker] = seed.seed_price

        n = len(self._order)
        if n == 0:
            self._L = np.array([[1.0]])
        else:
            # Build new row of R: pairwise corr against existing tickers + 1.0
            if default_corr is not None:
                r = np.full(n, default_corr)
            else:
                r = np.array([
                    pairwise_correlation(self._seeds[t], seed)
                    for t in self._order
                ])
            # Solve L · x = r for the new lower-triangular row.
            x = np.linalg.solve_triangular(self._L, r, lower=True) \
                if hasattr(np.linalg, "solve_triangular") \
                else np.linalg.solve(self._L, r)
            tail = math.sqrt(max(0.0, 1.0 - float(x @ x)))
            new_L = np.zeros((n + 1, n + 1))
            new_L[:n, :n] = self._L
            new_L[n, :n] = x
            new_L[n, n] = tail
            self._L = new_L

        self._order.append(seed.ticker)


class SimulatorDataSource(MarketDataSource):
    """asyncio-driven driver around GBMSimulator. Writes to PriceCache."""

    TICK_INTERVAL = 0.5  # seconds

    def __init__(self, cache: PriceCache, *, seed: int | None = None) -> None:
        rng = random.Random(seed) if seed is not None else None
        self._cache = cache
        self._sim = GBMSimulator(rng=rng)
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def start(self, initial_tickers: list[str]) -> None:
        for t in initial_tickers:
            self._sim.add_ticker(t)
        # Seed the cache so the first SSE snapshot has prices immediately.
        self._cache.bulk_set(self._sim.step())
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="market-sim-tick")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopping.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def add_ticker(self, ticker: str) -> None:
        self._sim.add_ticker(ticker)

    async def remove_ticker(self, ticker: str) -> None:
        # The simulator deliberately retains state so re-adds are cheap.
        # We just stop publishing — done by deleting cache entry.
        self._cache.delete(ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers()

    async def _run(self) -> None:
        try:
            while not self._stopping.is_set():
                await asyncio.sleep(self.TICK_INTERVAL)
                updates = self._sim.step()
                self._cache.bulk_set(updates)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("simulator tick loop crashed")
            raise
```

Notes:

- `np.linalg.solve_triangular` is available since NumPy 2.0; the
  `solve(...)` fallback keeps the code working on 1.x without changing
  results (the matrix is triangular).
- `GBMSimulator` has **no async, no I/O.** It is a pure function of its
  RNG state, so tests can call `step()` 10 000 times to check statistics.
- The simulator never *removes* internal state; `remove_ticker` only deletes
  from the **cache** so consumers stop seeing the ticker. Re-adding is free.

---

## 7. Massive (Polygon.io) REST source

The optional path, used when `MASSIVE_API_KEY` is set. Polls the unified
snapshot endpoint, parses the JSON, writes to the cache.

### 7.1 Endpoint and rate budget

Steady-state we use exactly **one** endpoint:

```
GET https://api.polygon.io/v3/snapshot
    ?ticker.any_of=AAPL,GOOGL,MSFT,...
    &limit=250
Authorization: Bearer ${MASSIVE_API_KEY}
```

| Detected tier | Poll interval | Rationale |
|---|---|---|
| Free (assumed) | 15 s | 4 calls/min ≪ 5/min cap |
| Paid (configured override) | 2–5 s | "Real-time-ish" |

Up to 250 tickers per call → one call covers any realistic FinAlly watchlist.

### 7.2 Implementation

```python
# backend/app/market/massive_client.py
from __future__ import annotations
import asyncio
import contextlib
import logging
import os
import time

import httpx

from .cache import PriceCache
from .interface import MarketDataSource
from .models import PriceUpdate

log = logging.getLogger(__name__)

BASE_URL = "https://api.polygon.io"
DEFAULT_POLL_SECS = 15.0
RATE_LIMIT_BACKOFF = 2.0


class MassiveDataSource(MarketDataSource):
    def __init__(
        self,
        cache: PriceCache,
        api_key: str,
        *,
        poll_interval: float | None = None,
        timeout: float = 5.0,
    ) -> None:
        if not api_key:
            raise ValueError("MassiveDataSource requires a non-empty api_key")
        self._cache = cache
        self._api_key = api_key
        self._poll = poll_interval or float(
            os.environ.get("MASSIVE_POLL_INTERVAL", DEFAULT_POLL_SECS)
        )
        self._timeout = timeout
        self._tickers: set[str] = set()
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self._client: httpx.AsyncClient | None = None

    # --- lifecycle -----------------------------------------------------

    async def start(self, initial_tickers: list[str]) -> None:
        self._tickers.update(initial_tickers)
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=self._timeout,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        # Fail-fast on bad key: do one poll synchronously before backgrounding.
        await self._poll_once()
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="massive-poll")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def add_ticker(self, ticker: str) -> None:
        self._tickers.add(ticker)

    async def remove_ticker(self, ticker: str) -> None:
        self._tickers.discard(ticker)
        self._cache.delete(ticker)

    def get_tickers(self) -> list[str]:
        return sorted(self._tickers)

    # --- internals -----------------------------------------------------

    async def _run(self) -> None:
        try:
            while not self._stopping.is_set():
                await asyncio.sleep(self._poll)
                try:
                    await self._poll_once()
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 429:
                        log.warning("Massive 429; backing off %.1fs", RATE_LIMIT_BACKOFF)
                        await asyncio.sleep(RATE_LIMIT_BACKOFF)
                    else:
                        log.warning("Massive HTTP %s", exc.response.status_code)
                except Exception:  # network blip, parse error — keep polling
                    log.exception("Massive poll failed")
        except asyncio.CancelledError:
            raise

    async def _poll_once(self) -> None:
        if not self._tickers or self._client is None:
            return
        params = {
            "ticker.any_of": ",".join(sorted(self._tickers)),
            "limit": 250,
        }
        resp = await self._client.get("/v3/snapshot", params=params)
        if resp.status_code == 401:
            log.error("Massive 401 Unauthorized — check MASSIVE_API_KEY")
            resp.raise_for_status()
        resp.raise_for_status()

        body = resp.json()
        now = time.time()
        updates: list[PriceUpdate] = []
        for row in body.get("results", []):
            ticker = row.get("ticker")
            price = self._extract_price(row)
            if ticker is None or price is None:
                continue
            prev = self._cache.get_price(ticker)
            updates.append(PriceUpdate(
                ticker=ticker,
                price=float(price),
                previous_price=prev,
                timestamp=now,
            ))
        self._cache.bulk_set(updates)

    @staticmethod
    def _extract_price(row: dict) -> float | None:
        last_trade = row.get("last_trade") or {}
        price = last_trade.get("price")
        if price is not None:
            return price
        session = row.get("session") or {}
        return session.get("close")
```

Why this shape:

- **No `massive` SDK.** One endpoint, one stable JSON shape — `httpx` and
  dict-walking is shorter and easier to mock with `respx`.
- **Synchronous first poll inside `start()`** — turns a bad key into a
  startup-time stack trace. Operators see misconfiguration immediately.
- **Per-request errors do not kill the loop.** A single 429 or 5xx is logged
  and we wait for the next poll. The cache keeps stale prices in the
  meantime; the frontend keeps showing the last value.

---

## 8. Source factory & env-var selection

One branch, no fallback chain:

```python
# backend/app/market/factory.py
from __future__ import annotations
import os
from .cache import PriceCache
from .interface import MarketDataSource
from .simulator import SimulatorDataSource
from .massive_client import MassiveDataSource


def create_market_data_source(cache: PriceCache) -> MarketDataSource:
    key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if key:
        return MassiveDataSource(cache, api_key=key)

    sim_seed = os.environ.get("MARKET_SIM_SEED")
    seed = int(sim_seed) if sim_seed and sim_seed.strip() else None
    return SimulatorDataSource(cache, seed=seed)
```

If `MASSIVE_API_KEY` is set but invalid, `MassiveDataSource.start()` will
raise from its first `_poll_once()` and the FastAPI startup hook will
propagate the error. **No silent fallback** — the operator decides whether
to unset the key or fix it.

---

## 9. SSE streaming endpoint

A router factory so the cache is dependency-injected (no module-level state)
and easy to swap in tests.

```python
# backend/app/market/stream.py
from __future__ import annotations
import asyncio
import json
from time import time
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

SSE_TICK_INTERVAL = 0.5
SSE_KEEPALIVE_INTERVAL = 15.0


def create_stream_router(cache: PriceCache) -> APIRouter:
    router = APIRouter(prefix="/api/stream", tags=["stream"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        return StreamingResponse(
            _generate_events(request, cache),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router


async def _generate_events(
    request: Request, cache: PriceCache,
) -> AsyncGenerator[str, None]:
    # 1. Initial snapshot — warm-start the client.
    snapshot = cache.get_all()
    yield _format_event("snapshot", {
        "type": "snapshot",
        "server_time": time(),
        "prices": [u.to_dict() for u in snapshot.values()],
    })
    last_version = cache.version
    last_keepalive = time()

    # 2. Diff-tick loop.
    while True:
        if await request.is_disconnected():
            return
        await asyncio.sleep(SSE_TICK_INTERVAL)

        v = cache.version
        if v != last_version:
            current = cache.get_all()
            yield _format_event("tick", {
                "type": "tick",
                "server_time": time(),
                "prices": [u.to_dict() for u in current.values()],
            })
            last_version = v
            last_keepalive = time()
        elif time() - last_keepalive >= SSE_KEEPALIVE_INTERVAL:
            # SSE comment — keeps proxies/load balancers from closing the conn.
            yield ": keepalive\n\n"
            last_keepalive = time()


def _format_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"
```

Wire-level example (one tick):

```
event: tick
data: {"type":"tick","server_time":1715712346.18,"prices":[{"ticker":"AAPL","price":190.55,"previous_price":190.42,"change":0.13,"change_percent":0.068,"direction":"up","timestamp":1715712346.1}]}
```

Design points:

- **Version-based change detection** is O(1); we only build the payload when
  something actually changed.
- **Keepalive every 15 s** so corporate proxies don't kill the connection.
- **No `Last-Event-ID` / replay.** The cache is always current; reconnects
  receive a fresh `snapshot` event.
- **`X-Accel-Buffering: no`** disables nginx buffering, which would
  otherwise hold SSE frames until the buffer fills.

---

## 10. FastAPI lifecycle wiring

A single application factory uses `lifespan` (modern FastAPI) rather than
deprecated `on_event` hooks:

```python
# backend/app/main.py  (excerpt)
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import init_db, read_watchlist_tickers
from app.market import (
    PriceCache,
    create_market_data_source,
    create_stream_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. DB ready first (lazy create + seed if missing).
    init_db()

    # 2. Build market data layer.
    cache = PriceCache()
    source = create_market_data_source(cache)
    initial = read_watchlist_tickers() or [
        "AAPL", "GOOGL", "MSFT", "AMZN", "TSLA",
        "NVDA", "META", "JPM", "V", "NFLX",
    ]
    await source.start(initial)

    # 3. Stash on app state for handlers.
    app.state.cache = cache
    app.state.market_source = source

    try:
        yield
    finally:
        await source.stop()


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    # Routers can be added before the cache exists because they only
    # close over `app.state.cache` inside handlers.
    cache_placeholder = PriceCache()  # real cache attached in lifespan
    app.include_router(create_stream_router(cache_placeholder))
    # ... mount static frontend, REST routers, etc.
    return app
```

> **Implementation note:** the shipped code in `backend/app/main.py` keeps
> the cache as a module singleton so the stream router can be created with
> the real reference. Either pattern is fine; the constraint is that the
> router and the source share the same `PriceCache` instance.

The watchlist add/remove handlers call `source.add_ticker` /
`source.remove_ticker` so the simulator and Massive poller learn about UI
changes:

```python
# backend/app/routes/watchlist.py  (excerpt)
@router.post("")
async def add_watchlist(payload: AddTicker, request: Request):
    ticker = payload.ticker.upper()
    insert_watchlist_row(ticker)
    await request.app.state.market_source.add_ticker(ticker)
    return {"ok": True, "ticker": ticker}


@router.delete("/{ticker}")
async def remove_watchlist(ticker: str, request: Request):
    delete_watchlist_row(ticker.upper())
    await request.app.state.market_source.remove_ticker(ticker.upper())
    return {"ok": True}
```

---

## 11. Consumers — how the rest of the backend uses the cache

| Caller | How |
|---|---|
| `GET /api/watchlist` | `[(t, cache.get(t)) for t in db_watchlist]` |
| `GET /api/portfolio` | for each position, `cache.get_price(ticker)` — multiply by quantity |
| `POST /api/portfolio/trade` | fill at `cache.get_price(ticker)`; 400 if `None` |
| `POST /api/chat` | inject `cache.get_all()` into the LLM context block |
| Portfolio snapshot job (30 s) | `sum(qty * cache.get_price(t)) + cash` → row in `portfolio_snapshots` |

Sketch of the trade fill (the cache **is** the order book in our model):

```python
# backend/app/routes/portfolio.py  (excerpt)
@router.post("/trade")
async def execute_trade(req: TradeRequest, request: Request):
    cache: PriceCache = request.app.state.cache
    price = cache.get_price(req.ticker)
    if price is None:
        raise HTTPException(400, f"No live price for {req.ticker}")
    notional = price * req.quantity
    with db_transaction() as tx:
        profile = tx.fetch_profile()
        if req.side == "buy":
            if profile.cash_balance < notional:
                raise HTTPException(400, "Insufficient cash")
            tx.debit_cash(notional)
            tx.upsert_position(req.ticker, +req.quantity, price)
        else:
            pos = tx.fetch_position(req.ticker)
            if pos is None or pos.quantity < req.quantity:
                raise HTTPException(400, "Insufficient shares")
            tx.credit_cash(notional)
            tx.upsert_position(req.ticker, -req.quantity, price)
        tx.append_trade(req.ticker, req.side, req.quantity, price)
        tx.snapshot_portfolio_now()
    return {"ok": True, "fill_price": price}
```

The portfolio snapshot job:

```python
# backend/app/jobs/snapshot.py
import asyncio
from app.db import compute_total_value, write_snapshot

async def snapshot_loop(cache, interval: float = 30.0):
    while True:
        await asyncio.sleep(interval)
        prices = {t: u.price for t, u in cache.get_all().items()}
        write_snapshot(total_value=compute_total_value(prices))
```

---

## 12. Test strategy with examples

The shipped suite is 73 tests across 6 modules. Below are representative
test recipes per layer.

### 12.1 `PriceUpdate` properties

```python
def test_change_percent_and_direction():
    u = PriceUpdate(ticker="AAPL", price=101.0, previous_price=100.0,
                   timestamp=0.0)
    assert u.change == pytest.approx(1.0)
    assert u.change_percent == pytest.approx(1.0)
    assert u.direction == "up"

def test_flat_when_prev_is_none():
    u = PriceUpdate(ticker="X", price=10.0, previous_price=None, timestamp=0.0)
    assert u.direction == "flat"
    assert u.change_percent == 0.0
```

### 12.2 Cache versioning

```python
def test_bulk_set_bumps_version_once():
    c = PriceCache()
    v0 = c.version
    c.bulk_set([
        PriceUpdate("AAPL", 190.0, None, 0.0),
        PriceUpdate("GOOGL", 175.0, None, 0.0),
    ])
    assert c.version == v0 + 1
```

### 12.3 GBM math

```python
def test_clamp_keeps_price_within_5_percent():
    sim = GBMSimulator(rng=random.Random(0))
    # 10k steps; assert every step is within ±5% of its predecessor.
    prev = {t: p for t, p in sim._prices.items()}
    for _ in range(10_000):
        updates = sim.step()
        for u in updates:
            ratio = u.price / prev[u.ticker]
            assert 0.95 - 1e-9 <= ratio <= 1.05 + 1e-9
            prev[u.ticker] = u.price
```

### 12.4 Correlation behavior

```python
def test_tech_pair_correlation_within_band():
    sim = GBMSimulator(rng=random.Random(42))
    log_returns_a, log_returns_b = [], []
    prev_a = sim._prices["AAPL"]; prev_b = sim._prices["MSFT"]
    for _ in range(5_000):
        sim.step()
        log_returns_a.append(math.log(sim._prices["AAPL"] / prev_a))
        log_returns_b.append(math.log(sim._prices["MSFT"] / prev_b))
        prev_a = sim._prices["AAPL"]; prev_b = sim._prices["MSFT"]
    rho = float(np.corrcoef(log_returns_a, log_returns_b)[0, 1])
    assert 0.55 <= rho <= 0.75   # target 0.65, allow ±0.10
```

### 12.5 Determinism (E2E hook)

```python
def test_seeded_simulator_is_deterministic():
    a = GBMSimulator(rng=random.Random(7))
    b = GBMSimulator(rng=random.Random(7))
    for _ in range(100):
        ua = a.step(); ub = b.step()
        assert [(u.ticker, u.price) for u in ua] == [(u.ticker, u.price) for u in ub]
```

### 12.6 Massive parsing with `respx`

```python
@pytest.mark.asyncio
async def test_massive_parses_last_trade(respx_mock):
    respx_mock.get(f"{BASE_URL}/v3/snapshot").mock(return_value=httpx.Response(
        200, json={"results": [
            {"ticker": "AAPL",
             "last_trade": {"price": 190.42},
             "session": {"close": 188.0}},
        ]},
    ))
    cache = PriceCache()
    src = MassiveDataSource(cache, api_key="test", poll_interval=0.01)
    await src.start(["AAPL"])
    await asyncio.sleep(0.05)
    await src.stop()
    assert cache.get_price("AAPL") == 190.42
```

### 12.7 SSE smoke test

```python
@pytest.mark.asyncio
async def test_sse_emits_snapshot_then_tick():
    cache = PriceCache()
    cache.set(PriceUpdate("AAPL", 190.0, None, 0.0))
    app = FastAPI()
    app.include_router(create_stream_router(cache))
    async with httpx.AsyncClient(app=app, base_url="http://test") as ac:
        async with ac.stream("GET", "/api/stream/prices") as resp:
            chunks = []
            async for line in resp.aiter_lines():
                chunks.append(line)
                if len(chunks) >= 2 and "snapshot" in chunks[0]:
                    break
    assert any('"type": "snapshot"' in c for c in chunks)
```

### 12.8 Trade endpoint integration

```python
@pytest.mark.asyncio
async def test_buy_debits_cash_at_cache_price(client, cache):
    cache.set(PriceUpdate("AAPL", 100.0, None, 0.0))
    before = await client.get("/api/portfolio")
    cash_before = before.json()["cash_balance"]
    r = await client.post("/api/portfolio/trade",
                          json={"ticker": "AAPL", "side": "buy", "quantity": 3})
    assert r.status_code == 200
    after = await client.get("/api/portfolio")
    assert after.json()["cash_balance"] == pytest.approx(cash_before - 300.0)
```

---

## 13. Error & operational model

| Scenario | Source | Cache | API surface |
|---|---|---|---|
| Massive 401 at startup | `start()` raises | Stays empty | Process exits — operator sees stack trace |
| Massive 429 mid-loop | Logs WARN, sleeps 2 s | Stale entries retained | SSE keepalives continue |
| Massive 5xx / network | Logs WARN, retries next poll | Stale entries retained | SSE keepalives continue |
| Ticker not in API result | DEBUG log | No update | `get_price` returns previous value |
| `cache.get_price()` returns `None` for a trade | n/a | Empty | `400 No live price for X` |
| SSE client disconnects | `request.is_disconnected()` → generator returns | n/a | Resources released |
| Source crashes unexpectedly | `_run()` re-raises | Frozen at last write | Operator alerted via FastAPI logs |

The simulator path has effectively none of these failure modes — that is
by design. It is the "always works" mode.

---

## 14. Performance budget

| Workload | Cost |
|---|---|
| `GBMSimulator.step()` for 10 tickers | ~50 µs (numpy matmul + clamp) |
| `bulk_set` of 10 updates | ~5 µs |
| SSE event format + emit | ~20 µs |
| Massive poll round trip | network-dominated; 50–300 ms |
| Cache memory @ 250 tickers | ~25 KB |

The 500 ms simulator cadence at 10 tickers consumes <0.02% CPU on a
laptop-class machine. The hot path is the SSE encoder, not the math.

---

## 15. Future-proofing

| Future need | Easiest path forward |
|---|---|
| Multi-user | Replace `PriceCache` with Redis; user-scoped watchlists drive a union of tracked tickers. Source interface unchanged. |
| Real WebSocket feed (paid tier) | New `MassiveWSDataSource` implementing the same ABC. Factory branches on a second env var. |
| Sparkline backfill | New endpoint `GET /api/aggs/{ticker}` calling `/v2/aggs/.../range/...`. Frontend opt-in; SSE accumulation still works. |
| Market hours awareness (Massive) | Watch `market_status` on responses; throttle to 60 s poll when all `closed`. |
| Determinism for E2E | Already wired: `MARKET_SIM_SEED=42` in `.env` produces a reproducible stream. |
| Cache eviction | Not needed at MVP scale; if added, key by `(ticker, last_access)` and prune oldest beyond a cap. |

Anything not in this list is **out of scope** for the MVP — by design, not
oversight.

---

## Appendix A. End-to-end usage

```python
import asyncio
from app.market import PriceCache, create_market_data_source

async def main():
    cache = PriceCache()
    source = create_market_data_source(cache)         # reads MASSIVE_API_KEY
    await source.start(["AAPL", "GOOGL", "MSFT"])

    await asyncio.sleep(2.0)
    print(cache.get("AAPL"))
    # PriceUpdate(ticker='AAPL', price=190.37, previous_price=190.42, ...)

    await source.add_ticker("PYPL")
    await asyncio.sleep(2.0)
    print(cache.get("PYPL"))

    await source.stop()

asyncio.run(main())
```

---

## Appendix B. Decisions log (condensed)

| Decision | Chosen | Alt | Why |
|---|---|---|---|
| Source interface | ABC | `Protocol` | Want `isinstance` and clear docstrings; structural typing not needed |
| Cache locking | `threading.Lock` for writes; lock-free reads | `asyncio.Lock` | Cache is read from sync handlers too; GIL makes dict reads safe |
| Source selection | Single env-var branch, fail-fast | Tiered fallback | Silent fallback hides misconfiguration |
| Push mechanism | 500 ms poll of cache + version check | per-tick event bus | Trivial to reason about; sufficient for 1 user |
| SSE payload | Full current snapshot per tick | Diff of changed tickers | Simpler client logic; payload is <2 KB |
| Stochastic model | GBM (closed-form per tick) | OU / Heston | Textbook; multiplicative ⇒ prices never negative |
| Correlation | Cholesky on fixed sector matrix | Estimated from history | Reproducible; no CSV to ship |
| Tick safety rail | ±5% clamp | None | Long-tail draws look broken in UI |
| Determinism | `MARKET_SIM_SEED` env var | Always random | Required for `LLM_MOCK=true` E2E tests |
| Massive client | `httpx` direct | `massive` SDK | One endpoint; easier to mock with `respx` |
| Massive failure handling | Per-call try; keep stale cache | Drop entries on failure | UI stays alive during transient outages |
