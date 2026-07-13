import { test, expect } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import { ensureSampleHistory } from './history-helpers';

/**
 * A-share (CN) AI strategy researcher E2E (D4_RESEARCHER_CONTRACT.md §4).
 *
 * SELF-SEEDED: beforeEach syncs the 600519 sample daily bars itself via
 * ensureSampleHistory (offline `sample` source, coverage-checked and
 * throttle-aware), so the spec passes standalone — E2E_SPECS subsets,
 * fresh volumes — without depending on history-cn.spec.ts having run first.
 *
 * Runs ONLY in the CN harness: the filename matches the CN project's
 * testMatch (/cn\.spec\.ts/) and is excluded from the default US run by the
 * e2e project's testIgnore — same zero-config split as the other *-cn specs.
 * Launched via docker-compose.cn.test.yml (FINALLY_MARKET=cn, LLM_MOCK=true).
 *
 * LLM_MOCK zh branch (contract §2.4): a message containing 「研究」 triggers
 * one ResearchInstruction — ticker 600519, days=120, three template
 * candidates named 均线金叉 / RSI 超跌反弹 / 动量突破, each sized
 * cash_pct 20%. All three become drafts with linked "Research: <name>" runs
 * and the chat renders the comparison card with Chinese labels.
 *
 * The recommendation is deterministically NULL here — asserted below as CN
 * behavior, not skipped: research backtests open the profile's seed account
 * (¥100,000 — chat.py mirrors routes/backtest.py), and 20% of it (¥20,000)
 * can never afford one whole 100-share board lot of 600519, which costs
 * ≥ ¥140,000 anywhere in the committed 2023–2026 sample bars (the same
 * "a 600519 history run can never fill" fact history-cn.spec.ts documents).
 * cash_pct sizing floors to whole board lots on CN (backtest.py), so all
 * three candidates complete with zero round trips; §2.2 demotes untraded
 * candidates and pins recommended_strategy_id to null — "an untraded winner
 * is not a recommendation". The card therefore shows three deployable ranked
 * rows WITHOUT a research-recommended badge (§3.2 renders the muted
 * no-recommendation note instead), and deploying the rank-1 row by explicit
 * click still flips it draft → live.
 *
 * Pinned testids (§3.2): research-card, research-candidate, research-deploy,
 * research-recommended, research-deployed.
 */

const CJK = /[一-鿿]/;

/** Contract §2.4 mock candidate names (ZH branch). */
const CANDIDATE_NAMES = ['均线金叉', 'RSI 超跌反弹', '动量突破'];

/** Contract §2.2 step 6: linked runs are labelled "Research: " + name. */
const RESEARCH_LABEL_PREFIX = 'Research: ';

/** Wait for the live stream (connection-status pattern from the other specs). */
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
 * non-archived strategy (legal from any state, clears open engine state) and
 * drop every "Research: " labelled run, so the exact-count assertions below
 * never see leftovers from retries or earlier CN specs.
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

test.describe('A-share (CN) AI strategy researcher (mock mode)', () => {
  test.beforeEach(async ({ request }) => {
    // Self-seed the sample daily bars the research backtests read (header
    // note): idempotent — a no-op when history-cn.spec.ts already synced.
    await ensureSampleHistory(request, ['600519']);
    await clearResearchState(request);
  });

  test('「研究」 message renders the ranked zh card (null recommendation) and deploys rank 1', async ({
    page,
    request,
  }) => {
    await page.goto('/');
    await waitForConnected(page);

    // Contains 「研究」 → the deterministic LLM_MOCK researcher branch, which
    // is checked BEFORE the 「策略」 strategy branch (contract §2.4). The zh
    // chat placeholder still contains the product name, so /FinAlly/ matches
    // both locales (same lookup as strategies-cn.spec.ts).
    const input = page.getByPlaceholder(/FinAlly/);
    await input.fill('帮我研究一下 600519 的策略');
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
    await expect(card).toContainText('600519');
    await expect(card).toContainText('120');

    // Three ranked candidate rows with the §2.4 zh mock names, all completed
    // (deployable) — and ZERO recommended badges: every candidate finished
    // untraded (lot math in the header comment), so §2.2 pins the
    // recommendation to null and §3.2 renders the muted note instead.
    const candidates = card.getByTestId('research-candidate');
    await expect(candidates).toHaveCount(3);
    for (const name of CANDIDATE_NAMES) {
      await expect(card).toContainText(name);
    }
    await expect(card.getByTestId('research-deploy')).toHaveCount(3);
    await expect(card.getByTestId('research-recommended')).toHaveCount(0);

    // Deploy the rank-1 row (§3.2 lists candidates in rank order) — an
    // explicit user click; the button label is Chinese (zh research.deploy)
    // and flips to the deployed marker on PATCH success.
    const rankOne = candidates.first();
    await expect(rankOne.getByTestId('research-deploy')).toHaveText(CJK);
    await rankOne.getByTestId('research-deploy').click();
    await expect(rankOne.getByTestId('research-deployed')).toBeVisible({
      timeout: 15_000,
    });
    await expect(card.getByTestId('research-deployed')).toHaveCount(1);

    // Server truth: exactly one live strategy among exactly three
    // non-archived 600519 strategies (the other two candidates stay drafts).
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
    expect(strategies.every((s) => s.ticker === '600519')).toBeTruthy();

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
    // later CN specs run) and drop their runs — same helper as beforeEach.
    await clearResearchState(request);
  });
});
