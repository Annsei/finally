/**
 * RulesTable tests (M2.2 — standing rules panel):
 * Test 1: rules render with condition text, action coloring and status chip
 * Test 2: pause/arm toggle PATCHes the right status and revalidates
 * Test 3: delete DELETEs and revalidates
 * Test 4: empty state renders
 */
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import useSWR from 'swr';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

import RulesTable from '@/components/RulesTable';
import type { TradingRule } from '@/types/market';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

const activeRule: TradingRule = {
  id: 'r1',
  ticker: 'NVDA',
  description: 'Buy 5 NVDA when day change <= -3%',
  trigger_type: 'day_change_pct_below',
  threshold: -3,
  side: 'buy',
  quantity: 5,
  status: 'active',
  created_at: '2026-07-06T14:00:00Z',
  last_fired_at: null,
  fire_count: 0,
};

const firedRule: TradingRule = {
  id: 'r2',
  ticker: 'AAPL',
  description: 'Sell 10 AAPL when price >= $200',
  trigger_type: 'price_above',
  threshold: 200,
  side: 'sell',
  quantity: 10,
  status: 'fired',
  created_at: '2026-07-06T13:00:00Z',
  last_fired_at: '2026-07-06T15:00:00Z',
  fire_count: 1,
};

describe('RulesTable', () => {
  const mockMutate = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn();
    mockUseSWR.mockReturnValue({
      data: { rules: [activeRule, firedRule] },
      mutate: mockMutate,
    } as any);
  });

  it('Test 1: rules render with condition, colored action and status chip', () => {
    render(<RulesTable />);

    expect(screen.getByTestId('rules-table')).toBeInTheDocument();

    const row1 = screen.getByTestId('rule-row-r1');
    expect(row1.textContent).toContain('Buy 5 NVDA when day change <= -3%');
    expect(row1.textContent).toContain('day ≤ -3%');
    expect(screen.getByTestId('rule-status-r1').textContent).toBe('active');

    const row2 = screen.getByTestId('rule-row-r2');
    expect(row2.textContent).toContain('price ≥ $200.00');
    expect(screen.getByTestId('rule-status-r2').textContent).toBe('fired');
    expect(row2.textContent).toContain('1'); // fire_count
  });

  it('Test 2: toggle PATCHes active→paused and fired→active (re-arm)', async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => ({ rule: { ...activeRule, status: 'paused' } }),
    });

    render(<RulesTable />);

    fireEvent.click(screen.getByTestId('rule-toggle-r1'));
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        '/api/rules/r1',
        expect.objectContaining({
          method: 'PATCH',
          body: JSON.stringify({ status: 'paused' }),
        })
      );
      expect(mockMutate).toHaveBeenCalled();
    });

    fireEvent.click(screen.getByTestId('rule-toggle-r2'));
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        '/api/rules/r2',
        expect.objectContaining({
          method: 'PATCH',
          body: JSON.stringify({ status: 'active' }),
        })
      );
    });
  });

  it('Test 3: delete DELETEs the rule and revalidates', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ rule: activeRule }),
    });

    render(<RulesTable />);

    fireEvent.click(screen.getByTestId('rule-delete-r1'));
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith('/api/rules/r1', { method: 'DELETE' });
      expect(mockMutate).toHaveBeenCalled();
    });
  });

  it('Test 4: empty state renders', () => {
    mockUseSWR.mockReturnValue({ data: { rules: [] }, mutate: mockMutate } as any);

    render(<RulesTable />);

    expect(screen.getByText(/No standing rules/i)).toBeInTheDocument();
    expect(screen.queryByTestId('rules-table')).not.toBeInTheDocument();
  });
});
