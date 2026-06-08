<!-- refreshed: 2026-06-04 -->
# Concerns

**Analysis Date:** 2026-06-04

## Summary

The codebase is at an early stage: the market data subsystem is complete and well-tested, but ~70% of the planned application (FastAPI app entry point, database, portfolio routes, LLM chat, frontend, Docker) is unimplemented. No critical bugs in existing code. Main concerns are scope gaps, missing infrastructure, and one security item.

---

## Critical

*(No critical bugs in existing code.)*

---

## High Priority

### H1 — No FastAPI App Entry Point

**File:** None yet (`backend/app/main.py` is planned but absent)

**Issue:** The market data subsystem (`backend/app/market/`) is fully implemented but there is no FastAPI application that uses it. There is no `main.py`, no lifespan handler to start/stop the market data source, no route registration, and no static file serving. Running `uvicorn` against this codebase will fail.

**Impact:** The app cannot be started at all until `main.py` is created.

**Fix:** Create `backend/app/main.py` with app lifespan, PriceCache init, market data source start, router registration, static file serving.

---

### H2 — No Database Layer

**File:** None yet (`backend/app/db/` is planned but absent)

**Issue:** All database tables (users_profile, watchlist, positions, trades, portfolio_snapshots, chat_messages) are specified in `planning/PLAN.md` but no schema SQL, no connection utility, and no migration/seed logic exists.

**Impact:** Portfolio, trade history, watchlist persistence, and chat are all blocked on this.

**Fix:** Create `backend/app/db/` with schema, lazy-init connection, and seed data matching `planning/PLAN.md § 7`.

---

### H3 — No REST API Routes

**File:** None yet

**Issue:** All REST endpoints listed in `planning/PLAN.md § 8` are unimplemented:
- `GET /api/portfolio`
- `POST /api/portfolio/trade`
- `GET /api/portfolio/history`
- `GET|POST|DELETE /api/watchlist*`
- `POST /api/chat`
- `GET /api/health`

Only `GET /api/stream/prices` (SSE) exists in `backend/app/market/stream.py`.

**Impact:** Frontend has nothing to call. All trading and chat functionality is blocked.

---

### H4 — No Frontend

**File:** None yet (`frontend/` is missing entirely)

**Issue:** The Next.js frontend described in `planning/PLAN.md § 10` does not exist. No `package.json`, no components, no SSE EventSource consumer, no charts, no trading UI.

**Impact:** No browser interface. The entire user-facing product is absent.

---

## Medium Priority

### M1 — OpenRouter API Key in .env (Potential Exposure Risk)

**File:** `.env` (gitignored, but note in `CLAUDE.md`: "There is an OPENROUTER_API_KEY in the .env file")

**Issue:** The `.env` file contains a real OpenRouter API key. If the key is accidentally committed (e.g., via `git add .`) or leaked through logs, it could be exploited. The file is in `.gitignore` which is correct, but the memory note in `CLAUDE.md` and `planning/PLAN.md` explicitly calls out its presence.

**Risk:** Unauthorized LLM API usage / billing exposure.

**Mitigation:** Key is gitignored. Rotate key if any concern of exposure. Consider using `.env.example` as the committed reference (already planned per PLAN.md).

---

### M2 — PriceCache Has No Persistence

**File:** `backend/app/market/cache.py`

**Issue:** `PriceCache` is purely in-memory. If the server restarts, all price history is lost. The SSE stream starts fresh. Sparkline data on the frontend accumulates from SSE since page load — it's ephemeral by design per `planning/PLAN.md § 2` ("sparklines fill in progressively"). But there is no warm-up period, so clients connecting after a restart see no sparkline history until prices re-accumulate.

**Impact:** Minor UX issue — sparklines are empty after restart. Acceptable per design doc.

---

### M3 — No Docker Infrastructure

**File:** `Dockerfile`, `docker-compose.yml`, `scripts/` — all absent

**Issue:** The single-container deployment described in `planning/PLAN.md § 11` is not built yet. No `Dockerfile`, no start/stop scripts, no `docker-compose.yml`. The app cannot be deployed or shared via Docker.

**Impact:** Users cannot run the app via the intended `docker run` command.

---

### M4 — No LLM Integration

**File:** None yet

**Issue:** The LiteLLM/OpenRouter/Cerebras chat integration described in `planning/PLAN.md § 9` is absent. No `POST /api/chat` route, no structured output schema, no portfolio context loader, no auto-trade execution from LLM response.

**Impact:** The AI trading assistant — the core differentiator of the product — does not exist.

---

### M5 — Massive API Poll Interval Hardcoded

**File:** `backend/app/market/massive_client.py:32`

**Issue:** `poll_interval: float = 15.0` is hardcoded as a default parameter. Changing poll rate for paid API tiers requires code change rather than config. No validation that the provided interval meets Polygon.io tier requirements.

**Severity:** Low — default is conservative (free tier safe), but worth parameterizing via env var for production use.

---

### M6 — No Input Validation on Ticker Symbols

**File:** `backend/app/market/cache.py`, `backend/app/market/simulator.py`

**Issue:** Ticker symbols passed to `cache.update()`, `source.add_ticker()`, etc. are not validated. Arbitrary strings (including empty string, very long strings) are accepted. When REST routes for watchlist are added, this should be validated at the API boundary.

**Severity:** Low risk now (no public API yet), but worth noting before routes are built.

---

## Low Priority

### L1 — No E2E Tests

**File:** `test/` (planned but empty)

**Issue:** No Playwright E2E tests exist. The `test/docker-compose.test.yml` infrastructure described in `planning/PLAN.md § 12` is not created.

**Impact:** No automated full-stack regression testing. Unit + integration tests cover the backend market data layer only.

---

### L2 — No CI Coverage Enforcement

**File:** `.github/workflows/claude.yml`

**Issue:** CI runs exist (GitHub Actions) but there is no coverage threshold check in the test pipeline. Coverage is measured manually but not gated.

---

### L3 — `market_data_demo.py` Is Non-Production Code

**File:** `backend/market_data_demo.py`

**Issue:** The Rich terminal demo script is useful for development but is included at the backend root. It imports `rich` as a runtime dependency (not dev-only). If `rich` is removed from production deps, this breaks; if it stays, it adds a non-production dependency.

**Suggestion:** Move `rich` to optional or dev dependencies once the main app is built.

---

### L4 — `conftest.py` Is Empty

**File:** `backend/tests/conftest.py`

**Issue:** The shared conftest is a placeholder (`"""Pytest configuration and fixtures."""` only). As more test modules are added (database, routes, LLM), shared fixtures (e.g., test DB setup, mock PriceCache, test FastAPI app) should be added here to avoid duplication.

---

## Positive Notes (No Action Required)

- **Market data subsystem is production-quality:** Clean ABC + Strategy pattern, proper async lifecycle, thread-safe cache, 75+ passing tests.
- **No security vulnerabilities in existing code:** No SQL injection surface (no DB yet), no user-facing input processing, no auth bypasses.
- **No circular imports detected** in the market data layer (strict one-direction import chain).
- **Error handling is appropriate:** Background task exceptions are logged and swallowed, preventing loop crashes. Resilience is tested.
- **Dependencies are pinned via lockfile** (`backend/uv.lock`) — reproducible installs.

---

*Concerns analysis: 2026-06-04*
