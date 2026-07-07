/**
 * TradeBarWholeLot.test.tsx (FinAlly-CN, CN-4a)
 *
 * On lot markets (A-share, lot_size 100) the 手 input must be a positive
 * integer. A non-blocking hint appears when it isn't — mirroring the
 * concentration rail. The US market (lot_size 1) never shows it.
 *
 * NEW test file — no existing test is touched.
 */
import React from 'react';
import { render, fireEvent } from '@testing-library/react';
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

const usProfile = { ...cnProfile, market: 'us', currency_symbol: '$', locale: 'en-US', lot_size: 1 };

const mockPortfolio = {
  cash: 100000,
  total_value: 100000,
  positions: [],
};

function mockSWR(profile: unknown) {
  jest.mocked(useSWR).mockImplementation(((key: string) => {
    if (key === '/api/market/profile') return { data: profile } as never;
    return { data: mockPortfolio, mutate: jest.fn() } as never;
  }) as never);
}

describe('TradeBar — whole-lot hint (A-share)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn();
  });

  it('shows the hint when the 手 quantity is not a whole number', () => {
    mockSWR(cnProfile);
    const { getByLabelText, getByTestId } = render(<TradeBar selectedTicker="600519" />);
    fireEvent.change(getByLabelText('手'), { target: { value: '2.5' } });
    expect(getByTestId('trade-whole-lot-hint').textContent).toBe('请输入整数手数。');
  });

  it('hides the hint for a whole number of 手', () => {
    mockSWR(cnProfile);
    const { getByLabelText, queryByTestId } = render(<TradeBar selectedTicker="600519" />);
    fireEvent.change(getByLabelText('手'), { target: { value: '2' } });
    expect(queryByTestId('trade-whole-lot-hint')).toBeNull();
  });

  it('hides the hint when the 手 field is empty', () => {
    mockSWR(cnProfile);
    const { queryByTestId } = render(<TradeBar selectedTicker="600519" />);
    expect(queryByTestId('trade-whole-lot-hint')).toBeNull();
  });

  it('never shows the hint on the US market (lot_size 1), even for fractional qty', () => {
    mockSWR(usProfile);
    const { getByLabelText, queryByTestId } = render(<TradeBar selectedTicker="AAPL" />);
    fireEvent.change(getByLabelText('Qty'), { target: { value: '2.5' } });
    expect(queryByTestId('trade-whole-lot-hint')).toBeNull();
  });
});
