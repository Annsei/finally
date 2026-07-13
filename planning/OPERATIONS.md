# FinAlly operations

FinAlly currently supports a single process and a single replica backed by a
persistent SQLite volume. These are operational requirements, not tuning
suggestions.

## Local demo

```bash
cp .env.example .env
# Set LLM_MOCK=true for an offline deterministic demo, or add OPENROUTER_API_KEY.
docker compose up --build -d
curl --fail http://127.0.0.1:8000/api/ready
```

Docker Compose binds the US service to `127.0.0.1:8000` by default. Start an
isolated CN service and volume alongside it with:

```bash
docker compose --profile cn up --build -d
curl --fail http://127.0.0.1:8001/api/ready
```

The start scripts also bind to loopback, rebuild through Docker's cache, wait
for readiness, and return non-zero if the container cannot become ready.

## Classroom server

Use a TLS reverse proxy and a persistent local disk. Set at least:

```dotenv
FINALLY_RUNTIME_MODE=classroom-server
FINALLY_HOST=0.0.0.0
FINALLY_BIND_HOST=0.0.0.0
FINALLY_SINGLE_REPLICA=true
FINALLY_SERVER_AUTH_SECRET=<independent-random-secret-at-least-16-characters>
FINALLY_ADMIN_TOKEN=<independent-random-admin-token-at-least-16-characters>
```

Generate independent secrets with a system password manager or, for example,
`openssl rand -hex 32`. Never reuse an OpenRouter/Massive key. Do not expose
port 8000 directly to the internet; terminate HTTPS and apply request limits at
the reverse proxy.

The application refuses unsafe classroom-server settings. It does not support
multiple replicas, multiple uvicorn workers, ephemeral database storage or a
network-shared SQLite file.

## Market data sources

`FINALLY_LIVE_SOURCE` selects the price feed: `auto` (default), `simulator`,
`massive`, `akshare` or `replay`. `auto` preserves the long-standing selection
exactly — `MASSIVE_API_KEY` set picks Massive, otherwise the built-in
simulator. The simulator remains the product default; the other feeds are
explicit opt-ins.

- `massive` requires `MASSIVE_API_KEY` (real US market data).
- `akshare` polls real A-share spot quotes and requires `FINALLY_MARKET=cn`;
  `FINALLY_AKSHARE_POLL_SECONDS` sets its poll cadence (default 15 seconds,
  clamped to 5..120).
- `replay` streams stored `daily_bars` history as accelerated live sessions
  (see the runbook below).
- Misconfiguration — an unknown value, `massive` without a key, `akshare`
  outside the CN profile, or a `replay` window the stored data cannot cover —
  fails startup rather than degrading silently.

Real feeds run a 24/7 session clock, but real quotes freeze once the actual
exchange closes: the feed keeps serving the closing frame, quotes go stale,
and the `FINALLY_QUOTE_MAX_AGE_SECONDS` freshness gate blocks trade execution
until the next real session. This is expected behavior, not an outage —
schedule classroom use inside real market hours, or keep the simulator, which
is always live on its accelerated session clock. AKShare data is
teaching-grade only, not investment-grade, and automated tests and E2E runs
never enable a real feed.

## Market replay mode

`FINALLY_LIVE_SOURCE=replay` replays stored daily bars as live market data:
one historical trading day is compressed into
`FINALLY_REPLAY_SECONDS_PER_DAY` seconds (default 120, clamped 30..600) with
a `FINALLY_REPLAY_BREAK_SECONDS` closed break between days (default 5,
clamped 2..60 — also the CN lunch-break length). Previous closes, day
changes and the CN price-limit bands roll with the REAL historical values;
trading, rules, strategies, competitions and the AI all work unchanged. Only
equity tickers with stored history replay — crypto has no daily bars and is
absent in this mode. There is no runtime toggle: the mode is environment
driven, so the instructor controls it at deployment.

Running a replay competition (for example a historical-window private
contest):

1. Pick the window. `FINALLY_REPLAY_FROM`/`FINALLY_REPLAY_TO` (ISO dates,
   both set) select an explicit window; leaving both empty replays the most
   recent 20 commonly-covered trading days. On a fresh volume, startup
   injects the committed sample series automatically (never the network).
   For a real window (say the March 2020 US circuit-breaker weeks), start
   once in any mode and sync real history first:
   `curl -X POST http://127.0.0.1:8000/api/market/history/sync -H 'Content-Type: application/json' -d '{"source":"yfinance","years":10}'`.
2. Set the env and restart the container: `FINALLY_LIVE_SOURCE=replay`, the
   window, the pace, and `FINALLY_REPLAY_LOOP` (`true` restarts at the first
   day forever; `false` freezes prices when the window ends — the freshness
   gate then blocks further trading).
3. Verify with `curl http://127.0.0.1:8000/api/market/replay` — it reports
   `active`, the window, `current_date`, `day_index`/`total_days`, `loop`,
   `finished` and a `source_hint` (`sample`, a real source, or `mixed`).
   Non-replay deployments return `{"active": false}`.
4. Create a timed private competition sized to the window and share the
   join code with the class. Wall-clock length: US
   `total_days * (seconds_per_day + break_seconds)` seconds; CN adds the
   midday break — `total_days * (seconds_per_day + 2 * break_seconds)`
   (replay reuses `break_seconds` as the four-phase midday pause).

A window the stored data cannot cover (fewer than 2 common trading days
across the default watchlist) fails startup with the current per-ticker
coverage and instructions — sync more history or adjust the window. Replay
sessions settle and roll exactly like simulator sessions, so leaderboards
and seasons behave normally.

## Liveness and readiness

- `GET /api/health` is process liveness.
- `GET /api/ready` is service readiness and checks market dependencies/freshness.

Container orchestration should restart failed liveness probes and remove an
instance from service when readiness returns 503. A closed simulator session is
not itself a failure; a missing required quote is.

## Volumes and market isolation

Compose uses project-scoped `finally-us-data` and `finally-cn-data` volumes.
Different checkouts therefore do not silently share a database, and US/CN
profiles never reuse one volume.

The standalone start scripts retain the legacy `finally-data` default for
backward-compatible local data. Override `FINALLY_VOLUME_NAME` and container
name when running another market or checkout.

## Backup

Keep backups outside the Docker volume. The following produces a consistent
SQLite online backup and copies it to the host:

```bash
mkdir -p backups
docker compose exec -T app python -c "import sqlite3; s=sqlite3.connect('/app/db/finally.db'); d=sqlite3.connect('/app/db/finally.backup.db'); s.backup(d); d.close(); s.close()"
docker compose cp app:/app/db/finally.backup.db ./backups/finally.db
```

For CN, replace `app` with `app-cn`. Encrypt off-host backups if user chat or
trading history is sensitive. A reasonable classroom policy is seven daily and
four weekly backups; the operator owns retention and restore testing.

## Restore

1. Stop the affected service gracefully.
2. Preserve the current DB as a rollback copy.
3. Copy the selected backup into `/app/db/finally.db`.
4. Restore ownership to the image's `app` user.
5. Start one replica and wait for `/api/ready`.

Example for US:

```bash
docker compose stop app
docker compose cp app:/app/db/finally.db ./backups/pre-restore-finally.db
docker compose cp ./backups/finally.db app:/app/db/finally.db
docker compose run --rm --no-deps --user root app chown app:app /app/db/finally.db
docker compose up -d app
curl --fail http://127.0.0.1:8000/api/ready
```

## Upgrade and rollback

Before every release that changes the database:

1. create and verify an off-volume backup;
2. record the current git revision and image digest;
3. rebuild, then start exactly one replica;
4. inspect startup migration logs and readiness;
5. run the US or CN smoke suite appropriate to the volume.

If a migration is not backward-compatible, rolling code back is insufficient;
restore the pre-upgrade DB as well. Automated external migrations and online
multi-replica rollouts are deferred until the Postgres production phase.

## Logs and incident capture

```bash
docker compose logs --since 30m app
docker compose ps
docker inspect --format '{{json .State.Health}}' "$(docker compose ps -q app)"
```

Resolve the container through `docker compose ps -q app` (as above) rather
than hardcoding a name: Compose derives container names from the project name
(directory by default, e.g. `finally-app-1`), so a fixed `finally-app` only
matches specific checkouts or explicit `container_name` settings.

Do not log `.env`, cookies, Bearer keys, admin tokens or raw LLM/API request
bodies. CI stores Playwright reports, traces and Compose logs for failed runs.
