# FinAlly security boundary

FinAlly is a simulated trading platform, but its identities, API keys, chat
history and shared arena state are still security-sensitive.

## Trust modes

`local-demo` is the default and is restricted to loopback. It preserves the
low-friction course workflow, including name-only demo identity.

`classroom-server` is a shared deployment. It requires an explicit server auth
secret, an independent administrator token, single-replica acknowledgement and
persistent SQLite storage. Administrative operations and API-key management
must not rely on a display name alone.

In classroom-server, `POST /api/auth/login` requires the configured shared
secret in the JSON `access_code` field and issues an HttpOnly, Secure,
SameSite=Lax cookie. The access code gates server membership; it is not a
full per-user password/recovery system.

Neither mode claims public multi-tenant production security. A public service
requires TLS, a real account recovery/authentication design, abuse controls,
external durable storage and shared coordination.

## Privilege boundaries

- Anonymous Guest may explore the local demo — including minting and using API
  keys (the single-user P3 contract) — but must not mint or use durable API
  keys in shared classroom-server mode.
- Bearer keys act only as their owner and cannot create, unfreeze or widen keys.
- Constrained keys (allowed_tickers / max_order_qty / daily_trade_cap) are
  guarded on the indirect trade paths: chat is denied outright (the LLM can
  execute arbitrary actions, so no payload pre-check exists); rule and strategy
  creation payloads are validated against the key's ticker whitelist and
  quantity limit (fail closed on unparseable or incomplete payloads); PATCH on
  rules/strategies remains pause-only.
- Season reset is an administrator operation because it changes every user.
- Season reset always requires `Idempotency-Key` and the explicit JSON
  confirmation. `X-FinAlly-Admin-Token` is mandatory in classroom-server mode;
  local-demo does not require it, but validates it whenever supplied. Retrying
  the same idempotency key replays the recorded result instead of creating
  another season.
- API audit records store bounded summaries, never raw secrets or full prompts.

## Known limitations

- The classroom `access_code` is a single shared secret that gates server
  membership only: any classmate holding it can log in as (take over) any
  display name, since identity is still name-based. Per-user credentials and
  account recovery are follow-up work; do not treat classroom identities as
  authenticated principals.
- Deferred fills are not counted against `daily_trade_cap`: a rule or strategy
  created through a constrained key executes its trades later from background
  loops, and those fills do not increment the key's daily cap (the cap counts
  successful placements on `POST /api/portfolio/trade` and
  `POST /api/portfolio/orders` only). Ticker and quantity limits ARE enforced
  at rule/strategy creation time.

See the live OpenAPI document at `/api/docs` for request/response schemas and
error responses implemented by the current build.

For the loopback-only local demo, an unset `FINALLY_ADMIN_TOKEN` resolves to
`local-demo-admin` for backwards-compatible manual testing. Shared mode never
uses that fallback and refuses to start without an explicit strong token.

## Secret handling

- `.env` is ignored and must never be committed or attached to CI artifacts.
- `OPENROUTER_API_KEY`, `MASSIVE_API_KEY`, server auth and admin tokens are four
  independent secrets.
- Rotate a key immediately if it appears in logs, screenshots, chat or git
  history. Removing it from the current tree does not remove git history.
- Backups contain the session signing secret and must receive the same access
  controls as the live database.

## Deployment controls

- Keep the Docker host publish address at `127.0.0.1` for local-demo.
- Put classroom-server behind HTTPS and reverse-proxy body/rate limits.
- Run exactly one application replica/worker.
- Monitor `/api/ready`; stale quotes must not be accepted for new trades.
- Back up before schema upgrades and test restore regularly.

## Deferred production work

Postgres, Redis-backed distributed rate limits, background-worker leadership,
formal password/passkey recovery, CSRF review and horizontally scalable market
fan-out are deferred. Until implemented, documentation and startup validation
must continue to reject the unsupported topology.
