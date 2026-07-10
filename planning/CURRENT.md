# FinAlly current state

Updated: 2026-07-10  
Status: V2 product surface complete; repository-wide hardening is in progress.

This file is the canonical starting point for contributors and coding agents.
`PLAN.md` is the historical V1 specification. Completed phase plans and
contracts explain why code exists, but they do not override this document or
the current implementation.

## Product today

FinAlly is an AI-assisted paper-trading workstation and classroom arena. It
ships as a modular monolith in one container:

- Next.js static-export frontend served by FastAPI;
- FastAPI REST API and SSE price stream;
- SQLite portfolio, strategy, audit, chat and arena state;
- simulator-first US and A-share market profiles;
- market, symbol, journal, arena, strategy, run-library and developer pages;
- manual/advanced orders, standing rules, declarative strategies and backtests;
- LLM chat actions and deterministic `LLM_MOCK` mode;
- cookie identities for the UI and guarded Bearer keys for external bots.

## Supported runtime boundary

| Mode | Intended use | Network | Persistence | Scale |
|---|---|---|---|---|
| `local-demo` (default) | one trusted developer or course demo | loopback only | SQLite volume | one process/replica |
| `classroom-server` | a shared, controlled classroom instance | explicit operator exposure behind TLS | persistent SQLite disk required | exactly one replica |

Multi-replica deployment is not supported. Price state, rate limiting and
background evaluators are process-local. Postgres, Redis and worker/leader
coordination are deferred production infrastructure, not hidden capabilities.

See [OPERATIONS.md](OPERATIONS.md) and [SECURITY.md](SECURITY.md) before
exposing a server outside the host.

## Architecture boundaries

```text
Browser / external bot
        |
        v
FastAPI routes + API-key gateway
        |
        +-- portfolio / orders / strategies / backtests / arena
        +-- LLM orchestration (LiteLLM -> OpenRouter)
        +-- SSE stream
        |
        +-- SQLite (durable user and trading state)
        +-- PriceCache + market source (process-local market state)
        +-- background loops (orders, rules, strategies, sessions, snapshots)
```

The one-container boundary is intentional for the course product. Do not add a
second API process or replica without first externalizing the process-local
state and electing a single owner for background loops.

## Active hardening work

The repository audit and execution order live in
[AUDIT_REMEDIATION_PLAN.md](AUDIT_REMEDIATION_PLAN.md). Its active themes are:

1. explicit local/server trust modes and protected administrative operations;
2. US/CN financial, quote and session consistency;
3. visible frontend error/accessibility behavior;
4. readiness, backups, CI smoke gates and current documentation.

Deferred items are intentionally explicit: multi-replica infrastructure,
strategy lot-allocation semantics and a dedicated responsive/performance UI
phase. The CI ESLint gate currently covers production source while historical
test mocks and two React 19 advisory rules are migrated to the strict policy.

## Documentation authority

1. `planning/CURRENT.md` — current supported product and architecture.
2. `planning/OPERATIONS.md`, `SECURITY.md`, `API.md` — current operator and
   compatibility contracts.
3. `planning/AUDIT_REMEDIATION_PLAN.md` — current hardening execution status.
4. V2/CN/P1-P4 contract files — completed feature contracts and deviation logs.
5. `planning/PLAN.md`, `.planning/**`, `planning/archive/**` — historical inputs.

When documentation conflicts with running code, verify the implementation and
tests, then update the current documents in the same change.

## Verification entry points

```bash
cd backend && uv sync --extra dev && uv run pytest
cd frontend && npm ci && npm test -- --watchAll=false && npm run build
cd test && docker compose -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from playwright
cd test && docker compose -f docker-compose.cn.test.yml up --build --abort-on-container-exit --exit-code-from playwright
```

Fast PR smoke subsets and report locations are documented in `test/README.md`.
