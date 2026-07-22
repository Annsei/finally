/**
 * OpenOrdersTable tests (Batch 3.2 — resting limit orders):
 * Test 1: open orders render with ≤/≥ limit formatting and side coloring
 * Test 2: cancel issues DELETE and revalidates
 * Test 3: empty state renders
 */
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import useSWR from 'swr';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

import OpenOrdersTable from '@/components/OpenOrdersTable';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

const mockOrders = {
  orders: [
    {
      id: 'o2',
      ticker: 'NVDA',
      side: 'sell' as const,
      quantity: 2,
      limit_price: 900,
      status: 'open' as const,
      reject_reason: null,
      created_at: '2026-07-06T14:32:10Z',
      filled_at: null,
      fill_price: null,
    },
    {
      id: 'o1',
      ticker: 'AAPL',
      side: 'buy' as const,
      quantity: 5,
      limit_price: 185,
      status: 'open' as const,
      reject_reason: null,
      created_at: '2026-07-06T14:30:00Z',
      filled_at: null,
      fill_price: null,
    },
  ],
};

describe('OpenOrdersTable', () => {
  const mockMutate = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn();
    mockUseSWR.mockReturnValue({ data: mockOrders, mutate: mockMutate } as any);
  });

  it('Test 1: open orders render with directional limit formatting and side coloring', () => {
    render(<OpenOrdersTable />);

    expect(screen.getByTestId('open-orders-table')).toBeInTheDocument();
    const rows = screen.getAllByTestId(/^open-order-row-/);
    expect(rows).toHaveLength(2);

    // Sell order: ≥ limit; buy order: ≤ limit
    expect(rows[0].textContent).toContain('≥$900.00');
    expect(rows[1].textContent).toContain('≤$185.00');
    expect(screen.getByText('sell').className).toContain('text-terminal-down');
    expect(screen.getByText('buy').className).toContain('text-terminal-up');
  });

  it('Test 2: cancel button DELETEs the order and revalidates', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ order: { ...mockOrders.orders[1], status: 'cancelled' } }),
    });

    render(<OpenOrdersTable />);

    fireEvent.click(screen.getByTestId('cancel-order-o1'));

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith('/api/portfolio/orders/o1', {
        method: 'DELETE',
      });
      expect(mockMutate).toHaveBeenCalled();
    });
  });

  it('Test 2b: failed cancel (order already filled) surfaces the error and still revalidates', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      json: async () => ({ error: 'Order is not open' }),
    });

    render(<OpenOrdersTable />);

    fireEvent.click(screen.getByTestId('cancel-order-o1'));

    await waitFor(() => {
      expect(screen.getByTestId('orders-cancel-error').textContent).toBe('Order is not open');
      expect(mockMutate).toHaveBeenCalled();
    });
  });

  it('Test 3: empty state renders', () => {
    mockUseSWR.mockReturnValue({ data: { orders: [] }, mutate: mockMutate } as any);

    render(<OpenOrdersTable />);

    expect(screen.getByText(/No open orders/i)).toBeInTheDocument();
    expect(screen.queryByTestId('open-orders-table')).not.toBeInTheDocument();
  });
});
