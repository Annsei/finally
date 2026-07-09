import { test, expect } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';

/**
 * A-share (CN) developers page E2E (P3_QUANT_API_CONTRACT.md §8/§10).
 *
 * Runs ONLY in the CN harness: the filename matches the CN project's
 * testMatch (/cn\.spec\.ts/) and is excluded from the default US run by the
 * e2e project's testIgnore — same zero-config split as cn.spec.ts /
 * pages-cn.spec.ts / strategies-cn.spec.ts. Launched via
 * docker-compose.cn.test.yml (FINALLY_MARKET=cn, LLM_MOCK=true).
 *
 * CN acceptance (contract §8 i18n dev.* + §10 双市场):
 *   (a) nav-developers and all four page blocks (key list / create / audit /
 *       quickstart) render with Chinese copy
 *   (b) the CN container mints its own key end-to-end: one-time
 *       dev-key-secret plaintext → list row with the display prefix — which
 *       proves the 8801 container's key store is independent (own DB)
 *
 * API fixtures run as the anonymous 'default' user — the same principal the
 * browser session uses (guests can mint keys, §8).
 */

const CJK = /[一-鿿]/;

/** Key plaintext: `fk_` + token_urlsafe(32) (contract §2). */
const SECRET_RE = /fk_[A-Za-z0-9_-]{20,}/;

/** Wait for the live stream (connection-status pattern from the other specs). */
async function waitForConnected(page: Page): Promise<void> {
  await expect(page.getByTestId('connection-status')).toHaveAttribute(
    'data-state',
    'connected',
    { timeout: 20_000 }
  );
}

interface KeyInfo {
  id: string;
  label: string;
  prefix: string;
}

/** GET /api/keys (cookie path → anonymous 'default'). */
async function listKeys(request: APIRequestContext): Promise<KeyInfo[]> {
  const res = await request.get('/api/keys');
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { keys: KeyInfo[] }).keys;
}

test.describe('A-share (CN) developers page', () => {
  test('(a) developer nav and the four page blocks render in Chinese', async ({
    page,
  }) => {
    await page.goto('/');
    await waitForConnected(page);

    // Nav entry renders with a Chinese label (zh dict nav.developers) and
    // client-side routes to /developers.
    const nav = page.getByTestId('nav-developers');
    await expect(nav).toBeVisible({ timeout: 15_000 });
    await expect(nav).toHaveText(CJK);
    await nav.click();
    await expect(page).toHaveURL(/\/developers\/?(\?.*)?$/);

    // All four contract blocks mount with Chinese copy (dev.* zh keys).
    for (const id of ['dev-keys', 'dev-key-create', 'dev-audit', 'dev-quickstart']) {
      const block = page.getByTestId(id);
      await expect(block).toBeVisible({ timeout: 15_000 });
      await expect(block).toContainText(CJK);
    }

    // Quickstart carries the copy-paste snippets: curl with the Bearer header
    // (contract §8 block 4).
    const quickstart = page.getByTestId('dev-quickstart');
    await expect(quickstart).toContainText('curl');
    await expect(quickstart).toContainText(/Bearer/);
  });

  test('(b) the CN container mints its own key: one-time secret + list row', async ({
    page,
    request,
  }) => {
    await page.goto('/developers/');
    await waitForConnected(page);

    const label = `E2E 中文密钥 ${Date.now()}`;
    const create = page.getByTestId('dev-key-create');
    await expect(create).toBeVisible({ timeout: 15_000 });
    await page.getByTestId('dev-key-label').fill(label);
    await create.click();

    // One-time plaintext reveal (+ copy affordance).
    const secretEl = page.getByTestId('dev-key-secret');
    await expect(secretEl).toBeVisible({ timeout: 15_000 });
    await expect(secretEl).toContainText(SECRET_RE);
    const secret = ((await secretEl.textContent()) ?? '').match(SECRET_RE)![0];
    await expect(page.getByTestId('dev-key-copy')).toBeVisible();

    // The key exists in THIS container's own store (8801 DB independence:
    // a fresh CN boot has no US keys, yet minting works end-to-end) and the
    // list row renders it by its display prefix.
    let created: KeyInfo | undefined;
    await expect
      .poll(
        async () => {
          created = (await listKeys(request)).find((k) => k.label === label);
          return created ? 1 : 0;
        },
        { timeout: 15_000 }
      )
      .toBe(1);
    expect(created!.prefix).toBe(secret.slice(0, 11));
    const row = page.getByTestId(`dev-key-row-${created!.id}`);
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(row).toContainText(created!.prefix);

    // Tidy up (idempotent across retries).
    await request.delete(`/api/keys/${created!.id}`);
  });
});
