/**
 * BacktestComponents.test.tsx — P2 §8 pure-refactor extraction.
 *
 * The five components extracted from BacktestPanel (EquityChart, StatCard,
 * StatsGrid, RunsSummaryStrip, TradesBlotter) must render standalone with the
 * exact DOM/testids the panel produced inline, so the /run and /strategy
 * pages can assemble the same UI.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';

jest.mock('lightweight-charts', () => {
  const mockAddSeries = jest.fn().mockReturnValue({
    setData: jest.fn(),
    update: jest.fn(),
    applyOptions: jest.fn(),
  });
  const mockCreateChart = jest.fn().mockReturnValue({
    addSeries: mockAddSeries,
    remove: jest.fn(),
    applyOptions: jest.fn(),
    timeScale: jest.fn().mockReturnValue({ fitContent: jest.fn() }),
  });
  const LineSeries = { __sentinelType: 'LineSeries' };
  const BaselineSeries = { __sentinelType: 'BaselineSeries' };
  return { createChart: mockCreateChart, LineSeries, BaselineSeries };
});

import { createChart, BaselineSeries, LineSeries } from 'lightweight-charts';
import EquityChart, { equityColors } from '@/components/backtest/EquityChart';
import StatCard, { signed, pnlClass } from '@/components/backtest/StatCard';
import StatsGrid from '@/components/backtest/StatsGrid';
import RunsSummaryStrip from '@/components/backtest/RunsSummaryStrip';
import TradesBlotter from '@/components/backtest/TradesBlotter';
import { makeT } from '@/lib/i18n';
import type { BacktestStats, BacktestTrade, BacktestRunsSummary } from '@/types/market';

const t = makeT('en');

const stats: BacktestStats = {
  total_return_pct: 4.31,
  buy_hold_return_pct: -6.02,
  max_drawdown_pct: 3.87,
  final_equity: 10431.22,
  fires: 6,
  round_trips: 6,
  win_rate: 0.67,
  avg_win: 141.02,
  avg_loss: -80.55,
  profit_factor: 2.33,
  commission_paid: 0,
  rejections: { insufficient_cash: 0 },
};

describe('StatCard', () => {
  it('renders label, value, and the default text colour', () => {
    render(<StatCard label="Return" value="+4.31%" />);
    expect(screen.getByText('Return')).toBeInTheDocument();
    const value = screen.getByText('+4.31%');
    expect(value.className).toContain('text-terminal-text');
  });

  it('applies a custom className and testid to the value node', () => {
    render(<StatCard label="Return" value="+4.31%" className="text-terminal-up" testid="backtest-return" />);
    const value = screen.getByTestId('backtest-return');
    expect(value.textContent).toBe('+4.31%');
    expect(value.className).toContain('text-terminal-up');
    expect(value.className).not.toContain('text-terminal-text');
  });
});

describe('signed / pnlClass helpers (single home in StatCard)', () => {
  it('signed prefixes non-negatives with + and respects digits', () => {
    expect(signed(4.311)).toBe('+4.31');
    expect(signed(0)).toBe('+0.00');
    expect(signed(-6.2)).toBe('-6.20');
    expect(signed(3.14159, 1)).toBe('+3.1');
  });

  it('pnlClass maps sign to the flippable direction classes', () => {
    expect(pnlClass(1)).toBe('text-terminal-up');
    expect(pnlClass(0)).toBe('text-terminal-up');
    expect(pnlClass(-0.01)).toBe('text-terminal-down');
  });
});

describe('StatsGrid', () => {
  it('renders all eight stat cards with the panel formatting', () => {
    render(<StatsGrid stats={stats} t={t} currencySymbol="$" locale="en-US" />);
    expect(screen.getByTestId('backtest-return').textContent).toBe('+4.31%');
    expect(screen.getByTestId('backtest-return').className).toContain('text-terminal-up');
    // Buy & hold is negative → down colour
    const bh = screen.getByText('-6.02%');
    expect(bh.className).toContain('text-terminal-down');
    expect(screen.getByText('−3.87%')).toBeInTheDocument(); // Max DD (U+2212)
    expect(screen.getByText('67%')).toBeInTheDocument(); // win rate rounded
    expect(screen.getByText('2.33')).toBeInTheDocument(); // profit factor
    expect(screen.getByText('$10,431.22')).toBeInTheDocument(); // final equity
    expect(screen.getAllByText('6')).toHaveLength(2); // entries + round trips
    expect(screen.getByText('Return')).toBeInTheDocument();
    expect(screen.getByText('Buy & Hold')).toBeInTheDocument();
  });

  it('renders em-dashes for null win rate and profit factor', () => {
    render(
      <StatsGrid
        stats={{ ...stats, win_rate: null, profit_factor: null, round_trips: 0 }}
        t={t}
        currencySymbol="$"
        locale="en-US"
      />
    );
    expect(screen.getAllByText('—')).toHaveLength(2);
  });

  it('cn prop path: currencySymbol/locale render a ¥ final equity', () => {
    render(<StatsGrid stats={stats} t={makeT('zh')} currencySymbol="¥" locale="zh-CN" />);
    expect(
      screen.getByText(
        `¥${(10431.22).toLocaleString('zh-CN', {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        })}`
      )
    ).toBeInTheDocument();
    expect(screen.queryByText('$10,431.22')).toBeNull();
  });
});

describe('RunsSummaryStrip', () => {
  const summary: BacktestRunsSummary = {
    runs: 30,
    median_return_pct: 3.1,
    p05_return_pct: -6.2,
    p95_return_pct: 14.8,
    positive_share: 0.7,
    median_max_drawdown_pct: 4.4,
  };

  it('renders the Monte Carlo distribution with the panel testid', () => {
    render(<RunsSummaryStrip summary={summary} t={t} />);
    const strip = screen.getByTestId('backtest-runs-summary');
    expect(strip.textContent).toContain('30 runs');
    expect(strip.textContent).toContain('+3.10%');
    expect(strip.textContent).toContain('-6.20%');
    expect(strip.textContent).toContain('+14.80%');
    expect(strip.textContent).toContain('70%');
    expect(strip.textContent).toContain('−4.40%');
  });
});

describe('TradesBlotter', () => {
  const trades: BacktestTrade[] = [
    { time: 1751000000, side: 'buy', price: 186.1, quantity: 5, reason: 'trigger', pnl: null },
    { time: 1751086400, side: 'sell', price: 195.4, quantity: 5, reason: 'take_profit', pnl: 46.5 },
  ];

  it('renders one row per trade with reasons, P&L, and direction colours', () => {
    render(
      <TradesBlotter trades={trades} t={t} currencySymbol="$" locale="en-US" lotSize={1} />
    );
    const table = screen.getByTestId('backtest-trades');
    expect(table.querySelectorAll('tbody tr')).toHaveLength(2);
    expect(table.textContent).toContain('entry');
    expect(table.textContent).toContain('take profit');
    expect(table.textContent).toContain('$186.10');
    expect(table.textContent).toContain('+$46.50');
    const buyCell = screen.getByText('buy');
    expect(buyCell.className).toContain('text-terminal-up');
    const sellCell = screen.getByText('sell');
    expect(sellCell.className).toContain('text-terminal-down');
  });

  it('renders — for buy rows without P&L', () => {
    render(
      <TradesBlotter
        trades={[trades[0]]}
        t={t}
        currencySymbol="$"
        locale="en-US"
        lotSize={1}
      />
    );
    expect(screen.getByText('—').className).toContain('text-terminal-muted');
  });

  it('cn prop path: currencySymbol/locale render ¥ prices and localized dates', () => {
    render(
      <TradesBlotter
        trades={trades}
        t={makeT('zh')}
        currencySymbol="¥"
        locale="zh-CN"
        lotSize={100}
      />
    );
    const table = screen.getByTestId('backtest-trades');
    expect(table.textContent).toContain('¥186.10');
    expect(table.textContent).toContain('+¥46.50');
    expect(table.textContent).not.toContain('$');
    expect(table.textContent).toContain(
      new Date(trades[0].time * 1000).toLocaleDateString('zh-CN', {
        month: 'short',
        day: 'numeric',
      })
    );
  });
});

describe('EquityChart', () => {
  beforeEach(() => jest.clearAllMocks());

  const equity = [
    { time: 1751000000, value: 10000 },
    { time: 1751000060, value: 10431.22 },
  ];
  const baseline = [
    { time: 1751000000, value: 10000 },
    { time: 1751000060, value: 10602 },
  ];

  it('mounts a chart with the $10k BaselineSeries and dashed buy&hold line, then sets both datasets', () => {
    render(
      <EquityChart equity={equity} baseline={baseline} colors={equityColors(false)} baseValue={10000} />
    );
    expect(screen.getByTestId('backtest-chart')).toBeInTheDocument();

    const mc = jest.mocked(createChart);
    expect(mc).toHaveBeenCalledTimes(1);
    const chart = mc.mock.results[0].value as { addSeries: jest.Mock };
    expect(chart.addSeries).toHaveBeenCalledWith(
      BaselineSeries,
      expect.objectContaining({
        baseValue: { type: 'price', price: 10000 },
        topLineColor: '#22c55e',
        bottomLineColor: '#ef4444',
      })
    );
    expect(chart.addSeries).toHaveBeenCalledWith(LineSeries, expect.objectContaining({ lineStyle: 2 }));
    const equitySeries = (chart.addSeries.mock.results[0] as jest.MockResult<{ setData: jest.Mock }>)
      .value;
    expect(equitySeries.setData).toHaveBeenCalledWith(equity);
    const baselineSeries = (chart.addSeries.mock.results[1] as jest.MockResult<{ setData: jest.Mock }>)
      .value;
    expect(baselineSeries.setData).toHaveBeenCalledWith(baseline);
  });

  it('equityColors swaps hexes and fill tints on up_is_red markets', () => {
    const us = equityColors(false);
    expect(us.upHex).toBe('#22c55e');
    expect(us.downHex).toBe('#ef4444');
    expect(us.upFill1).toContain('34, 197, 94');
    expect(us.downFill2).toContain('239, 68, 68');

    const cn = equityColors(true);
    expect(cn.upHex).toBe('#ef4444');
    expect(cn.downHex).toBe('#22c55e');
    expect(cn.upFill1).toContain('239, 68, 68');
    expect(cn.downFill2).toContain('34, 197, 94');
    // The tint constants converge to one module: same rgba strings, swapped
    expect(cn.upFill1).toBe(us.downFill2);
    expect(cn.upFill2).toBe(us.downFill1);
  });
});
