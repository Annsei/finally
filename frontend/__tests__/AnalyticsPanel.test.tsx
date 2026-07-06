/**
 * AnalyticsPanel tests (M3.4):
 * Test 1: KPI tiles render formatted values with P&L direction coloring
 * Test 2: sector allocation bars render with labels, weights and fixed hues
 * Test 3: best/worst trades render; null metrics show placeholders
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import useSWR from 'swr';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

import AnalyticsPanel from '@/components/AnalyticsPanel';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

const fullAnalytics = {
  total_trades: 12,
  sell_trades: 5,
  win_rate: 0.6,
  realized_pnl: 142.5,
  max_drawdown_pct: 6.7,
  sharpe: 1.23,
  best_trade: {
    ticker: 'NVDA', side: 'sell', quantity: 2, price: 880.4,
    realized_pnl: 120.3, executed_at: '2026-07-06T14:00:00Z',
  },
  worst_trade: {
    ticker: 'TSLA', side: 'sell', quantity: 4, price: 240.1,
    realized_pnl: -35.2, executed_at: '2026-07-06T15:00:00Z',
  },
  sector_allocation: [
    { sector: 'tech', value: 5200, weight: 0.52 },
    { sector: 'cash', value: 3600, weight: 0.36 },
    { sector: 'crypto', value: 1200, weight: 0.12 },
  ],
};

describe('AnalyticsPanel', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('Test 1: KPI tiles render formatted values, realized P&L colored by sign', () => {
    mockUseSWR.mockReturnValue({ data: fullAnalytics } as any);

    render(<AnalyticsPanel />);

    expect(screen.getByTestId('stat-total-trades').textContent).toBe('12');
    expect(screen.getByTestId('stat-win-rate').textContent).toBe('60%');
    const realized = screen.getByTestId('stat-realized');
    expect(realized.textContent).toBe('+$142.50');
    expect(realized.className).toContain('text-terminal-up');
    expect(screen.getByTestId('stat-drawdown').textContent).toBe('6.7%');
    expect(screen.getByTestId('stat-sharpe').textContent).toBe('1.23');
  });

  it('Test 2: sector allocation renders labeled bars with weights and fixed hues', () => {
    mockUseSWR.mockReturnValue({ data: fullAnalytics } as any);

    const { container } = render(<AnalyticsPanel />);

    const alloc = screen.getByTestId('sector-allocation');
    expect(alloc.textContent).toContain('tech');
    expect(alloc.textContent).toContain('52.0%');
    expect(alloc.textContent).toContain('cash');
    expect(alloc.textContent).toContain('36.0%');

    // Fixed categorical hues (validated palette): tech blue, crypto purple
    const bars = container.querySelectorAll('[data-testid="sector-allocation"] span[style]');
    const styles = Array.from(bars).map((b) => (b as HTMLElement).style.backgroundColor);
    expect(styles).toContain('rgb(32, 157, 215)'); // #209dd7 tech
    expect(styles).toContain('rgb(168, 117, 201)'); // #a875c9 crypto
  });

  it('Test 3: best/worst trades render; null metrics show em-dash placeholders', () => {
    mockUseSWR.mockReturnValue({
      data: {
        ...fullAnalytics,
        win_rate: null,
        max_drawdown_pct: null,
        sharpe: null,
      },
    } as any);

    render(<AnalyticsPanel />);

    expect(screen.getByTestId('best-trade').textContent).toContain('Sell 2 NVDA @ $880.40');
    expect(screen.getByTestId('best-trade').textContent).toContain('+$120.30');
    expect(screen.getByTestId('worst-trade').textContent).toContain('-$35.20');
    expect(screen.getByTestId('stat-win-rate').textContent).toBe('—');
    expect(screen.getByTestId('stat-drawdown').textContent).toBe('—');
    expect(screen.getByTestId('stat-sharpe').textContent).toBe('—');
  });
});
