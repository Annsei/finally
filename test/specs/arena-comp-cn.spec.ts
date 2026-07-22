import { test, expect } from '@playwright/test';
import { waitForConnected } from './history-helpers';
import {
  createCompetitionViaUI,
  expectCompRow,
  resolveCompetitionByName,
} from './arena-helpers';

/**
 * A-share (CN) competitions + risk analytics E2E
 * (D2_LIVE_ARENA_CONTRACT.md §6).
 *
 * Runs ONLY in the CN harness: the filename matches the CN project's
 * testMatch (/cn\.spec\.ts/) and is excluded from the default US run by the
 * e2e project's testIgnore — same zero-config split as the other *-cn
 * specs. Launched via docker-compose.cn.test.yml (FINALLY_MARKET=cn,
 * LLM_MOCK=true).
 *
 * CN acceptance (contract §6: 中文文案 + ¥):
 *   (a) the /arena competition area carries Chinese copy (arena.comp* zh
 *       keys); a competition created through the Chinese form yields an
 *       A-Z2-9 code, a Chinese row and an expanded board with a return %
 *       column, on a page with ¥-denominated figures (formatMoney under
 *       the CN profile)
 *   (b) the Analytics tab risk cards render the Chinese null state — the
 *       em-dash values with the 同步历史数据 hint (fresh CN DB: no position
 *       and no daily bars → VaR/beta null per §4)
 *
 * ZERO external network: no FINALLY_LIVE_SOURCE, no history sync at all in
 * this file (the null state is the point). No portfolio mutations either,
 * so the CN specs that run later keep their seeded baseline.
 */

const CJK = /[一-鿿]/;

test.describe('A-share (CN) competitions + risk analytics', () => {
  test('(a) the /arena competition area is Chinese and a created comp shows a ranked board with ¥ on page', async ({
    page,
    request,
  }) => {
    await page.goto('/arena/');
    await waitForConnected(page);

    // Create/join affordances carry Chinese copy (arena.comp* zh keys).
    const create = page.getByTestId('comp-create');
    await expect(create).toBeVisible({ timeout: 15_000 });
    await expect(create).toContainText(CJK);
    await expect(page.getByTestId('comp-join')).toContainText(CJK);

    // Create through the Chinese form; the mine-scope list resolves the
    // pinned A-Z2-9 code for the creator.
    const name = `E2E竞赛${Date.now()}`;
    await createCompetitionViaUI(page, name, 2);
    const comp = await resolveCompetitionByName(request, name);
    expect(comp.code ?? '').toMatch(/^[A-Z2-9]{6}$/);
    expect(comp.status).toBe('running');

    // Row (Chinese chrome + countdown) → expanded board with the return %
    // column; the page renders ¥-denominated figures (formatMoney).
    const row = await expectCompRow(page, comp.id);
    await expect(row).toContainText(CJK);
    await expect(page.getByTestId(`comp-countdown-${comp.id}`)).toContainText(
      /\d/,
      { timeout: 15_000 }
    );
    await row.click();
    const board = page.getByTestId(`comp-board-${comp.id}`);
    await expect(board).toBeVisible({ timeout: 15_000 });
    await expect(board).toContainText('%', { timeout: 20_000 });
    await expect(page.locator('body')).toContainText(/¥\s*[\d,]+/, {
      timeout: 15_000,
    });
  });

  test('(b) the Analytics risk cards render the Chinese null state with the sync hint', async ({
    page,
  }) => {
    // Fresh CN DB: no position and no daily bars → VaR/beta null (§4), so
    // the cards show the em-dash and the Chinese "同步历史数据后可用" hint
    // (§5). This file never trades nor syncs, keeping the state null.
    await page.goto('/');
    await waitForConnected(page);
    await page.getByTestId('tab-analytics').click();

    const varCard = page.getByTestId('analytics-var');
    await expect(varCard).toBeVisible({ timeout: 15_000 });
    await expect(varCard).toContainText('—');
    await expect(page.getByTestId('analytics-beta')).toContainText('—');

    const hint = page.getByTestId('analytics-risk-hint');
    await expect(hint).toBeVisible({ timeout: 15_000 });
    await expect(hint).toContainText(CJK);
    await expect(hint).toContainText('同步');
  });
});
