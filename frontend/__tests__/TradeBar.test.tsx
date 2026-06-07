/**
 * TradeBar tests (TDD):
 * Test T-4-01: Empty/invalid ticker blocks fetch with inline error
 * Test T-4-03: Quantity <= 0 blocks fetch with inline error
 * Test T-4-buy: Clicking Buy with valid inputs calls POST /api/portfolio/trade with side "buy"
 * Test T-4-400: 400 response ({ error: "Insufficient cash" }) shows inline error
 * Test T-4-clear: Submitting again clears the prior error before the new attempt
 * Test T-4-fill: When selectedTicker prop changes, ticker input syncs to it
 */
import React from 'react';
import { render, fireEvent, waitFor, act } from '@testing-library/react';
import useSWR from 'swr';

// jest.mock is hoisted above variable declarations — define inline data in the factory
// to avoid "Cannot access before initialization" errors. We override per-test with
// jest.mocked(useSWR).mockReturnValue in beforeEach.
jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

import TradeBar from '@/components/TradeBar';

// Mock portfolio data matching PortfolioResponse
const mockPortfolio = {
  cash: 10000,
  total_value: 10000,
  positions: [
    {
      ticker: 'AAPL',
      quantity: 10,
      avg_cost: 185.0,
      current_price: 190.0,
      unrealized_pnl: 50.0,
      pnl_pct: 2.7,
    },
  ],
};

// mockMutate: invokes the async mutator and re-throws errors so the caller's
// catch block fires. SWR v2 applies rollback internally AND re-throws — our mock
// matches that behavior so TradeBar.handleTrade can setError on failure.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const mockMutate = jest.fn().mockImplementation(async (fn: any) => {
  if (typeof fn === 'function') {
    await fn(mockPortfolio); // let any throw propagate to the caller
  }
});

describe('TradeBar', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn();
    // Wire up useSWR mock for each test — safe here because hoisting is done
    jest.mocked(useSWR).mockReturnValue({
      data: mockPortfolio,
      mutate: mockMutate,
    } as ReturnType<typeof useSWR>);
  });

  it('Test T-4-01: empty ticker blocks fetch and shows inline error', async () => {
    const { getByLabelText, getByText } = render(<TradeBar selectedTicker={null} />);

    // Clear ticker input (it might be pre-filled)
    const tickerInput = getByLabelText('Ticker');
    fireEvent.change(tickerInput, { target: { value: '' } });

    const qtyInput = getByLabelText('Qty');
    fireEvent.change(qtyInput, { target: { value: '5' } });

    fireEvent.click(getByText('Buy'));

    expect(global.fetch).not.toHaveBeenCalled();
    expect(getByText('Enter a valid ticker and quantity.')).toBeTruthy();
  });

  it('Test T-4-01b: invalid ticker (with digits) blocks fetch and shows inline error', async () => {
    const { getByLabelText, getByText } = render(<TradeBar selectedTicker={null} />);

    const tickerInput = getByLabelText('Ticker');
    fireEvent.change(tickerInput, { target: { value: 'AAPL123' } });

    const qtyInput = getByLabelText('Qty');
    fireEvent.change(qtyInput, { target: { value: '5' } });

    fireEvent.click(getByText('Buy'));

    expect(global.fetch).not.toHaveBeenCalled();
    expect(getByText('Enter a valid ticker and quantity.')).toBeTruthy();
  });

  it('Test T-4-03: quantity <= 0 blocks fetch and shows inline error', async () => {
    const { getByLabelText, getByText } = render(<TradeBar selectedTicker={null} />);

    const tickerInput = getByLabelText('Ticker');
    fireEvent.change(tickerInput, { target: { value: 'AAPL' } });

    const qtyInput = getByLabelText('Qty');
    fireEvent.change(qtyInput, { target: { value: '-1' } });

    fireEvent.click(getByText('Buy'));

    expect(global.fetch).not.toHaveBeenCalled();
    expect(getByText('Enter a valid ticker and quantity.')).toBeTruthy();
  });

  it('Test T-4-03b: non-finite quantity blocks fetch and shows inline error', async () => {
    const { getByLabelText, getByText } = render(<TradeBar selectedTicker={null} />);

    const tickerInput = getByLabelText('Ticker');
    fireEvent.change(tickerInput, { target: { value: 'AAPL' } });

    const qtyInput = getByLabelText('Qty');
    fireEvent.change(qtyInput, { target: { value: 'abc' } });

    fireEvent.click(getByText('Buy'));

    expect(global.fetch).not.toHaveBeenCalled();
    expect(getByText('Enter a valid ticker and quantity.')).toBeTruthy();
  });

  it('Test T-4-buy: clicking Buy with valid inputs calls POST /api/portfolio/trade with side "buy"', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        status: 'ok',
        ticker: 'AAPL',
        side: 'buy',
        quantity: 5,
        price: 190,
        trade_id: 'test-id',
      }),
    });

    const { getByLabelText, getByText } = render(<TradeBar selectedTicker={null} />);

    const tickerInput = getByLabelText('Ticker');
    fireEvent.change(tickerInput, { target: { value: 'AAPL' } });

    const qtyInput = getByLabelText('Qty');
    fireEvent.change(qtyInput, { target: { value: '5' } });

    await act(async () => {
      fireEvent.click(getByText('Buy'));
    });

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/portfolio/trade',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker: 'AAPL', quantity: 5, side: 'buy' }),
      })
    );
  });

  it('Test T-4-sell: clicking Sell with valid inputs calls POST /api/portfolio/trade with side "sell"', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        status: 'ok',
        ticker: 'AAPL',
        side: 'sell',
        quantity: 3,
        price: 190,
        trade_id: 'test-id-2',
      }),
    });

    const { getByLabelText, getByText } = render(<TradeBar selectedTicker={null} />);

    const tickerInput = getByLabelText('Ticker');
    fireEvent.change(tickerInput, { target: { value: 'AAPL' } });

    const qtyInput = getByLabelText('Qty');
    fireEvent.change(qtyInput, { target: { value: '3' } });

    await act(async () => {
      fireEvent.click(getByText('Sell'));
    });

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/portfolio/trade',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ ticker: 'AAPL', quantity: 3, side: 'sell' }),
      })
    );
  });

  it('Test T-4-400: 400 response shows inline error from API', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      json: async () => ({ error: 'Insufficient cash' }),
    });

    const { getByLabelText, getByText } = render(<TradeBar selectedTicker={null} />);

    const tickerInput = getByLabelText('Ticker');
    fireEvent.change(tickerInput, { target: { value: 'AAPL' } });

    const qtyInput = getByLabelText('Qty');
    fireEvent.change(qtyInput, { target: { value: '5' } });

    await act(async () => {
      fireEvent.click(getByText('Buy'));
    });

    await waitFor(() => {
      expect(getByText('Insufficient cash')).toBeTruthy();
    });
  });

  it('Test T-4-clear: submitting again clears prior error before the new attempt', async () => {
    // First submit fails with validation error
    const { getByLabelText, getByText, queryByText } = render(
      <TradeBar selectedTicker={null} />
    );

    const tickerInput = getByLabelText('Ticker');
    const qtyInput = getByLabelText('Qty');

    // Cause a validation error
    fireEvent.change(tickerInput, { target: { value: '' } });
    fireEvent.change(qtyInput, { target: { value: '5' } });
    fireEvent.click(getByText('Buy'));
    expect(getByText('Enter a valid ticker and quantity.')).toBeTruthy();

    // Now fix the ticker and try again — error should be cleared on submit
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: 'ok', ticker: 'AAPL', side: 'buy', quantity: 5, price: 190, trade_id: 'x' }),
    });
    fireEvent.change(tickerInput, { target: { value: 'AAPL' } });

    await act(async () => {
      fireEvent.click(getByText('Buy'));
    });

    // The prior error should be cleared
    expect(queryByText('Enter a valid ticker and quantity.')).toBeNull();
  });

  it('Test T-4-fill: when selectedTicker prop changes, ticker input syncs to it', () => {
    const { getByLabelText, rerender } = render(<TradeBar selectedTicker="AAPL" />);

    const tickerInput = getByLabelText('Ticker') as HTMLInputElement;
    expect(tickerInput.value).toBe('AAPL');

    rerender(<TradeBar selectedTicker="MSFT" />);
    expect(tickerInput.value).toBe('MSFT');
  });
});
