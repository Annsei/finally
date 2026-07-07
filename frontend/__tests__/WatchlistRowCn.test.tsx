/**
 * WatchlistRowCn.test.tsx (FinAlly-CN, CN-3 §6)
 *
 * With an A-share profile the row shows the Chinese name beside the code and a
 * 涨停/跌停 badge when the live price hits the day's limit. The default (US)
 * profile — the fallback for every existing test — renders neither.
 */
import React from 'react';
import { render, act } from '@testing-library/react';
import { usePriceStore } from '@/stores/priceStore';
import { US_PROFILE, type MarketProfile } from '@/lib/marketProfile';
import type { PriceUpdate } from '@/types/market';

jest.mock('@/components/SparklineChart', () => ({
  __esModule: true,
  default: ({ ticker }: { ticker: string }) => <div data-testid={`sparkline-${ticker}`} />,
}));

import WatchlistRow from '@/components/WatchlistRow';

const cnProfile: MarketProfile = {
  ...US_PROFILE,
  market: 'cn',
  currency_symbol: '¥',
  locale: 'zh-CN',
  lot_size: 100,
  up_is_red: true,
  names: { '600519': '贵州茅台' },
  price_limit_pct: { '600519': 10 },
};

const mkPrice = (over: Partial<PriceUpdate>): PriceUpdate => ({
  ticker: '600519',
  price: 1700,
  previous_price: 1699,
  timestamp: 1,
  change: 1,
  change_percent: 0.06,
  direction: 'up',
  ...over,
});

const renderRow = (ticker: string, profile?: MarketProfile) =>
  render(
    <table>
      <tbody>
        <WatchlistRow ticker={ticker} isSelected={false} onSelect={jest.fn()} profile={profile} />
      </tbody>
    </table>
  );

describe('WatchlistRow — A-share market', () => {
  beforeEach(() => {
    usePriceStore.setState({ prices: {}, connectionStatus: 'disconnected' });
  });

  it('shows the stock name and a 涨停 badge at the upper limit', () => {
    const { getByText, getByTestId } = renderRow('600519', cnProfile);
    act(() => {
      usePriceStore.setState({
        prices: { '600519': mkPrice({ price: 1870, limit_up: 1870, limit_down: 1530 }) },
      });
    });
    expect(getByText('贵州茅台')).toBeTruthy();
    expect(getByTestId('limit-badge-600519').textContent).toBe('涨停');
  });

  it('shows a 跌停 badge at the lower limit', () => {
    const { getByTestId } = renderRow('600519', cnProfile);
    act(() => {
      usePriceStore.setState({
        prices: { '600519': mkPrice({ price: 1530, limit_up: 1870, limit_down: 1530 }) },
      });
    });
    expect(getByTestId('limit-badge-600519').textContent).toBe('跌停');
  });

  it('US default (no profile) renders neither a name nor a badge', () => {
    const { queryByText, queryByTestId } = renderRow('AAPL');
    act(() => {
      usePriceStore.setState({
        prices: { AAPL: { ...mkPrice({}), ticker: 'AAPL', price: 190 } },
      });
    });
    expect(queryByText('贵州茅台')).toBeNull();
    expect(queryByTestId('limit-badge-AAPL')).toBeNull();
  });
});
