/**
 * OrdersTable + PortfolioTabs tests (Batch-1 blotter):
 * Test 1: trades render newest-first with formatted time/qty/price/value
 * Test 2: side coloring — buy green, sell red
 * Test 3: empty state renders when there are no trades
 * Test 4: PortfolioTabs defaults to Positions (E2E contract) and switches to Orders
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import useSWR from 'swr';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

import OrdersTable from '@/components/OrdersTable';
import PortfolioTabs from '@/components/PortfolioTabs';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

const mockTrades = {
  trades: [
    {
      id: 't2',
      ticker: 'NVDA',
      side: 'sell' as const,
      quantity: 2.5,
      price: 880.4,
      executed_at: '2026-07-05T14:32:10Z',
    },
    {
      id: 't1',
      ticker: 'AAPL',
      side: 'buy' as const,
      quantity: 5,
      price: 190.02,
      executed_at: '2026-07-05T14:30:00Z',
    },
  ],
};

describe('OrdersTable', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('Test 1: renders trades newest-first with formatted qty, price and value', () => {
    mockUseSWR.mockReturnValue({ data: mockTrades } as any);

    render(<OrdersTable />);

    const table = screen.getByTestId('orders-table');
    expect(table).toBeInTheDocument();

    // Row order matches the API order (newest first)
    const rows = screen.getAllByTestId(/^order-row-/);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveAttribute('data-testid', 'order-row-t2');
    expect(rows[1]).toHaveAttribute('data-testid', 'order-row-t1');

    // NVDA sell: qty 2.5, price $880.40, value 2.5 × 880.4 = $2201.00
    expect(rows[0].textContent).toContain('NVDA');
    expect(rows[0].textContent).toContain('2.5');
    expect(rows[0].textContent).toContain('$880.40');
    expect(rows[0].textContent).toContain('$2201.00');

    // AAPL buy: value 5 × 190.02 = $950.10
    expect(rows[1].textContent).toContain('AAPL');
    expect(rows[1].textContent).toContain('$950.10');
  });

  it('Test 2: buy renders green, sell renders red', () => {
    mockUseSWR.mockReturnValue({ data: mockTrades } as any);

    render(<OrdersTable />);

    const sellCell = screen.getByText('sell');
    const buyCell = screen.getByText('buy');
    expect(sellCell.className).toContain('text-terminal-down');
    expect(buyCell.className).toContain('text-terminal-up');
  });

  it('Test 3: empty state renders when there are no trades', () => {
    mockUseSWR.mockReturnValue({ data: { trades: [] } } as any);

    render(<OrdersTable />);

    expect(screen.getByText(/No trades yet/i)).toBeInTheDocument();
    expect(screen.queryByTestId('orders-table')).not.toBeInTheDocument();
  });
});

describe('PortfolioTabs', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    // Both PositionsTable and OrdersTable read useSWR — empty data for both
    mockUseSWR.mockReturnValue({ data: undefined } as any);
  });

  it('Test 4: defaults to Positions and switches between Orders (open) and Fills (blotter)', () => {
    render(<PortfolioTabs />);

    // Positions tab content is the default (E2E data-testid contract)
    expect(screen.getByText(/No positions yet/i)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('tab-orders'));
    expect(screen.getByText(/No open orders/i)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('tab-fills'));
    expect(screen.getByText(/No trades yet/i)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('tab-positions'));
    expect(screen.getByText(/No positions yet/i)).toBeInTheDocument();
  });
});
