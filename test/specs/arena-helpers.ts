import { expect } from '@playwright/test';
import type { APIRequestContext, Locator, Page } from '@playwright/test';
import { waitForConnected } from './history-helpers';

/**
 * Shared fixtures for the D2 competition specs (arena-comp.spec.ts /
 * arena-comp-cn.spec.ts — D2_LIVE_ARENA_CONTRACT.md §3/§5/§6).
 *
 * Not a spec file: the name ends in `-helpers.ts` (history-helpers.ts
 * convention), so neither the US project's /\.spec\.ts/ testMatch nor the
 * CN /cn\.spec\.ts/ testMatch discovers it.
 *
 * All waiting goes through expect/expect.poll or reload-polling — never a
 * bare sleep (the expectAuditRow convention shared by the other specs).
 */

/** GET /api/competitions?scope=… list row (contract §3). `code` is present
 *  only in mine-scope for competitions the caller created. */
export interface CompetitionSummary {
  id: string;
  name: string;
  code?: string | null;
  status: string;
  member_count: number;
  starts_at: string;
  ends_at: string;
}

/** GET /api/competitions/{id} board row (contract §3). */
export interface BoardRow {
  user_id: string;
  name: string;
  baseline_value: number;
  value: number;
  return_pct: number;
  rank: number;
}

/** List competitions in a scope through a request context (contract §3). */
export async function listCompetitions(
  request: APIRequestContext,
  scope: 'mine' | 'all' = 'mine'
): Promise<CompetitionSummary[]> {
  const res = await request.get(`/api/competitions?scope=${scope}`);
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as { competitions: CompetitionSummary[] };
  return body.competitions;
}

/**
 * GET /api/competitions/{id} → the pinned `board` array. The contract pins
 * the board rows but not the detail envelope, so accept the board at the
 * top level or nested under a `competition` wrapper (mirrors the
 * frontend's tolerant parse).
 */
export async function getBoard(
  request: APIRequestContext,
  id: string
): Promise<BoardRow[]> {
  const res = await request.get(`/api/competitions/${id}`);
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as {
    board?: BoardRow[];
    competition?: { board?: BoardRow[] };
  };
  const board = body.board ?? body.competition?.board;
  expect(Array.isArray(board), 'detail response carries a board array').toBe(
    true
  );
  return board!;
}

/**
 * Drive the /arena comp-create flow (contract §5): fill comp-name /
 * comp-hours and submit. The contract pins the comp-create testid but not
 * whether it sits on the form element or on the submit button itself, so
 * this adapts: a button is clicked directly; any other element (form /
 * container) has its submit — or only — button clicked.
 */
export async function createCompetitionViaUI(
  page: Page,
  name: string,
  hours: number
): Promise<void> {
  const create = page.getByTestId('comp-create');
  await expect(create).toBeVisible({ timeout: 15_000 });
  await page.getByTestId('comp-name').fill(name);
  await page.getByTestId('comp-hours').fill(String(hours));
  const tag = await create.evaluate((el) => el.tagName.toLowerCase());
  if (tag === 'button') {
    await create.click();
  } else {
    const submit = create.locator('button[type="submit"]');
    if ((await submit.count()) > 0) {
      await submit.first().click();
    } else {
      await create.locator('button').first().click();
    }
  }
}

/**
 * Resolve a competition created moments ago by its (unique) name through
 * the mine-scope list — polled, because the UI create round-trip is async.
 */
export async function resolveCompetitionByName(
  request: APIRequestContext,
  name: string
): Promise<CompetitionSummary> {
  let found: CompetitionSummary | undefined;
  await expect
    .poll(
      async () => {
        found = (await listCompetitions(request, 'mine')).find(
          (c) => c.name === name
        );
        return found ? 'found' : 'missing';
      },
      {
        timeout: 15_000,
        message: `waiting for competition "${name}" in scope=mine`,
      }
    )
    .toBe('found');
  return found!;
}

/**
 * Wait for comp-row-${id} on an already-loaded /arena page, reloading
 * between attempts — the membership may postdate the list fetch the page
 * booted with (p4 reload-poll convention, no bare sleeps). Returns the row.
 */
export async function expectCompRow(page: Page, id: string): Promise<Locator> {
  const row = page.getByTestId(`comp-row-${id}`);
  const attempts = 3;
  for (let attempt = 1; ; attempt++) {
    try {
      await expect(row).toBeVisible({ timeout: 10_000 });
      return row;
    } catch (err) {
      if (attempt >= attempts) throw err;
      await page.reload();
      await waitForConnected(page);
    }
  }
}
