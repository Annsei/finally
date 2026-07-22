# FinAlly API compatibility policy

FinAlly exposes FastAPI's OpenAPI schema at `/api/openapi.json` and Swagger UI
at `/api/docs`. External bots should generate or validate clients against that
schema rather than scraping frontend behavior.

## Version policy

The current unversioned `/api/*` surface is the V1 compatibility line. Within
this line:

- additive response fields and new endpoints are allowed;
- existing field meaning, authentication and successful status codes should
  remain compatible;
- removed/renamed fields, stricter required inputs or semantic changes require
  a deprecation window or a new `/api/v2` surface;
- the FastAPI application version and release notes must change with a public
  API release.

CI exports the fully initialized schema with `scripts/export_openapi.py` and
retains it as an artifact for review/release diffs.
An OpenAPI change is not automatically breaking, but it must be intentional.

## Authentication classes

- Public market/read-only endpoints may be called without credentials where
  documented in OpenAPI.
- Browser UI ownership uses the current cookie session.
- In `classroom-server`, login JSON also requires `access_code`; the server
  secret is never returned to the browser after submission.
- External bots use `Authorization: Bearer <FINALLY_API_KEY>`.
- Key management remains a browser/session privilege; a Bearer key cannot
  alter its own constraints.
- Global administrative operations use the configured administrator boundary,
  not ordinary cookie/Bearer identity.
- `POST /api/season/reset` requires a bounded `Idempotency-Key` and
  `{"confirm": true}` in every mode. `X-FinAlly-Admin-Token` is mandatory in
  classroom-server mode; local-demo does not require it but validates a
  supplied token.

## Reliability contract

- `/api/health` reports liveness; `/api/ready` reports whether dependencies are
  ready for market-sensitive work.
- SSE clients must handle named heartbeat events, reconnect and validate data
  frames before updating local state.
- 4xx responses indicate caller/auth/guardrail failures; 5xx responses are
  server failures and may be retried only when the operation is idempotent.
- Clients must honor 429 and back off.

## Change checklist

Every public API change should include:

1. backend contract tests and authorization tests;
2. updated OpenAPI/version notes;
3. frontend and example-client compatibility checks where affected;
4. US/CN behavior review;
5. migration, backup and rollback notes for durable schema changes.
