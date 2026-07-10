import React from 'react';
import { render, screen } from '@testing-library/react';
import useSWR from 'swr';
import PositionsTable from '@/components/PositionsTable';
import OrdersTable from '@/components/OrdersTable';
import { usePriceStore } from '@/stores/priceStore';

jest.mock('swr', () => ({ __esModule: true, default: jest.fn() }));

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;
const profile = {
  market: 'cn',
  currency_symbol: '¥',
  locale: 'zh-CN',
  lot_size: 100,
  t_plus: 1,
  up_is_red: true,
  seed_cash: 100000,
  midday_break: true,
  names: { '600519': '贵州茅台' },
  price_limit_pct: { '600519': 10 },
};

describe('market-aware amount surfaces', () => {
  beforeEach(() => {
    usePriceStore.setState({ prices: {}, connectionStatus: 'disconnected' });
    mockUseSWR.mockImplementation(((key: string) => {
      if (key === '/api/market/profile') return { data: profile, mutate: jest.fn() };
      if (key === '/api/portfolio/') {
        return {
          data: {
            cash: 100000,
            total_value: 275000,
            positions: [
              {
                ticker: '600519',
                quantity: 100,
                avg_cost: 1700,
                current_price: 1750,
                unrealized_pnl: 5000,
                pnl_pct: 2.94,
              },
            ],
          },
          mutate: jest.fn(),
        };
      }
      if (key === '/api/portfolio/trades') {
        return {
          data: {
            trades: [
              {
                id: 'cn-trade',
                ticker: '600519',
                side: 'buy',
                quantity: 100,
                price: 1750,
                executed_at: '2026-07-10T01:00:00Z',
              },
            ],
          },
          mutate: jest.fn(),
        };
      }
      return { data: undefined, mutate: jest.fn() };
    }) as never);
  });

  it('uses CN currency and board-lot quantities without dollar fallbacks', () => {
    render(
      <>
        <PositionsTable />
        <OrdersTable />
      </>
    );

    const position = screen.getByTestId('position-row-600519');
    const fill = screen.getByTestId('order-row-cn-trade');
    expect(position.textContent).toContain('1手');
    expect(position.textContent).toContain('¥1,700.00');
    expect(fill.textContent).toContain('1手');
    expect(fill.textContent).toContain('¥1750.00');
    expect(`${position.textContent}${fill.textContent}`).not.toContain('$');
  });
});
