/**
 * PositionsTable tests (TDD):
 * Test 1: Given a portfolio with one position, all six columns render
 * Test 2: Price update with direction 'up' → current-price cell gains class flash-up;
 *         after 500ms (fake timers) the class is removed
 * Test 3: Empty portfolio → empty-state copy renders
 */
import React from 'react';
import { render, screen, act } from '@testing-library/react';
import { usePriceStore } from '@/stores/priceStore';
import type { PriceUpdate, Position } from '@/types/market';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

import useSWR from 'swr';
const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

import PositionsTable from '@/components/PositionsTable';

const mockPosition: Position = {
  ticker: 'AAPL',
  quantity: 10,
  avg_cost: 185.5,
  current_price: 188.25,
  unrealized_pnl: 27.5,
  pnl_pct: 1.48,
};

const mockPortfolio = {
  cash: 10000,
  total_value: 11882.5,
  positions: [mockPosition],
};

const mkPrice = (direction: 'up' | 'down' | 'flat', ts = 1717700000): PriceUpdate => ({
  ticker: 'AAPL',
  price: 190.0,
  previous_price: 188.25,
  timestamp: ts,
  change: direction === 'down' ? -1 : 1,
  change_percent: direction === 'down' ? -0.93 : 0.93,
  direction,
});

describe('PositionsTable', () => {
  beforeEach(() => {
    usePriceStore.setState({ prices: {}, connectionStatus: 'disconnected' });
    jest.useFakeTimers();
    mockUseSWR.mockReturnValue({ data: mockPortfolio } as any);
    jest.clearAllMocks();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('Test 1: One position → all six columns render (Ticker, Qty, Avg Cost, Price, P&L, Change %)', () => {
    render(<PositionsTable />);

    // Column headers
    expect(screen.getByText('Ticker')).toBeInTheDocument();
    expect(screen.getByText('Qty')).toBeInTheDocument();
    expect(screen.getByText('Avg Cost')).toBeInTheDocument();
    expect(screen.getByText('Price')).toBeInTheDocument();
    expect(screen.getByText('P&L')).toBeInTheDocument();
    expect(screen.getByText('Change %')).toBeInTheDocument();

    // Row data
    expect(screen.getByText('AAPL')).toBeInTheDocument();
  });

  it('Test 2: Price update direction up → current-price cell gains flash-up; removed after 500ms', () => {
    const { container } = render(<PositionsTable />);

    // Dispatch a price update with direction 'up'
    act(() => {
      usePriceStore.setState({ prices: { AAPL: mkPrice('up') } });
    });

    // The ref-tracked price cell should have flash-up
    // Find the price cell by its data-testid or by querying within the row
    const priceCells = container.querySelectorAll('[data-price-cell="AAPL"]');
    expect(priceCells.length).toBe(1);
    const priceCell = priceCells[0] as HTMLElement;
    expect(priceCell.classList.contains('flash-up')).toBe(true);
    expect(priceCell.classList.contains('flash-down')).toBe(false);

    // After 500ms, class should be removed
    act(() => {
      jest.advanceTimersByTime(500);
    });
    expect(priceCell.classList.contains('flash-up')).toBe(false);
  });

  it('Test 3: Empty portfolio → empty-state text renders', () => {
    mockUseSWR.mockReturnValue({
      data: { cash: 10000, total_value: 10000, positions: [] },
    } as any);

    render(<PositionsTable />);
    expect(
      screen.getByText('No positions yet. Use the trade bar to buy shares.')
    ).toBeInTheDocument();
  });
});
