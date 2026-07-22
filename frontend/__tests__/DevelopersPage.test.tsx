/**
 * DevelopersPage.test.tsx — /developers developer portal (P3 §8).
 *
 * Pure helpers:  parseTickersInput / tickersInputValue (null = unrestricted),
 *                buildConstraints (empty → null, validation errors),
 *                constraintSummary, resultBadgeClass (ok=up, denied/error=down,
 *                rate_limited=amber), curl/python quickstart snippets
 * Rendering:     four contract blocks, key rows (chip, summary, freeze toggle,
 *                two-click revoke, constraint editor with explicit-null PATCH),
 *                create → one-time dev-key-secret + copy (clipboard API +
 *                execCommand fallback), audit badges + `before`-cursor paging,
 *                quickstart origin + Swagger link
 */
import React from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';
import useSWR from 'swr';
import type { ApiAuditEntry, ApiAuditResponse, ApiKeyInfo } from '@/types/market';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
  useSWRConfig: jest.fn().mockReturnValue({ mutate: jest.fn() }),
}));

// AppShell chrome is covered by AppShell.test.tsx — stub it so the page's own
// content renders in isolation.
jest.mock('@/components/AppShell', () => ({
  __esModule: true,
  default: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="app-shell">{children}</div>
  ),
}));

import DevelopersPage, {
  KEYS_KEY,
  parseTickersInput,
  tickersInputValue,
  buildConstraints,
  constraintSummary,
  resultBadgeClass,
  curlSnippet,
  pythonSnippet,
  copyText,
} from '@/pages/developers';
import { makeT } from '@/lib/i18n';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;
const t = makeT('en');

const key = (id: string, over: Partial<ApiKeyInfo> = {}): ApiKeyInfo => ({
  id,
  label: 'trading bot',
  prefix: 'fk_abc12345',
  created_at: '2026-07-08T10:00:00Z',
  last_used_at: null,
  frozen: false,
  allowed_tickers: null,
  max_order_qty: null,
  daily_trade_cap: null,
  ...over,
});

const entry = (id: string, over: Partial<ApiAuditEntry> = {}): ApiAuditEntry => ({
  id,
  method: 'POST',
  endpoint: '/api/portfolio/trade',
  payload_digest: '{"ticker":"NVDA","side":"buy","quantity":1}',
  result: 'ok',
  status_code: 200,
  created_at: '2026-07-08T10:00:00Z',
  ...over,
});

const keysMutate = jest.fn();

function mockData(opts: { keys?: ApiKeyInfo[]; audit?: Record<string, ApiAuditResponse> }) {
  mockUseSWR.mockImplementation(((swrKey: string | null) => {
    if (swrKey === KEYS_KEY) {
      return { data: opts.keys ? { keys: opts.keys } : undefined, mutate: keysMutate };
    }
    if (typeof swrKey === 'string' && opts.audit) {
      const m = swrKey.match(/^\/api\/keys\/([^/]+)\/audit\?limit=50$/);
      if (m && opts.audit[m[1]]) return { data: opts.audit[m[1]], mutate: jest.fn() };
    }
    return { data: undefined, mutate: jest.fn() };
  }) as never);
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------
describe('developers page helpers (P3 §8)', () => {
  it('parseTickersInput: comma list → uppercase deduped array; empty → null', () => {
    expect(parseTickersInput('')).toBeNull();
    expect(parseTickersInput('   ')).toBeNull();
    expect(parseTickersInput(',,,')).toBeNull();
    expect(parseTickersInput('aapl, msft')).toEqual(['AAPL', 'MSFT']);
    expect(parseTickersInput('AAPL,aapl, AAPL ')).toEqual(['AAPL']);
    expect(parseTickersInput('nvda,')).toEqual(['NVDA']);
  });

  it('tickersInputValue is the inverse for prefilling (null → empty string)', () => {
    expect(tickersInputValue(null)).toBe('');
    expect(tickersInputValue(undefined)).toBe('');
    expect(tickersInputValue(['AAPL', 'MSFT'])).toBe('AAPL,MSFT');
    expect(parseTickersInput(tickersInputValue(['AAPL', 'MSFT']))).toEqual(['AAPL', 'MSFT']);
  });

  it('buildConstraints: empty inputs → explicit nulls (= unrestricted)', () => {
    expect(buildConstraints('', '', '')).toEqual({
      ok: true,
      allowed_tickers: null,
      max_order_qty: null,
      daily_trade_cap: null,
    });
    expect(buildConstraints('aapl,msft', '2.5', '10')).toEqual({
      ok: true,
      allowed_tickers: ['AAPL', 'MSFT'],
      max_order_qty: 2.5,
      daily_trade_cap: 10,
    });
  });

  it('buildConstraints: invalid max qty / daily cap surface i18n error keys', () => {
    expect(buildConstraints('', '0', '')).toEqual({ ok: false, errorKey: 'dev.errMaxQty' });
    expect(buildConstraints('', '-1', '')).toEqual({ ok: false, errorKey: 'dev.errMaxQty' });
    expect(buildConstraints('', 'abc', '')).toEqual({ ok: false, errorKey: 'dev.errMaxQty' });
    expect(buildConstraints('', '', '0')).toEqual({ ok: false, errorKey: 'dev.errDailyCap' });
    expect(buildConstraints('', '', '1.5')).toEqual({ ok: false, errorKey: 'dev.errDailyCap' });
    expect(buildConstraints('', '', 'x')).toEqual({ ok: false, errorKey: 'dev.errDailyCap' });
  });

  it('constraintSummary joins guardrails with · and falls back to Unrestricted', () => {
    expect(constraintSummary(key('k1'), t)).toBe('Unrestricted');
    expect(
      constraintSummary(
        key('k1', { allowed_tickers: ['AAPL', 'MSFT'], max_order_qty: 10, daily_trade_cap: 5 }),
        t
      )
    ).toBe('Tickers: AAPL,MSFT · Max qty 10 · 5 trades/day');
    expect(constraintSummary(key('k1', { daily_trade_cap: 3 }), t)).toBe('3 trades/day');
  });

  it('resultBadgeClass maps to the semantic direction classes (P3 §8)', () => {
    expect(resultBadgeClass('ok')).toContain('text-terminal-up');
    expect(resultBadgeClass('denied')).toContain('text-terminal-down');
    expect(resultBadgeClass('error')).toContain('text-terminal-down');
    expect(resultBadgeClass('rate_limited')).toContain('text-terminal-amber');
    expect(resultBadgeClass('whatever')).toContain('text-terminal-muted');
  });

  it('curl/python snippets carry the Bearer header and the given origin', () => {
    const curl = curlSnippet('https://demo.example');
    expect(curl).toContain('https://demo.example/api/portfolio/trade');
    expect(curl).toContain('Authorization: Bearer');
    const py = pythonSnippet('https://demo.example');
    expect(py).toContain('"https://demo.example"');
    expect(py).toContain('FINALLY_API_KEY');
    expect(py).toContain('Bearer');
    expect(py).toContain('/api/portfolio/trade');
  });
});

// ---------------------------------------------------------------------------
// Page rendering
// ---------------------------------------------------------------------------
describe('DevelopersPage (P3 §8)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    mockData({ keys: [] });
  });

  it('renders the four contract blocks: dev-keys, dev-key-create, dev-audit, dev-quickstart', () => {
    mockData({ keys: [key('k1')] });
    render(<DevelopersPage />);
    expect(screen.getByTestId('dev-keys')).toBeInTheDocument();
    expect(screen.getByTestId('dev-key-create')).toBeInTheDocument();
    expect(screen.getByTestId('dev-audit')).toBeInTheDocument();
    expect(screen.getByTestId('dev-quickstart')).toBeInTheDocument();
  });

  it('shows the loading state before data and the i18n empty state after', () => {
    mockData({});
    const { unmount } = render(<DevelopersPage />);
    expect(screen.getByText('Loading keys…')).toBeInTheDocument();
    unmount();

    mockData({ keys: [] });
    render(<DevelopersPage />);
    expect(screen.getByText(/No API keys yet/)).toBeInTheDocument();
  });

  it('key rows show label, prefix, status chip, and the constraint summary', () => {
    mockData({
      keys: [
        key('k1', { allowed_tickers: ['NVDA'], max_order_qty: 5 }),
        key('k2', { label: 'frozen bot', frozen: 1, last_used_at: '2026-07-08T11:00:00Z' }),
      ],
    });
    render(<DevelopersPage />);

    const row1 = screen.getByTestId('dev-key-row-k1');
    expect(row1.textContent).toContain('trading bot');
    expect(row1.textContent).toContain('fk_abc12345');
    expect(row1.textContent).toContain('Tickers: NVDA · Max qty 5');
    expect(row1.textContent).toContain('Never'); // last_used_at null
    expect(screen.getByTestId('dev-key-status-k1').textContent).toBe('Active');
    expect(screen.getByTestId('dev-key-status-k1').className).toContain('text-terminal-up');

    // frozen accepts the SQLite 0/1 encoding and flips the chip + toggle label
    expect(screen.getByTestId('dev-key-status-k2').textContent).toBe('Frozen');
    expect(screen.getByTestId('dev-key-status-k2').className).toContain('text-terminal-amber');
    expect(screen.getByTestId('dev-key-freeze-k2').textContent).toBe('Unfreeze');
  });

  it('freeze is an immediate PATCH toggle (no confirm) + list revalidate', async () => {
    mockData({ keys: [key('k1')] });
    render(<DevelopersPage />);
    await act(async () => {
      fireEvent.click(screen.getByTestId('dev-key-freeze-k1'));
    });
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/keys/k1',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ frozen: true }) })
    );
    expect(keysMutate).toHaveBeenCalled();
  });

  it('unfreeze PATCHes frozen: false for a frozen key', async () => {
    mockData({ keys: [key('k1', { frozen: 1 })] });
    render(<DevelopersPage />);
    await act(async () => {
      fireEvent.click(screen.getByTestId('dev-key-freeze-k1'));
    });
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/keys/k1',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ frozen: false }) })
    );
  });

  it('revoke is a two-click confirm: arm, then DELETE + revalidate', async () => {
    mockData({ keys: [key('k1')] });
    render(<DevelopersPage />);

    const revoke = screen.getByTestId('dev-key-revoke-k1');
    expect(revoke.textContent).toBe('Revoke');
    fireEvent.click(revoke);
    // First click only arms — nothing leaves the client.
    expect(global.fetch).not.toHaveBeenCalled();
    expect(revoke.textContent).toBe('Confirm revoke?');

    await act(async () => {
      fireEvent.click(revoke);
    });
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/keys/k1',
      expect.objectContaining({ method: 'DELETE' })
    );
    expect(keysMutate).toHaveBeenCalled();
  });

  it('edit expands prefilled; clearing every field PATCHes explicit nulls', async () => {
    mockData({
      keys: [key('k1', { allowed_tickers: ['AAPL'], max_order_qty: 5, daily_trade_cap: 3 })],
    });
    render(<DevelopersPage />);

    fireEvent.click(screen.getByTestId('dev-key-edit-k1'));
    const tickers = screen.getByTestId('dev-key-edit-tickers-k1') as HTMLInputElement;
    const maxQty = screen.getByTestId('dev-key-edit-max-qty-k1') as HTMLInputElement;
    const cap = screen.getByTestId('dev-key-edit-cap-k1') as HTMLInputElement;
    expect(tickers.value).toBe('AAPL');
    expect(maxQty.value).toBe('5');
    expect(cap.value).toBe('3');

    fireEvent.change(tickers, { target: { value: '' } });
    fireEvent.change(maxQty, { target: { value: '' } });
    fireEvent.change(cap, { target: { value: '' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('dev-key-save-k1'));
    });
    // null = unrestricted, sent explicitly so the API clears the constraint
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/keys/k1',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ allowed_tickers: null, max_order_qty: null, daily_trade_cap: null }),
      })
    );
    expect(keysMutate).toHaveBeenCalled();
    // A successful save collapses the editor.
    expect(screen.queryByTestId('dev-key-editor-k1')).toBeNull();
  });

  it('edit with values PATCHes normalized constraints', async () => {
    mockData({ keys: [key('k1')] });
    render(<DevelopersPage />);

    fireEvent.click(screen.getByTestId('dev-key-edit-k1'));
    fireEvent.change(screen.getByTestId('dev-key-edit-tickers-k1'), {
      target: { value: 'aapl, msft' },
    });
    fireEvent.change(screen.getByTestId('dev-key-edit-max-qty-k1'), { target: { value: '2.5' } });
    fireEvent.change(screen.getByTestId('dev-key-edit-cap-k1'), { target: { value: '10' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('dev-key-save-k1'));
    });
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/keys/k1',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({
          allowed_tickers: ['AAPL', 'MSFT'],
          max_order_qty: 2.5,
          daily_trade_cap: 10,
        }),
      })
    );
  });

  it('edit validation stays client-side: bad cap shows the error, no PATCH', async () => {
    mockData({ keys: [key('k1')] });
    render(<DevelopersPage />);
    fireEvent.click(screen.getByTestId('dev-key-edit-k1'));
    fireEvent.change(screen.getByTestId('dev-key-edit-cap-k1'), { target: { value: '1.5' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('dev-key-save-k1'));
    });
    expect(global.fetch).not.toHaveBeenCalled();
    expect(screen.getByTestId('dev-key-edit-error-k1').textContent).toContain(
      'Daily trade cap'
    );
  });

  it('create POSTs the label (+ only non-null constraints) and shows the one-time secret', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ key: 'fk_plaintext_secret_42', info: key('k9') }),
    });
    mockData({ keys: [] });
    render(<DevelopersPage />);

    fireEvent.change(screen.getByTestId('dev-key-label'), { target: { value: 'my bot' } });
    fireEvent.change(screen.getByTestId('dev-key-daily-cap'), { target: { value: '5' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('dev-key-create'));
    });

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/keys',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ label: 'my bot', daily_trade_cap: 5 }),
      })
    );
    // One-time plaintext + the shown-only-once warning; list revalidates.
    expect(screen.getByTestId('dev-key-secret').textContent).toBe('fk_plaintext_secret_42');
    expect(screen.getByText(/Shown only once/)).toBeInTheDocument();
    expect(screen.getByTestId('dev-key-copy')).toBeInTheDocument();
    expect(keysMutate).toHaveBeenCalled();
  });

  it('create without a label errors inline and never leaves the client', async () => {
    mockData({ keys: [] });
    render(<DevelopersPage />);
    await act(async () => {
      fireEvent.click(screen.getByTestId('dev-key-create'));
    });
    expect(global.fetch).not.toHaveBeenCalled();
    expect(screen.getByTestId('dev-key-create-error').textContent).toBe(
      'Enter a label (1–40 characters).'
    );
  });

  it('a create failure surfaces the API error inline (no secret panel)', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: async () => ({ error: 'Key limit reached (10)' }),
    });
    mockData({ keys: [] });
    render(<DevelopersPage />);
    fireEvent.change(screen.getByTestId('dev-key-label'), { target: { value: 'one too many' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('dev-key-create'));
    });
    expect(screen.getByTestId('dev-key-create-error').textContent).toBe('Key limit reached (10)');
    expect(screen.queryByTestId('dev-key-secret')).toBeNull();
  });

  describe('secret copy + dismiss', () => {
    const showSecret = async () => {
      (global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => ({ key: 'fk_copy_me_99', info: key('k9') }),
      });
      mockData({ keys: [] });
      render(<DevelopersPage />);
      fireEvent.change(screen.getByTestId('dev-key-label'), { target: { value: 'bot' } });
      await act(async () => {
        fireEvent.click(screen.getByTestId('dev-key-create'));
      });
    };

    afterEach(() => {
      delete (window.navigator as { clipboard?: unknown }).clipboard;
      delete (document as { execCommand?: unknown }).execCommand;
    });

    it('dev-key-copy uses navigator.clipboard and confirms with Copied', async () => {
      const writeText = jest.fn().mockResolvedValue(undefined);
      Object.defineProperty(window.navigator, 'clipboard', {
        value: { writeText },
        configurable: true,
      });
      await showSecret();
      await act(async () => {
        fireEvent.click(screen.getByTestId('dev-key-copy'));
      });
      expect(writeText).toHaveBeenCalledWith('fk_copy_me_99');
      expect(screen.getByTestId('dev-key-copy').textContent).toBe('Copied');
    });

    it('falls back to document.execCommand when the clipboard API is missing', async () => {
      const execCommand = jest.fn().mockReturnValue(true);
      (document as { execCommand?: unknown }).execCommand = execCommand;
      await showSecret();
      await act(async () => {
        fireEvent.click(screen.getByTestId('dev-key-copy'));
      });
      expect(execCommand).toHaveBeenCalledWith('copy');
      expect(screen.getByTestId('dev-key-copy').textContent).toBe('Copied');
    });

    it('an execCommand throw still removes the plaintext textarea from the DOM', async () => {
      // No clipboard API → the hidden-textarea fallback runs; execCommand
      // throwing must not leave the secret-bearing textarea in the DOM.
      (document as { execCommand?: unknown }).execCommand = jest.fn(() => {
        throw new Error('copy blocked');
      });
      await expect(copyText('fk_residue_check')).resolves.toBe(false);
      expect(document.querySelectorAll('textarea')).toHaveLength(0);
    });

    it('missing execCommand is guarded: returns false without touching the DOM', async () => {
      // Neither clipboard nor execCommand exists (afterEach removed both).
      await expect(copyText('fk_no_fallback')).resolves.toBe(false);
      expect(document.querySelectorAll('textarea')).toHaveLength(0);
    });

    it('the copy button surfaces the failure when execCommand throws', async () => {
      (document as { execCommand?: unknown }).execCommand = jest.fn(() => {
        throw new Error('copy blocked');
      });
      await showSecret();
      await act(async () => {
        fireEvent.click(screen.getByTestId('dev-key-copy'));
      });
      expect(screen.getByTestId('dev-key-copy').textContent).toBe('Copy failed');
      expect(document.querySelectorAll('textarea')).toHaveLength(0);
    });

    it('dismiss hides the secret — it lives only in component state', async () => {
      await showSecret();
      expect(screen.getByTestId('dev-key-secret')).toBeInTheDocument();
      fireEvent.click(screen.getByTestId('dev-key-secret-dismiss'));
      expect(screen.queryByTestId('dev-key-secret')).toBeNull();
    });
  });

  it('audit rows render result badges (up/down/amber) and the muted digest', () => {
    mockData({
      keys: [key('k1')],
      audit: {
        k1: {
          entries: [
            entry('e1', { result: 'ok', status_code: 200 }),
            entry('e2', { result: 'denied', status_code: 403 }),
            entry('e3', { result: 'rate_limited', status_code: 429, payload_digest: null }),
          ],
          has_more: false,
        },
      },
    });
    render(<DevelopersPage />);

    expect(screen.getByTestId('dev-audit-result-e1').textContent).toBe('ok');
    expect(screen.getByTestId('dev-audit-result-e1').className).toContain('text-terminal-up');
    expect(screen.getByTestId('dev-audit-result-e2').className).toContain('text-terminal-down');
    expect(screen.getByTestId('dev-audit-result-e3').className).toContain('text-terminal-amber');

    const row1 = screen.getByTestId('dev-audit-row-e1');
    expect(row1.textContent).toContain('POST');
    expect(row1.textContent).toContain('/api/portfolio/trade');
    expect(row1.textContent).toContain('{"ticker":"NVDA"');
    // no more pages → no paging button
    expect(screen.queryByTestId('dev-audit-more')).toBeNull();
  });

  it('dev-audit-more pages older entries via the created_at before cursor', async () => {
    mockData({
      keys: [key('k1')],
      audit: {
        k1: {
          entries: [
            entry('e1', { created_at: '2026-07-08T10:00:00Z' }),
            entry('e2', { created_at: '2026-07-08T09:00:00Z' }),
          ],
          has_more: true,
        },
      },
    });
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        entries: [entry('e3', { created_at: '2026-07-08T08:00:00Z' })],
        has_more: false,
      }),
    });
    render(<DevelopersPage />);

    expect(screen.getByTestId('dev-audit-row-e1')).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByTestId('dev-audit-more'));
    });
    // Cursor = oldest created_at currently shown, URL-encoded.
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/keys/k1/audit?limit=50&before=2026-07-08T09%3A00%3A00Z'
    );
    expect(screen.getByTestId('dev-audit-row-e3')).toBeInTheDocument();
    // Exhausted → the button disappears.
    expect(screen.queryByTestId('dev-audit-more')).toBeNull();
  });

  it('audit shows the empty state for a key with no entries', () => {
    mockData({
      keys: [key('k1')],
      audit: { k1: { entries: [], has_more: false } },
    });
    render(<DevelopersPage />);
    expect(screen.getByTestId('dev-audit').textContent).toContain(
      'No audit entries for this key yet.'
    );
  });

  it('quickstart embeds location.origin in both snippets and links to /api/docs', () => {
    mockData({ keys: [] });
    render(<DevelopersPage />);
    // jsdom origin — proves the snippet resolves the runtime origin post-mount.
    const origin = window.location.origin;
    expect(screen.getByTestId('dev-quickstart-curl').textContent).toContain(
      `${origin}/api/portfolio/trade`
    );
    expect(screen.getByTestId('dev-quickstart-curl').textContent).toContain(
      'Authorization: Bearer'
    );
    expect(screen.getByTestId('dev-quickstart-python').textContent).toContain(`"${origin}"`);
    expect(screen.getByTestId('dev-quickstart-python').textContent).toContain('FINALLY_API_KEY');
    expect(screen.getByTestId('dev-swagger-link').getAttribute('href')).toBe('/api/docs');
    expect(screen.getByTestId('dev-quickstart').textContent).toContain('examples/finally_bot.py');
  });
});
