# FinAlly Playwright E2E

The browser suites validate the cross-layer contracts that unit tests cannot:
static deep links, SSE recovery, US/CN profile behavior, trading, chat,
strategies, run library, arena and developer API keys.

## Full US suite

```bash
cd test
docker compose -f docker-compose.test.yml up --build \
  --abort-on-container-exit --exit-code-from playwright
docker compose -f docker-compose.test.yml down -v
```

## Full CN suite

```bash
cd test
docker compose -f docker-compose.cn.test.yml up --build \
  --abort-on-container-exit --exit-code-from playwright
docker compose -f docker-compose.cn.test.yml down -v
```

Both harnesses use `LLM_MOCK=true`, an ephemeral application database and an
official Playwright image matching `test/package.json`. Container dependencies
live in a named `/tests/node_modules` volume, so Docker never replaces the
host's macOS/Windows dependencies with Linux files.

## PR smoke subsets

CI runs fast representative subsets for both profiles. Run the same gates
locally:

```bash
cd test
E2E_SPECS="specs/fresh-start.spec.ts specs/sse-resilience.spec.ts specs/trade.spec.ts" \
  docker compose -f docker-compose.test.yml up --build \
  --abort-on-container-exit --exit-code-from playwright

E2E_SPECS="specs/cn.spec.ts" \
  docker compose -f docker-compose.cn.test.yml up --build \
  --abort-on-container-exit --exit-code-from playwright
```

Always tear down with the matching `down -v` after a local smoke run.

## Run against an existing local app

```bash
cd test
npm ci
npx playwright install chromium
BASE_URL=http://127.0.0.1:8000 npx playwright test
```

The `fresh-start` project requires a pristine DB. Against a long-lived local
instance, select the `e2e` project and disable dependencies:

```bash
BASE_URL=http://127.0.0.1:8000 npx playwright test --project=e2e --no-deps
```

For a CN instance, set `CN_E2E=1` and point `BASE_URL` to its port.

## State and timing

- Specs use one worker because they share one SQLite instance.
- `fresh-start` is a project dependency and runs before mutating US specs.
- Specs should clean their own positions, orders, keys and strategies so retries
  do not depend on prior attempts.
- The correlation history scenario intentionally waits for completed minute
  bars and belongs in the full nightly suite, not PR smoke. Future work should
  replace this wall-clock dependency with an injected/accelerated clock.

## Failure artifacts

Playwright writes:

- `test-results/` — failure screenshots and first-retry traces;
- `playwright-report/` — HTML report;
- `compose-<market>.log` — CI-captured app/browser container logs.

CI uploads these paths even when a suite fails. Locally, run
`npx playwright show-report` after a non-container run.

## Version lockstep

`@playwright/test` in `package.json` must match the
`mcr.microsoft.com/playwright:v<version>-<distribution>` tag in both Compose
files. Upgrade them in one change and run US plus CN discovery before merging.
