/**
 * AnalyticsRisk.test.tsx — Analytics tab VaR/beta risk cards (D2 §4/§5).
 *
 * Additive contract: `analytics-var` / `analytics-beta` render the backend's
 * 2dp values with a `risk_window_bars` badge; when the backend reports null
 * (<20 common bars / no positions / zero benchmark variance) both cards show
 * "—" and `analytics-risk-hint` links to the /market data card. The existing
 * KPI tiles keep rendering untouched alongside.
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

const baseAnalytics = {
  total_trades: 12,
  sell_trades: 5,
  win_rate: 0.6,
  realized_pnl: 142.5,
  max_drawdown_pct: 6.7,
  sharpe: 1.23,
  best_trade: null,
  worst_trade: null,
  sector_allocation: [{ sector: 'tech', value: 5200, weight: 0.52 }],
};

describe('AnalyticsPanel risk cards (D2 §4)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders VaR/beta at 2dp with the risk-window badge and no hint', () => {
    mockUseSWR.mockReturnValue({
      data: { ...baseAnalytics, var_95_pct: 2.5, beta: 1.062, risk_window_bars: 60 },
    } as never);

    render(<AnalyticsPanel />);

    expect(screen.getByTestId('analytics-var').textContent).toBe('2.50%');
    expect(screen.getByTestId('analytics-beta').textContent).toBe('1.06');
    expect(screen.getByTestId('analytics-risk-window').textContent).toBe('60 bars');
    expect(screen.queryByTestId('analytics-risk-hint')).toBeNull();
  });

  it('null VaR/beta → em-dash cards, no window badge, and the sync hint linking to /market', () => {
    mockUseSWR.mockReturnValue({
      data: { ...baseAnalytics, var_95_pct: null, beta: null, risk_window_bars: 0 },
    } as never);

    render(<AnalyticsPanel />);

    expect(screen.getByTestId('analytics-var').textContent).toBe('—');
    expect(screen.getByTestId('analytics-beta').textContent).toBe('—');
    expect(screen.queryByTestId('analytics-risk-window')).toBeNull();
    const hint = screen.getByTestId('analytics-risk-hint');
    expect(hint.textContent).toContain('Available after syncing historical data');
    const link = hint.querySelector('a');
    expect(link?.getAttribute('href')).toBe('/market');
    expect(link?.textContent).toContain('Historical Data');
  });

  it('a pre-D2 payload without the risk keys degrades exactly like null (no crash)', () => {
    mockUseSWR.mockReturnValue({ data: baseAnalytics } as never);

    render(<AnalyticsPanel />);

    expect(screen.getByTestId('analytics-var').textContent).toBe('—');
    expect(screen.getByTestId('analytics-beta').textContent).toBe('—');
    expect(screen.getByTestId('analytics-risk-hint')).toBeInTheDocument();
  });

  it('the existing KPI tiles keep rendering untouched alongside the risk row', () => {
    mockUseSWR.mockReturnValue({
      data: { ...baseAnalytics, var_95_pct: 2.5, beta: 1.06, risk_window_bars: 60 },
    } as never);

    render(<AnalyticsPanel />);

    expect(screen.getByTestId('stat-total-trades').textContent).toBe('12');
    expect(screen.getByTestId('stat-win-rate').textContent).toBe('60%');
    expect(screen.getByTestId('stat-realized').textContent).toBe('+$142.50');
    expect(screen.getByTestId('stat-drawdown').textContent).toBe('6.7%');
    expect(screen.getByTestId('stat-sharpe').textContent).toBe('1.23');
  });

  it('VaR stays a positive loss % with neutral tone — direction colours stay reserved for P&L', () => {
    mockUseSWR.mockReturnValue({
      data: { ...baseAnalytics, var_95_pct: 4.31, beta: 0.9, risk_window_bars: 20 },
    } as never);

    render(<AnalyticsPanel />);

    const varCard = screen.getByTestId('analytics-var');
    expect(varCard.textContent).toBe('4.31%');
    expect(varCard.className).toContain('text-terminal-text');
    expect(varCard.className).not.toContain('text-terminal-down');
    expect(varCard.className).not.toContain('text-terminal-up');
  });
});
