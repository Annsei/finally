# FinAlly project instructions

Start with `planning/CURRENT.md`; it is the canonical current product and
architecture source. `planning/PLAN.md`, `.planning/**` and
`planning/archive/**` are historical inputs.

FinAlly currently supports one process and one replica with persistent SQLite.
Preserve the default localhost boundary and isolated US/CN volumes. Do not
present deferred Postgres, Redis, worker leadership or horizontal scaling as
implemented.

The active hardening plan is `planning/AUDIT_REMEDIATION_PLAN.md`. Security,
operations and API compatibility contracts live in:

- `planning/SECURITY.md`
- `planning/OPERATIONS.md`
- `planning/API.md`

For changes, run the relevant backend/frontend tests plus the affected US/CN
E2E smoke path. Never commit `.env`, SQLite data, API keys, session cookies or
generated browser reports.
