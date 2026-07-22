import { test, expect } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import { ensureSampleHistory } from './history-helpers';

/**
 * AI strategy researcher E2E (D4_RESEARCHER_CONTRACT.md §4, US project).
 *
 * LLM_MOCK deterministic branch (contract §2.4): a chat message containing
 * "research" triggers one ResearchInstruction — ticker AAPL, days=120, three
 * template candidates named "Golden Cross", "RSI Rebound" and
 * "Momentum Breakout". The backend backtests every candidate on the committed
 * sample daily bars, persists each as a DRAFT strategy plus a linked
 * "Research: <name>" run (§2.2), ranks them by the documented robustness
 * score and recommends the top traded candidate. The chat renders a
 * comparison card (§3.2) with per-candidate Deploy buttons; deploy is a
 * separate explicit click that PATCHes the strategy to live — never
 * automatic.
 *
 * SELF-SEEDED: beforeEach syncs the AAPL sample daily bars itself via
 * ensureSampleHistory (offline `sample` source, coverage-checked and
 * throttle-aware), so the spec passes standalone — E2E_SPECS subsets,
 * fresh volumes — without depending on history.spec.ts having run first.
 *
 * Pinned testids (§3.2): research-card, research-candidate, research-deploy,
 * research-recommended, research-deployed.
 *
 * All API fixtures run as the anonymous 'default' user — the same principal
 * the browser session uses (strategies.spec.ts precedent) — so the drafts
 * created by the chat turn are visible to the request fixture and vice versa.
 */

/** Contract §2.4 mock candidate names (EN branch). */
const CANDIDATE_NAMES = ['Golden Cross', 'RSI Rebound', 'Momentum Breakout'];

/** Contract §2.2 step 6: linked runs are labelled "Research: " + name. */
const RESEARCH_LABEL_PREFIX = 'Research: ';

/** Wait for the live price stream (same pattern as the other specs). */
async function waitForConnected(page: Page): Promise<void> {
  await expect(page.getByTestId('connection-status')).toHaveAttribute(
    'data-state',
    'connected',
    { timeout: 20_000 }
  );
}

interface StrategySummary {
  id: string;
  name: string;
  ticker: string;
  status: string;
}

/** GET /api/strategies (default view: everything except archived). */
async function listStrategies(
  request: APIRequestContext
): Promise<StrategySummary[]> {
  const res = await request.get('/api/strategies');
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { strategies: StrategySummary[] }).strategies;
}

interface RunListItem {
  id: string;
  strategy_id: string | null;
  label: string | null;
}

/** GET /api/backtest/runs — list items only (stats, never curves). */
async function listRuns(request: APIRequestContext): Promise<RunListItem[]> {
  const res = await request.get('/api/backtest/runs?limit=200');
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { runs: RunListItem[] }).runs;
}

/**
 * Defensive slate-clearing (also the tail cleanup): archive EVERY
 * non-archived strategy — archive is a legal transition from any state and
 * clears open engine state, so a previously deployed research strategy stops
 * running — and drop every "Research: " labelled run so the exact-count
 * assertions below never see leftovers from retries or earlier specs.
 *
 * Strategies are archived, NOT deleted: the card derives status from
 * GET /api/strategies?status=all (§3.2) — the only list view that returns
 * archived rows — so an archived id resolves to an archived marker with NO
 * research-deploy button, while a deleted id would keep a (disabled) Deploy
 * button. Archiving therefore strips old cards in the persisted chat
 * history of deploy buttons entirely, which is what lets the card locator
 * below tell THIS turn's card apart from retry leftovers (.last() stays as
 * belt and braces for the moment before the strategies list loads).
 */
async function clearResearchState(request: APIRequestContext): Promise<void> {
  for (const strategy of await listStrategies(request)) {
    await request.patch(`/api/strategies/${strategy.id}`, {
      data: { status: 'archived' },
    });
  }
  for (const run of await listRuns(request)) {
    if (run.label?.startsWith(RESEARCH_LABEL_PREFIX)) {
      await request.delete(`/api/backtest/runs/${run.id}`);
    }
  }
}

test.describe('AI strategy researcher (mock mode)', () => {
  test.beforeEach(async ({ request }) => {
    // Self-seed the sample daily bars the research backtests read (header
    // note): idempotent — a no-op when history.spec.ts already synced them.
    await ensureSampleHistory(request, ['AAPL']);
    await clearResearchState(request);
  });

  test('research message renders a ranked card; deploying the recommendation goes live', async ({
    page,
    request,
  }) => {
    await page.goto('/');
    await waitForConnected(page);

    // Contains "research" → the deterministic LLM_MOCK researcher branch,
    // which is checked BEFORE the "strategy" branch (contract §2.4).
    const input = page.getByPlaceholder(/Ask FinAlly/);
    await input.fill('Research momentum strategies for AAPL');
    await input.press('Enter');

    // The chat turn owns a single commit (§2.2), so the three drafts appear
    // atomically once all three history backtests have completed.
    await expect
      .poll(async () => (await listStrategies(request)).length, {
        message: 'research turn should persist exactly three draft strategies',
        timeout: 30_000,
      })
      .toBe(3);

    // Retried runs leave earlier research cards in the persisted history, but
    // beforeEach archived their strategies, so those cards render archived
    // markers instead of Deploy buttons (§3.2 status derivation via the
    // status=all list view — see clearResearchState). The card that carries
    // Deploy buttons is therefore THIS turn's card (.last() as belt and
    // braces — new messages append at the end).
    const card = page
      .getByTestId('research-card')
      .filter({ has: page.getByTestId('research-deploy') })
      .last();
    await expect(card).toBeVisible({ timeout: 15_000 });

    // Header: ticker · days · candidate count (§3.2; mock days=120, §2.4).
    await expect(card).toContainText('AAPL');
    await expect(card).toContainText('120');

    // Three ranked candidate rows with the §2.4 mock names, all completed
    // (deployable), and exactly one recommended badge (§2.2: rank 1 traded).
    const candidates = card.getByTestId('research-candidate');
    await expect(candidates).toHaveCount(3);
    for (const name of CANDIDATE_NAMES) {
      await expect(card).toContainText(name);
    }
    await expect(card.getByTestId('research-deploy')).toHaveCount(3);
    await expect(card.getByTestId('research-recommended')).toHaveCount(1);

    // Deploy the recommended candidate — an explicit user click (§2.2 never
    // deploys during the research turn). The button flips to the deployed
    // marker on PATCH success.
    const recommendedRow = candidates.filter({
      has: page.getByTestId('research-recommended'),
    });
    await expect(recommendedRow).toHaveCount(1);
    await recommendedRow.getByTestId('research-deploy').click();
    await expect(recommendedRow.getByTestId('research-deployed')).toBeVisible({
      timeout: 15_000,
    });
    await expect(card.getByTestId('research-deployed')).toHaveCount(1);

    // Server truth: exactly one live strategy among exactly three
    // non-archived AAPL strategies (the other two candidates stay drafts).
    await expect
      .poll(
        async () =>
          (await listStrategies(request)).filter((s) => s.status === 'live')
            .length,
        { timeout: 15_000 }
      )
      .toBe(1);
    const strategies = await listStrategies(request);
    expect(strategies).toHaveLength(3);
    expect(strategies.filter((s) => s.status === 'draft')).toHaveLength(2);
    expect(strategies.every((s) => s.ticker === 'AAPL')).toBeTruthy();

    // Run library truth: exactly three "Research: <name>" runs — one per
    // candidate — each linked to one of the new strategies (§2.2 step 6).
    const researchRuns = (await listRuns(request)).filter((run) =>
      run.label?.startsWith(RESEARCH_LABEL_PREFIX)
    );
    expect(researchRuns).toHaveLength(3);
    expect(researchRuns.map((run) => run.label).sort()).toEqual(
      CANDIDATE_NAMES.map((name) => `${RESEARCH_LABEL_PREFIX}${name}`).sort()
    );
    const strategyIds = new Set(strategies.map((s) => s.id));
    for (const run of researchRuns) {
      expect(run.strategy_id && strategyIds.has(run.strategy_id)).toBeTruthy();
    }

    // Tidy up: archive the three strategies (stops the deployed one before
    // later specs run) and drop their runs — same helper as beforeEach.
    await clearResearchState(request);
  });
});
