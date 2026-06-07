/**
 * PortfolioHeatmap tests (TDD):
 * Test 1: Two positions → heatmap renders two tiles, each showing ticker, dollar value, and P&L%
 * Test 2: Each tile's width% is proportional to positionValue / total_value (assert inline width style)
 * Test 3: No positions → empty-state text renders
 */
import React from 'react';
import { render, screen } from '@testing-library/react';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

import useSWR from 'swr';
const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

import PortfolioHeatmap from '@/components/PortfolioHeatmap';
import type { Position, PortfolioResponse } from '@/types/market';

const mockPositions: Position[] = [
  {
    ticker: 'AAPL',
    quantity: 10,
    avg_cost: 185.0,
    current_price: 190.0,
    unrealized_pnl: 50.0,
    pnl_pct: 2.7,
  },
  {
    ticker: 'TSLA',
    quantity: 5,
    avg_cost: 250.0,
    current_price: 240.0,
    unrealized_pnl: -50.0,
    pnl_pct: -4.0,
  },
];

// AAPL: 10 * 190 = 1900, TSLA: 5 * 240 = 1200, total_value = 3100 (cash not needed for heatmap)
const mockPortfolio: PortfolioResponse = {
  cash: 5000,
  total_value: 3100,
  positions: mockPositions,
};

describe('PortfolioHeatmap', () => {
  beforeEach(() => {
    mockUseSWR.mockReturnValue({ data: undefined } as any);
    jest.clearAllMocks();
  });

  it('Test 1: Two positions → renders two tiles, each showing ticker, dollar value, and P&L%', () => {
    mockUseSWR.mockReturnValue({ data: mockPortfolio } as any);
    render(<PortfolioHeatmap />);

    // AAPL tile: ticker, dollar value $1900, pnl%
    expect(screen.getByText('AAPL')).toBeInTheDocument();
    expect(screen.getByText('$1900')).toBeInTheDocument();
    expect(screen.getByText('+2.70%')).toBeInTheDocument();

    // TSLA tile: ticker, dollar value $1200, pnl% (negative)
    expect(screen.getByText('TSLA')).toBeInTheDocument();
    expect(screen.getByText('$1200')).toBeInTheDocument();
    expect(screen.getByText('-4.00%')).toBeInTheDocument();
  });

  it('Test 2: Each tile width% is proportional to positionValue / total_value', () => {
    mockUseSWR.mockReturnValue({ data: mockPortfolio } as any);
    const { container } = render(<PortfolioHeatmap />);

    // AAPL: 1900/3100 * 100 ≈ 61.29%
    // TSLA: 1200/3100 * 100 ≈ 38.71%
    const tiles = container.querySelectorAll('[style*="width"]');
    expect(tiles.length).toBeGreaterThanOrEqual(2);

    const aaplWidthPct = (1900 / 3100) * 100;
    const tslaWidthPct = (1200 / 3100) * 100;

    // Find tiles by ticker text
    const aaplTile = screen.getByText('AAPL').closest('div[style]') as HTMLElement;
    const tslaTile = screen.getByText('TSLA').closest('div[style]') as HTMLElement;

    expect(aaplTile?.style.width).toBe(`${aaplWidthPct}%`);
    expect(tslaTile?.style.width).toBe(`${tslaWidthPct}%`);
  });

  it('Test 3: No positions → renders empty-state text', () => {
    mockUseSWR.mockReturnValue({
      data: { cash: 10000, total_value: 10000, positions: [] },
    } as any);
    render(<PortfolioHeatmap />);

    expect(
      screen.getByText('No positions yet. Use the trade bar to buy shares.')
    ).toBeInTheDocument();
  });

  it('Test 3b: Data not loaded (undefined) → renders empty-state text', () => {
    mockUseSWR.mockReturnValue({ data: undefined } as any);
    render(<PortfolioHeatmap />);

    expect(
      screen.getByText('No positions yet. Use the trade bar to buy shares.')
    ).toBeInTheDocument();
  });
});
