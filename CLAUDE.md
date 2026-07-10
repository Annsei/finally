# FinAlly project instructions

Read `planning/CURRENT.md` first. It is the current product/architecture truth
source. `planning/PLAN.md`, `.planning/**` and `planning/archive/**` are
historical unless CURRENT explicitly links them for background.

The supported runtime is a single-process, single-replica modular monolith with
persistent SQLite. Do not describe Postgres, Redis, multiple workers or public
production deployment as implemented.

Active hardening scope and status are in `planning/AUDIT_REMEDIATION_PLAN.md`.
Preserve the default localhost boundary, US/CN volume isolation, declarative
strategy safety and existing API/test contracts.

Before changing deployment, security or public API behavior, read:

- `planning/OPERATIONS.md`
- `planning/SECURITY.md`
- `planning/API.md`

Verify backend, frontend and the affected US/CN E2E smoke path. Never commit
`.env`, database files, API keys, cookies or generated browser reports.

@planning/CURRENT.md
