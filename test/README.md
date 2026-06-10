# FinAlly E2E Tests

Playwright end-to-end tests covering the PLAN.md §12 scenarios: fresh start,
watchlist add/remove, buy/sell trades, portfolio visualizations, AI chat
(mocked), and SSE reconnection.

## Run with Docker Compose (recommended)

Builds the app image, starts it with `LLM_MOCK=true` and a fresh database,
then runs the suite in the official Playwright container:

```bash
cd test
docker compose -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from playwright
docker compose -f docker-compose.test.yml down
```

## Run locally against a running app

Point the suite at any running instance (default `http://localhost:8000`):

```bash
# Start the app first, with mock LLM for deterministic chat tests, e.g.:
#   docker run --rm -p 8000:8000 -e LLM_MOCK=true -e OPENROUTER_API_KEY=x finally

cd test
npm ci
npx playwright install chromium     # first time only
BASE_URL=http://localhost:8000 npx playwright test
npx playwright show-report
```

## Notes

- `fresh-start.spec.ts` asserts the pristine seeded state ($10,000 cash,
  default 10-ticker watchlist), so it must run against a fresh database (the
  compose harness uses no volume — every run is fresh). Against a long-lived
  local instance, skip it with: `npx playwright test --project=e2e --no-deps`.
- Specs run serially (`workers: 1`) because all tests share one SQLite
  database; each spec cleans up the positions/watchlist entries it creates.
- The `@playwright/test` version in `package.json` is pinned to match the
  `mcr.microsoft.com/playwright:v1.44.0-jammy` image tag in
  `docker-compose.test.yml` — keep both in sync when upgrading.
- Chat tests require the app to run with `LLM_MOCK=true`.
