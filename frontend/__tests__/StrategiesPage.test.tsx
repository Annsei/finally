/**
 * StrategiesPage.test.tsx — /strategies strategy center (P2 §8).
 *
 * Pure helpers:  defaultRow / rowsToGroup / groupToRows (builder ↔ payload),
 *                validateStrategyForm gate matrix
 * Rendering:     six template cards (i18n copy), template click → form
 *                prefill, condition-row add/remove (≤5), POST payload on
 *                submit, client validation blocking the fetch, list rows
 *                (status chip, P&L direction colour, lifecycle toggle PATCH),
 *                cn ¥ formatting
 */
import React from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';
import useSWR from 'swr';
import type { Strategy, StrategyTemplate } from '@/types/market';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
  useSWRConfig: jest.fn().mockReturnValue({ mutate: jest.fn() }),
}));

jest.mock('next/compat/router', () => ({
  __esModule: true,
  useRouter: jest.fn(),
}));

// AppShell chrome is covered by AppShell.test.tsx — stub it so the page's own
// content renders in isolation (MarketPage.test.tsx recipe).
jest.mock('@/components/AppShell', () => ({
  __esModule: true,
  default: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="app-shell">{children}</div>
  ),
}));

import StrategiesPage, {
  FIELD_SPECS,
  MAX_CONDITIONS,
  defaultRow,
  rowsToGroup,
  groupToRows,
  validateStrategyForm,
} from '@/pages/strategies';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

const TEMPLATES: StrategyTemplate[] = [
  {
    key: 'dip_buyer',
    ticker_hint: null,
    entry: { all: [{ field: 'day_change_pct', op: 'below', value: -3 }] },
    exits: { take_profit_pct: 4, stop_loss_pct: 3 },
    sizing: { mode: 'cash_pct', pct: 20 },
  },
  {
    key: 'momentum_breakout',
    ticker_hint: null,
    entry: { all: [{ field: 'window_high', op: 'above', params: { minutes: 60 } }] },
    exits: { trailing_stop_pct: 2.5, stop_loss_pct: 3 },
    sizing: { mode: 'cash_pct', pct: 20 },
  },
  {
    key: 'ma_golden_cross',
    ticker_hint: null,
    entry: { all: [{ field: 'ma_cross', op: 'above', params: { fast: 5, slow: 20 } }] },
    exits: { take_profit_pct: 5, stop_loss_pct: 3 },
    sizing: { mode: 'cash_pct', pct: 25 },
  },
  {
    key: 'grid_lite',
    ticker_hint: null,
    entry: {
      all: [{ field: 'pullback_from_high_pct', op: 'above', value: 2, params: { minutes: 60 } }],
    },
    exits: { take_profit_pct: 2, stop_loss_pct: 6 },
    sizing: { mode: 'cash_pct', pct: 15 },
  },
  {
    key: 'rsi_rebound',
    ticker_hint: null,
    entry: { all: [{ field: 'rsi', op: 'below', value: 30, params: { period: 14 } }] },
    exits: { take_profit_pct: 4, stop_loss_pct: 3 },
    sizing: { mode: 'cash_pct', pct: 20 },
  },
  {
    key: 'trend_rider',
    ticker_hint: null,
    entry: {
      all: [
        { field: 'ma', op: 'above', value: 0, params: { period: 30 } },
        { field: 'day_change_pct', op: 'above', value: 0.5 },
      ],
    },
    exits: { trailing_stop_pct: 3 },
    sizing: { mode: 'cash_pct', pct: 25 },
  },
];

const strategy = (id: string, over: Partial<Strategy> = {}): Strategy => ({
  id,
  name: `Strat ${id}`,
  ticker: 'NVDA',
  status: 'draft',
  entry: { all: [{ field: 'day_change_pct', op: 'below', value: -2 }] },
  exits: { stop_loss_pct: 3 },
  sizing: { mode: 'fixed_qty', qty: 1 },
  template: null,
  created_at: '2026-07-07T00:00:00Z',
  deployed_at: null,
  open_qty: 0,
  open_price: null,
  opened_at: null,
  entered_count: 0,
  exited_count: 0,
  last_fired_at: null,
  runs_count: 0,
  realized_pnl: 0,
  ...over,
});

const listMutate = jest.fn();

function mockData(opts: {
  strategies?: Strategy[];
  templates?: StrategyTemplate[];
  profile?: Record<string, unknown>;
}) {
  mockUseSWR.mockImplementation(((key: string) => {
    if (key === '/api/strategies') {
      return { data: { strategies: opts.strategies ?? [] }, mutate: listMutate };
    }
    if (key === '/api/strategies/templates') {
      return { data: { templates: opts.templates ?? TEMPLATES }, mutate: jest.fn() };
    }
    if (key === '/api/market/profile' && opts.profile) {
      return { data: opts.profile, mutate: jest.fn() };
    }
    return { data: undefined, mutate: jest.fn() };
  }) as never);
}

const CN_PROFILE = {
  market: 'cn',
  currency_symbol: '¥',
  locale: 'zh-CN',
  lot_size: 100,
  up_is_red: true,
  names: { '600519': '贵州茅台' },
  price_limit_pct: {},
};

describe('strategies page helpers (P2 §8)', () => {
  it('defaultRow seeds registry defaults per field', () => {
    const day = defaultRow('day_change_pct');
    expect(day).toEqual({ field: 'day_change_pct', op: 'below', value: '-2', params: {} });

    const cross = defaultRow('ma_cross');
    expect(cross.op).toBe('above');
    expect(cross.params).toEqual({ fast: '5', slow: '20' });
    expect(cross.value).toBe('');

    // window_low is a breakdown — its op is locked to below
    expect(defaultRow('window_low').op).toBe('below');
    expect(FIELD_SPECS.window_low.lockedOp).toBe('below');
    expect(FIELD_SPECS.window_high.lockedOp).toBe('above');
  });

  it('rowsToGroup builds the declarative payload: mode key, values, numeric params', () => {
    expect(
      rowsToGroup('all', [
        { field: 'price', op: 'above', value: '150', params: {} },
        { field: 'rsi', op: 'below', value: '30', params: { period: '14' } },
      ])
    ).toEqual({
      all: [
        { field: 'price', op: 'above', value: 150 },
        { field: 'rsi', op: 'below', value: 30, params: { period: 14 } },
      ],
    });

    // any-mode; value omitted for none-value fields even if left in state
    expect(
      rowsToGroup('any', [{ field: 'ma_cross', op: 'above', value: '', params: { fast: '5', slow: '20' } }])
    ).toEqual({ any: [{ field: 'ma_cross', op: 'above', params: { fast: 5, slow: 20 } }] });
  });

  it('groupToRows round-trips template entries and backfills param defaults', () => {
    const { mode, rows } = groupToRows(TEMPLATES[0].entry);
    expect(mode).toBe('all');
    expect(rows).toEqual([{ field: 'day_change_pct', op: 'below', value: '-3', params: {} }]);

    // missing params fall back to the registry defaults
    const { rows: rsiRows } = groupToRows({ all: [{ field: 'rsi', op: 'below', value: 25 }] });
    expect(rsiRows[0].params).toEqual({ period: '14' });

    // empty group degrades to a single default row
    expect(groupToRows({ all: [] }).rows).toHaveLength(1);
  });

  it('validateStrategyForm gates name / ticker / required values', () => {
    const rows = [defaultRow('day_change_pct')];
    expect(validateStrategyForm('', 'NVDA', rows)).toBe('strategy.errName');
    expect(validateStrategyForm('x'.repeat(41), 'NVDA', rows)).toBe('strategy.errName');
    expect(validateStrategyForm('ok', '', rows)).toBe('strategy.errTicker');
    expect(
      validateStrategyForm('ok', 'NVDA', [{ field: 'price', op: 'above', value: '', params: {} }])
    ).toBe('strategy.errValue');
    expect(validateStrategyForm('ok', 'NVDA', rows)).toBeNull();
    // ma's value is optional — empty is fine, junk is not
    expect(
      validateStrategyForm('ok', 'NVDA', [{ field: 'ma', op: 'above', value: '', params: { period: '20' } }])
    ).toBeNull();
  });

  it('validateStrategyForm enforces per-field value ranges (backend mirror)', () => {
    const priceRow = (value: string) => [{ field: 'price', op: 'above' as const, value, params: {} }];
    // price is required_positive: 0 and negatives are backend 400s
    expect(validateStrategyForm('ok', 'NVDA', priceRow('0'))).toBe('strategy.errValueRange');
    expect(validateStrategyForm('ok', 'NVDA', priceRow('-5'))).toBe('strategy.errValueRange');
    expect(validateStrategyForm('ok', 'NVDA', priceRow('150'))).toBeNull();
    // rsi is required_0_100 (inclusive bounds)
    const rsiRow = (value: string) => [
      { field: 'rsi', op: 'below' as const, value, params: { period: '14' } },
    ];
    expect(validateStrategyForm('ok', 'NVDA', rsiRow('101'))).toBe('strategy.errValueRange');
    expect(validateStrategyForm('ok', 'NVDA', rsiRow('-1'))).toBe('strategy.errValueRange');
    expect(validateStrategyForm('ok', 'NVDA', rsiRow('0'))).toBeNull();
    expect(validateStrategyForm('ok', 'NVDA', rsiRow('100'))).toBeNull();
    // pullback is required_positive
    const pullbackRow = (value: string) => [
      { field: 'pullback_from_high_pct', op: 'above' as const, value, params: { minutes: '60' } },
    ];
    expect(validateStrategyForm('ok', 'NVDA', pullbackRow('0'))).toBe('strategy.errValueRange');
    expect(validateStrategyForm('ok', 'NVDA', pullbackRow('2'))).toBeNull();
    // day_change_pct has no range — negatives are the whole point
    expect(
      validateStrategyForm('ok', 'NVDA', [
        { field: 'day_change_pct', op: 'below', value: '-3', params: {} },
      ])
    ).toBeNull();
  });

  it('validateStrategyForm rejects inverted fast/slow on the cross fields', () => {
    const cross = (field: string, fast: string, slow: string) => [
      { field, op: 'above' as const, value: '', params: { fast, slow } },
    ];
    // both inside [2, 120] but inverted — exactly the backend-400 pair
    expect(validateStrategyForm('ok', 'NVDA', cross('ma_cross', '30', '20'))).toBe(
      'strategy.errFastSlow'
    );
    expect(validateStrategyForm('ok', 'NVDA', cross('ema_cross', '20', '20'))).toBe(
      'strategy.errFastSlow'
    );
    expect(validateStrategyForm('ok', 'NVDA', cross('ma_cross', '5', '20'))).toBeNull();
    // the gate sees the submitted (defaulted) params: empty fast falls back
    // to 5 — fine against slow 20, inverted against slow 3
    expect(validateStrategyForm('ok', 'NVDA', cross('ema_cross', '', '20'))).toBeNull();
    expect(validateStrategyForm('ok', 'NVDA', cross('ema_cross', '', '3'))).toBe(
      'strategy.errFastSlow'
    );
  });

  it('validateStrategyForm gates exits: > 0 and max days an integer 1..120', () => {
    const rows = [defaultRow('day_change_pct')];
    const exits = (over: Partial<Record<string, string>> = {}) => ({
      takeProfit: '',
      stopLoss: '',
      trailing: '',
      maxDays: '',
      ...over,
    });
    // all-empty exits are legal (each exit param is optional)
    expect(validateStrategyForm('ok', 'NVDA', rows, exits())).toBeNull();
    expect(validateStrategyForm('ok', 'NVDA', rows, exits({ takeProfit: '0' }))).toBe(
      'strategy.errExits'
    );
    expect(validateStrategyForm('ok', 'NVDA', rows, exits({ stopLoss: '-3' }))).toBe(
      'strategy.errExits'
    );
    expect(validateStrategyForm('ok', 'NVDA', rows, exits({ trailing: '2.5' }))).toBeNull();
    expect(validateStrategyForm('ok', 'NVDA', rows, exits({ maxDays: '0' }))).toBe(
      'strategy.errExits'
    );
    expect(validateStrategyForm('ok', 'NVDA', rows, exits({ maxDays: '121' }))).toBe(
      'strategy.errExits'
    );
    expect(validateStrategyForm('ok', 'NVDA', rows, exits({ maxDays: '2.5' }))).toBe(
      'strategy.errExits'
    );
    expect(validateStrategyForm('ok', 'NVDA', rows, exits({ maxDays: '120' }))).toBeNull();
  });

  it('validateStrategyForm gates sizing — an empty input must not pass as 0', () => {
    const rows = [defaultRow('day_change_pct')];
    const noExits = { takeProfit: '', stopLoss: '', trailing: '', maxDays: '' };
    const fixed = (qty: string) => ({ mode: 'fixed_qty' as const, qty, pct: '20' });
    const pct = (value: string) => ({ mode: 'cash_pct' as const, qty: '1', pct: value });
    // Number('') === 0 — the cleared-input case the gate exists for
    expect(validateStrategyForm('ok', 'NVDA', rows, noExits, fixed(''))).toBe('strategy.errSizing');
    expect(validateStrategyForm('ok', 'NVDA', rows, noExits, fixed('0'))).toBe('strategy.errSizing');
    expect(validateStrategyForm('ok', 'NVDA', rows, noExits, fixed('-1'))).toBe('strategy.errSizing');
    expect(validateStrategyForm('ok', 'NVDA', rows, noExits, fixed('0.5'))).toBeNull();
    expect(validateStrategyForm('ok', 'NVDA', rows, noExits, pct(''))).toBe('strategy.errSizing');
    expect(validateStrategyForm('ok', 'NVDA', rows, noExits, pct('0'))).toBe('strategy.errSizing');
    expect(validateStrategyForm('ok', 'NVDA', rows, noExits, pct('101'))).toBe('strategy.errSizing');
    expect(validateStrategyForm('ok', 'NVDA', rows, noExits, pct('1'))).toBeNull();
    expect(validateStrategyForm('ok', 'NVDA', rows, noExits, pct('100'))).toBeNull();
  });
});

describe('StrategiesPage (P2 §8)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    mockData({});
  });

  it('renders the six template cards with i18n names and descriptions', () => {
    render(<StrategiesPage />);
    for (const tpl of TEMPLATES) {
      expect(screen.getByTestId(`template-card-${tpl.key}`)).toBeInTheDocument();
    }
    const dip = screen.getByTestId('template-card-dip_buyer');
    expect(dip.textContent).toContain('Dip Buyer');
    expect(dip.textContent).toContain('day change ≤ −3%');
  });

  it('clicking a template card prefills the builder form', () => {
    render(<StrategiesPage />);
    fireEvent.click(screen.getByTestId('template-card-dip_buyer'));

    expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('Dip Buyer');
    expect((screen.getByTestId('condition-field-0') as HTMLSelectElement).value).toBe(
      'day_change_pct'
    );
    expect((screen.getByTestId('condition-value-0') as HTMLInputElement).value).toBe('-3');
    expect((screen.getByLabelText('TP %') as HTMLInputElement).value).toBe('4');
    expect((screen.getByLabelText('SL %') as HTMLInputElement).value).toBe('3');
    // dip_buyer sizes by cash percentage → the pct input shows 20
    expect((screen.getByTestId('sizing-pct') as HTMLInputElement).value).toBe('20');
    // template select reflects the chosen key
    expect((screen.getByTestId('strategy-template-select') as HTMLSelectElement).value).toBe(
      'dip_buyer'
    );
  });

  it('condition builder adds rows up to five and removes them again', () => {
    render(<StrategiesPage />);
    expect(screen.getByTestId('condition-row-0')).toBeInTheDocument();

    for (let i = 1; i < MAX_CONDITIONS; i++) {
      fireEvent.click(screen.getByTestId('condition-add'));
    }
    expect(screen.getByTestId(`condition-row-${MAX_CONDITIONS - 1}`)).toBeInTheDocument();
    // at the cap the add affordance disappears
    expect(screen.queryByTestId('condition-add')).toBeNull();

    fireEvent.click(screen.getByTestId(`condition-remove-${MAX_CONDITIONS - 1}`));
    expect(screen.queryByTestId(`condition-row-${MAX_CONDITIONS - 1}`)).toBeNull();
    expect(screen.getByTestId('condition-add')).toBeInTheDocument();
  });

  it('changing a row field resets it to that field registry defaults', () => {
    render(<StrategiesPage />);
    fireEvent.change(screen.getByTestId('condition-field-0'), { target: { value: 'rsi' } });
    expect((screen.getByTestId('condition-value-0') as HTMLInputElement).value).toBe('30');
    expect((screen.getByTestId('condition-param-period-0') as HTMLInputElement).value).toBe('14');
    // op select is enabled for rsi…
    expect((screen.getByTestId('condition-op-0') as HTMLSelectElement).disabled).toBe(false);
    // …but locked for window_low (breakdown is below-only per the registry)
    fireEvent.change(screen.getByTestId('condition-field-0'), { target: { value: 'window_low' } });
    const op = screen.getByTestId('condition-op-0') as HTMLSelectElement;
    expect(op.value).toBe('below');
    expect(op.disabled).toBe(true);
  });

  it('submit POSTs /api/strategies with the assembled payload and revalidates the list', async () => {
    render(<StrategiesPage />);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'My Strat' } });
    fireEvent.change(screen.getByLabelText('Ticker'), { target: { value: 'nvda' } });

    await act(async () => {
      fireEvent.click(screen.getByTestId('strategy-create'));
    });

    expect(global.fetch).toHaveBeenCalledTimes(1);
    const [url, init] = (global.fetch as jest.Mock).mock.calls[0];
    expect(url).toBe('/api/strategies');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({
      name: 'My Strat',
      ticker: 'NVDA',
      entry: { all: [{ field: 'day_change_pct', op: 'below', value: -2 }] },
      exits: {
        take_profit_pct: null,
        stop_loss_pct: null,
        trailing_stop_pct: null,
        max_holding_days: null,
      },
      sizing: { mode: 'fixed_qty', qty: 1 },
    });
    expect(listMutate).toHaveBeenCalled();
  });

  it('template-prefililed submit carries the template key and cash_pct sizing', async () => {
    render(<StrategiesPage />);
    fireEvent.click(screen.getByTestId('template-card-dip_buyer'));
    fireEvent.change(screen.getByLabelText('Ticker'), { target: { value: 'AAPL' } });

    await act(async () => {
      fireEvent.click(screen.getByTestId('strategy-create'));
    });

    const body = JSON.parse((global.fetch as jest.Mock).mock.calls[0][1].body);
    expect(body.template).toBe('dip_buyer');
    expect(body.sizing).toEqual({ mode: 'cash_pct', pct: 20 });
    expect(body.exits).toEqual({
      take_profit_pct: 4,
      stop_loss_pct: 3,
      trailing_stop_pct: null,
      max_holding_days: null,
    });
  });

  it('client validation blocks the POST and surfaces the i18n error', async () => {
    render(<StrategiesPage />);
    await act(async () => {
      fireEvent.click(screen.getByTestId('strategy-create'));
    });
    expect(global.fetch).not.toHaveBeenCalled();
    expect(screen.getByTestId('strategy-form-error').textContent).toBe(
      'Enter a name (1–40 characters).'
    );
  });

  it('a cleared sizing qty blocks the POST via strategy-form-error (not a 0-qty payload)', async () => {
    render(<StrategiesPage />);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'My Strat' } });
    fireEvent.change(screen.getByLabelText('Ticker'), { target: { value: 'NVDA' } });
    fireEvent.change(screen.getByTestId('sizing-qty'), { target: { value: '' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('strategy-create'));
    });
    expect(global.fetch).not.toHaveBeenCalled();
    expect(screen.getByTestId('strategy-form-error').textContent).toBe(
      'Enter a valid size: quantity > 0, or 1–100% of cash.'
    );
  });

  it('an out-of-range max holding days blocks the POST via strategy-form-error', async () => {
    render(<StrategiesPage />);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'My Strat' } });
    fireEvent.change(screen.getByLabelText('Ticker'), { target: { value: 'NVDA' } });
    fireEvent.change(screen.getByLabelText('Max days'), { target: { value: '121' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('strategy-create'));
    });
    expect(global.fetch).not.toHaveBeenCalled();
    expect(screen.getByTestId('strategy-form-error').textContent).toBe(
      'Exit values must be greater than 0; max days must be a whole number between 1 and 120.'
    );
  });

  it('a server 400 error body surfaces in the form error', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: async () => ({ error: 'Unknown ticker ZZZZ' }),
    });
    render(<StrategiesPage />);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Bad' } });
    fireEvent.change(screen.getByLabelText('Ticker'), { target: { value: 'ZZZZ' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('strategy-create'));
    });
    expect(screen.getByTestId('strategy-form-error').textContent).toBe('Unknown ticker ZZZZ');
  });

  it('lists strategies: row, status chip, P&L direction colour, runs count, details link', () => {
    mockData({
      strategies: [
        strategy('s1', { realized_pnl: -12.5, runs_count: 3 }),
        strategy('s2', { status: 'live', realized_pnl: 40 }),
      ],
    });
    render(<StrategiesPage />);

    const row = screen.getByTestId('strategy-row-s1');
    expect(row.textContent).toContain('Strat s1');
    expect(screen.getByTestId('strategy-status-s1').textContent).toBe('Draft');
    expect(row.textContent).toContain('-$12.50');
    expect(row.querySelector('.text-terminal-down')).toBeTruthy();
    expect(row.textContent).toContain('3');
    expect(screen.getByTestId('strategy-details-s1')).toBeInTheDocument();
    // ticker cell routes through the canonical SymbolLink
    expect(screen.getAllByTestId('symbol-link-NVDA').length).toBeGreaterThanOrEqual(1);

    expect(screen.getByTestId('strategy-status-s2').textContent).toBe('Live');
    const row2 = screen.getByTestId('strategy-row-s2');
    expect(row2.textContent).toContain('+$40.00');
    expect(row2.querySelector('.text-terminal-up')).toBeTruthy();
  });

  it('lifecycle toggle PATCHes: draft → live (Deploy), live → paused (Pause)', async () => {
    mockData({
      strategies: [strategy('s1'), strategy('s2', { status: 'live' })],
    });
    render(<StrategiesPage />);

    expect(screen.getByTestId('strategy-toggle-s1').textContent).toBe('Deploy');
    await act(async () => {
      fireEvent.click(screen.getByTestId('strategy-toggle-s1'));
    });
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/strategies/s1',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ status: 'live' }) })
    );

    expect(screen.getByTestId('strategy-toggle-s2').textContent).toBe('Pause');
    await act(async () => {
      fireEvent.click(screen.getByTestId('strategy-toggle-s2'));
    });
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/strategies/s2',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ status: 'paused' }) })
    );
    expect(listMutate).toHaveBeenCalled();
  });

  it('a paused strategy offers Resume and PATCHes back to live', async () => {
    mockData({ strategies: [strategy('s3', { status: 'paused' })] });
    render(<StrategiesPage />);
    expect(screen.getByTestId('strategy-toggle-s3').textContent).toBe('Resume');
    await act(async () => {
      fireEvent.click(screen.getByTestId('strategy-toggle-s3'));
    });
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/strategies/s3',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ status: 'live' }) })
    );
  });

  it('cn: chrome renders in Chinese with ¥-formatted P&L (formatMoney)', () => {
    mockData({
      strategies: [strategy('s1', { ticker: '600519', realized_pnl: 1234.5 })],
      profile: CN_PROFILE,
    });
    render(<StrategiesPage />);
    expect(screen.getByText('策略中心')).toBeInTheDocument();
    const row = screen.getByTestId('strategy-row-s1');
    expect(row.textContent).toContain('+¥1,234.50');
    expect(screen.getByTestId('strategy-status-s1').textContent).toBe('草稿');
    expect(screen.getByTestId('template-card-dip_buyer').textContent).toContain('抄底');
  });
});
