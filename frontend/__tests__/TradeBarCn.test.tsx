/**
 * TradeBarCn.test.tsx (FinAlly-CN, CN-3 §6)
 *
 * With a mocked A-share profile (lot_size 100, locale zh-CN) the trade ticket
 * renders in Chinese, inputs quantity in 手, and submits shares = 手 × lot.
 */
import React from 'react';
import { render, fireEvent, act } from '@testing-library/react';
import useSWR from 'swr';

jest.mock('swr', () => ({ __esModule: true, default: jest.fn() }));

import TradeBar from '@/components/TradeBar';

const cnProfile = {
  market: 'cn',
  currency_symbol: '¥',
  locale: 'zh-CN',
  lot_size: 100,
  t_plus: 1,
  up_is_red: true,
  seed_cash: 100000,
  midday_break: true,
  names: {},
  price_limit_pct: {},
};

const mockPortfolio = {
  cash: 100000,
  total_value: 100000,
  positions: [
    { ticker: 'AAPL', quantity: 100, avg_cost: 190, current_price: 190, unrealized_pnl: 0, pnl_pct: 0 },
  ],
};

const mockMutate = jest.fn().mockImplementation(async (fn: any) => {
  if (typeof fn === 'function') await fn(mockPortfolio);
});

describe('TradeBar — A-share (lot) market', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ticker: 'AAPL', side: 'buy', quantity: 200, price: 190 }),
    });
    jest.mocked(useSWR).mockImplementation(((key: string) => {
      if (key === '/api/market/profile') return { data: cnProfile } as never;
      return { data: mockPortfolio, mutate: mockMutate } as never;
    }) as never);
  });

  it('renders Chinese labels and quantity in 手', () => {
    const { getByTestId, getByLabelText } = render(<TradeBar selectedTicker="AAPL" />);
    expect(getByTestId('trade-buy-button').textContent).toBe('买入');
    expect(getByTestId('trade-sell-button').textContent).toBe('卖出');
    // The quantity input is labelled 手 (getByLabelText resolves via aria-label)
    expect(getByLabelText('手')).toBeTruthy();
  });

  it('submits shares = 手 × lot_size (2 手 → quantity 200)', async () => {
    const { getByLabelText, getByTestId } = render(<TradeBar selectedTicker="AAPL" />);

    fireEvent.change(getByLabelText('手'), { target: { value: '2' } });

    await act(async () => {
      fireEvent.click(getByTestId('trade-buy-button'));
    });

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/portfolio/trade',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ ticker: 'AAPL', quantity: 200, side: 'buy' }),
      })
    );
  });

  it('shows the ¥ currency symbol in the estimate', () => {
    const { getByLabelText, getByTestId } = render(<TradeBar selectedTicker="AAPL" />);
    fireEvent.change(getByLabelText('手'), { target: { value: '2' } });
    // 2 手 × 100 × ¥190 = ¥38,000.00
    expect(getByTestId('trade-estimate').textContent).toContain('¥38,000.00');
  });
});
