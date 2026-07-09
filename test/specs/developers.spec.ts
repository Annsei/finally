import { test, expect } from '@playwright/test';
import type { APIRequestContext, APIResponse, Page } from '@playwright/test';
import { flattenPosition, getPortfolio } from './helpers';

/**
 * Developer API keys E2E (P3_QUANT_API_CONTRACT.md §8/§10, US project).
 *
 * Covers the contract scenarios:
 *   ① nav-developers → /developers; the UI mints a key: the one-time
 *     dev-key-secret plaintext renders (with dev-key-copy), the key row
 *     appears in dev-keys with its display prefix, and GET /api/keys never
 *     re-echoes anything beyond that prefix (明文只出现一次 invariant)
 *   ② the plaintext taken from dev-key-secret's textContent drives an
 *     Authorization: Bearer market order through the request context → it
 *     fills against the same 'default' portfolio → an ok audit row for
 *     POST /api/portfolio/trade shows in the dev-audit table
 *   ③ dev-key-edit sets max_order_qty → an over-limit Bearer order is 403
 *     with the contract error body → a denied audit row shows in dev-audit
 *   ④ dev-key-freeze flips frozen instantly → the next Bearer request is 403
 *   ⑤ dev-key-revoke needs a second confirming click → the key is deleted,
 *     its row disappears, and Bearer requests are 401 "Invalid API key"
 *   ⑥ GET /api/docs serves the Swagger UI (200)
 *
 * Testid contract (§8): nav-developers, dev-keys, dev-key-row-${id},
 * dev-key-freeze-${id}, dev-key-revoke-${id}, dev-key-edit-${id},
 * dev-key-create, dev-key-secret, dev-key-copy, dev-audit, dev-audit-more,
 * dev-quickstart — plus the page's granular ids (dev-key-label,
 * dev-key-edit-max-qty-${id}, dev-key-save-${id}, dev-audit-select with
 * option value = key id). connection-status comes from the existing
 * frontend contract.
 *
 * All cookie-path fixtures run as the anonymous 'default' user — the same
 * principal the browser session uses (guests can mint keys, §8) — so
 * API-created keys render in the page UI and Bearer keys trade against the
 * portfolio the browser shows.
 */

/** Key plaintext: `fk_` + token_urlsafe(32) (contract §2). */
const SECRET_RE = /fk_[A-Za-z0-9_-]{20,}/;

/** Wait for the live price stream (same pattern as the other specs). */
async function waitForConnected(page: Page): Promise<void> {
  await expect(page.getByTestId('connection-status')).toHaveAttribute(
    'data-state',
    'connected',
    { timeout: 20_000 }
  );
}

/** Pull the fk_ plaintext out of dev-key-secret's textContent. */
function extractSecret(text: string | null): string {
  const match = (text ?? '').match(SECRET_RE);
  expect(match, 'dev-key-secret must contain the fk_ plaintext').toBeTruthy();
  return match![0];
}

/** GET /api/keys list-item shape (contract §6 — no hash, no plaintext). */
interface KeyInfo {
  id: string;
  label: string;
  prefix: string;
  frozen: number | boolean;
  max_order_qty: number | null;
}

/** GET /api/keys (cookie path → anonymous 'default'). */
async function listKeys(request: APIRequestContext): Promise<KeyInfo[]> {
  const res = await request.get('/api/keys');
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { keys: KeyInfo[] }).keys;
}

/**
 * Mint a key through the cookie-path API (test setup). The plaintext comes
 * from the 201 body (the only place it ever exists); the id is resolved via
 * the list endpoint using the unique label — no reliance on the info shape.
 */
async function createKey(
  request: APIRequestContext,
  label: string
): Promise<{ id: string; secret: string }> {
  const res = await request.post('/api/keys', { data: { label } });
  expect(res.status()).toBe(201);
  const { key: secret } = (await res.json()) as { key: string };
  expect(secret).toMatch(new RegExp(`^${SECRET_RE.source}$`));
  const created = (await listKeys(request)).find((k) => k.label === label);
  expect(created).toBeTruthy();
  return { id: created!.id, secret };
}

/** Cleanup: revoke a key (audit rows are retained by contract). */
async function deleteKey(request: APIRequestContext, id: string): Promise<void> {
  await request.delete(`/api/keys/${id}`);
}

function bearer(secret: string): Record<string, string> {
  return { Authorization: `Bearer ${secret}` };
}

/** A Bearer-authenticated market order through the request context. */
function bearerTrade(
  request: APIRequestContext,
  secret: string,
  quantity: number,
  ticker = 'AAPL'
): Promise<APIResponse> {
  return request.post('/api/portfolio/trade', {
    headers: bearer(secret),
    data: { ticker, side: 'buy', quantity },
  });
}

/**
 * Resolve the key created through the UI by its unique label (the creation
 * POST is the browser's, so the API list is the lookup surface).
 */
async function resolveKeyByLabel(
  request: APIRequestContext,
  label: string
): Promise<KeyInfo> {
  let found: KeyInfo | undefined;
  await expect
    .poll(
      async () => {
        found = (await listKeys(request)).find((k) => k.label === label);
        return found ? 1 : 0;
      },
      { timeout: 15_000 }
    )
    .toBe(1);
  return found!;
}

/**
 * Mint a key through the /developers UI and hand back the on-screen secret.
 * The page must already be on /developers/ and hydrated.
 */
async function createKeyViaUi(page: Page, label: string): Promise<string> {
  const create = page.getByTestId('dev-key-create');
  await expect(create).toBeVisible({ timeout: 15_000 });
  await page.getByTestId('dev-key-label').fill(label);
  await create.click();

  const secretEl = page.getByTestId('dev-key-secret');
  await expect(secretEl).toBeVisible({ timeout: 15_000 });
  await expect(secretEl).toContainText(SECRET_RE);
  return extractSecret(await secretEl.textContent());
}

/** Point the dev-audit ledger at one key (option value = key id). */
async function selectAuditKey(page: Page, id: string): Promise<void> {
  await page.getByTestId('dev-audit-select').selectOption(id);
}

/**
 * Assert that key `id`'s audit ledger renders a row for `endpoint` whose
 * newest result badge matches `resultRe`. The audit table fetches once per
 * mount (no refreshInterval, and headless pages never refocus, so SWR never
 * revalidates on its own) — a single-shot assertion races the ledger write.
 * Poll instead: navigate to /developers/, point the table at the key, and
 * retry the web-first assertions across reloads (no bare sleeps, same
 * assert-the-state-change convention as the other helpers).
 */
async function expectAuditRow(
  page: Page,
  id: string,
  resultRe: RegExp,
  endpoint: string
): Promise<void> {
  const audit = page.getByTestId('dev-audit');
  const badge = audit.locator('[data-testid^="dev-audit-result-"]').first();
  const attempts = 4;
  for (let attempt = 1; ; attempt++) {
    await page.goto('/developers/');
    await waitForConnected(page);
    await selectAuditKey(page, id);
    try {
      await expect(audit).toContainText(endpoint, { timeout: 5_000 });
      // Assert the result badge itself (dev-audit-result-* holds exactly the
      // result word) — the table's flattened textContent concatenates cells
      // without separators, so word-boundary regexes can never match there.
      await expect(badge).toHaveText(resultRe, { timeout: 5_000 });
      return;
    } catch (err) {
      if (attempt >= attempts) throw err;
      // Reload-poll: the next iteration remounts the table and refetches.
    }
  }
}

test.describe('developer API keys', () => {
  test('① minting a key in the UI shows the one-time secret and the list row', async ({
    page,
    request,
  }) => {
    await page.goto('/');
    await waitForConnected(page);

    // Nav entry routes to /developers (contract §8).
    await page.getByTestId('nav-developers').click();
    await expect(page).toHaveURL(/\/developers\/?(\?.*)?$/);

    const label = `E2E ui key ${Date.now()}`;
    const secret = await createKeyViaUi(page, label);

    // One-time reveal comes with its copy affordance.
    await expect(page.getByTestId('dev-key-copy')).toBeVisible();

    // The list refreshed with the new key, identified by its display prefix
    // (= the first 11 chars of the plaintext, contract §2).
    const created = await resolveKeyByLabel(request, label);
    expect(created.prefix).toBe(secret.slice(0, 11));
    const row = page.getByTestId(`dev-key-row-${created.id}`);
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(row).toContainText(created.prefix);

    // 明文只出现一次: the list API shows the prefix but never re-echoes any
    // part of the secret beyond it (no plaintext, no hash).
    const listJson = JSON.stringify(
      await (await request.get('/api/keys')).json()
    );
    expect(listJson).toContain(created.prefix);
    expect(listJson).not.toContain(secret.slice(11));

    await deleteKey(request, created.id);
  });

  test('② a Bearer order with the on-screen secret fills and lands an ok audit row', async ({
    page,
    request,
  }) => {
    const TICKER = 'AAPL';
    // Clean slate (retried runs / earlier specs may have left a position).
    await flattenPosition(request, TICKER);

    await page.goto('/developers/');
    await waitForConnected(page);

    // Mint through the UI — the plaintext is taken from dev-key-secret's
    // textContent and reused verbatim as the Authorization header.
    const label = `E2E bearer ${Date.now()}`;
    const secret = await createKeyViaUi(page, label);
    const { id } = await resolveKeyByLabel(request, label);

    const before = await getPortfolio(request);
    const res = await bearerTrade(request, secret, 1, TICKER);
    expect(res.status()).toBe(200);

    // The Bearer identity resolves to the same 'default' principal: the
    // browser-visible portfolio paid for the fill.
    await expect
      .poll(async () => (await getPortfolio(request)).cash, { timeout: 15_000 })
      .toBeLessThan(before.cash);

    // The trade is on the ledger: reload-poll the audit table pointed at
    // this key until the ok row for the trade endpoint renders.
    await expectAuditRow(page, id, /^ok$/i, '/api/portfolio/trade');

    // Tidy up so later specs start flat.
    await flattenPosition(request, TICKER);
    await deleteKey(request, id);
  });

  test('③ max_order_qty guardrail: over-limit Bearer order is 403 and audited as denied', async ({
    page,
    request,
  }) => {
    const { id, secret } = await createKey(request, `E2E qty cap ${Date.now()}`);

    await page.goto('/developers/');
    await waitForConnected(page);
    await expect(page.getByTestId(`dev-key-row-${id}`)).toBeVisible({
      timeout: 15_000,
    });

    // Expand the constraint editor (contract §8: tickers / max qty / daily
    // cap) and set max qty, then save.
    await page.getByTestId(`dev-key-edit-${id}`).click();
    const maxQty = page.getByTestId(`dev-key-edit-max-qty-${id}`);
    await expect(maxQty).toBeVisible({ timeout: 15_000 });
    await maxQty.fill('5');
    await page.getByTestId(`dev-key-save-${id}`).click();

    // The constraint is live server-side before the over-limit order fires
    // (assert the state change, never sleep).
    await expect
      .poll(
        async () =>
          (await listKeys(request)).find((k) => k.id === id)?.max_order_qty ??
          null,
        { timeout: 15_000 }
      )
      .toBe(5);

    // quantity 6 > 5 → guardrail 403 with the contract error body (§4).
    const res = await bearerTrade(request, secret, 6);
    expect(res.status()).toBe(403);
    expect(((await res.json()) as { error: string }).error).toBe(
      'Quantity exceeds key limit'
    );

    // The denial is on the ledger: reload-poll until the denied row renders.
    await expectAuditRow(page, id, /^denied$/i, '/api/portfolio/trade');

    await deleteKey(request, id);
  });

  test('④ freezing a key kills Bearer access immediately (403)', async ({
    page,
    request,
  }) => {
    const { id, secret } = await createKey(request, `E2E freeze ${Date.now()}`);

    // The key authenticates before the freeze (GET is un-audited but goes
    // through the Bearer gateway).
    const pre = await request.get('/api/portfolio/', { headers: bearer(secret) });
    expect(pre.status()).toBe(200);

    await page.goto('/developers/');
    await waitForConnected(page);
    await expect(page.getByTestId(`dev-key-row-${id}`)).toBeVisible({
      timeout: 15_000,
    });

    // 即时切换: one click flips frozen server-side — no save step.
    await page.getByTestId(`dev-key-freeze-${id}`).click();
    await expect
      .poll(
        async () =>
          Boolean((await listKeys(request)).find((k) => k.id === id)?.frozen),
        { timeout: 15_000 }
      )
      .toBe(true);

    // 急停 is immediate: the very next Bearer request is rejected.
    const res = await bearerTrade(request, secret, 1);
    expect(res.status()).toBe(403);

    await deleteKey(request, id);
  });

  test('⑤ revoking a key needs a second confirming click, then Bearer is 401', async ({
    page,
    request,
  }) => {
    const { id, secret } = await createKey(request, `E2E revoke ${Date.now()}`);

    const pre = await request.get('/api/portfolio/', { headers: bearer(secret) });
    expect(pre.status()).toBe(200);

    await page.goto('/developers/');
    await waitForConnected(page);
    const revoke = page.getByTestId(`dev-key-revoke-${id}`);
    await expect(revoke).toBeVisible({ timeout: 15_000 });

    // 二次确认: the first click only arms the confirmation — the key must
    // still exist server-side (soft-gate pattern, same as strategy deploy).
    await revoke.click();
    expect((await listKeys(request)).some((k) => k.id === id)).toBe(true);

    // The second click confirms: the key is deleted and its row disappears.
    await revoke.click();
    await expect
      .poll(async () => (await listKeys(request)).some((k) => k.id === id), {
        timeout: 15_000,
      })
      .toBe(false);
    await expect(page.getByTestId(`dev-key-row-${id}`)).toHaveCount(0, {
      timeout: 15_000,
    });

    // The revoked plaintext is now an unknown key (contract §2).
    const res = await bearerTrade(request, secret, 1);
    expect(res.status()).toBe(401);
    expect(((await res.json()) as { error: string }).error).toBe(
      'Invalid API key'
    );
  });

  test('⑥ GET /api/docs serves the interactive API docs', async ({ request }) => {
    const res = await request.get('/api/docs');
    expect(res.status()).toBe(200);
    // FastAPI's docs_url renders the Swagger UI page (contract §7).
    expect((await res.text()).toLowerCase()).toContain('swagger');
  });
});
