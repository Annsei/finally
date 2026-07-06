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
    } as unknown as ReturnType<typeof useSWR>);
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

  // ---------------------------------------------------------------------------
  // Batch-1 realism: live estimate, max-buy / held shortcuts, fill toast
  // (position current_price 190.00 is the price source; cash is $10,000)
  // ---------------------------------------------------------------------------
  it('Test T-4-est: estimate row shows qty × price notional', () => {
    const { getByLabelText, getByTestId } = render(<TradeBar selectedTicker="AAPL" />);

    fireEvent.change(getByLabelText('Qty'), { target: { value: '5' } });

    // 5 × $190.00 = $950.00
    expect(getByTestId('trade-estimate').textContent).toContain('$950.00');
  });

  it('Test T-4-max: clicking Max buy fills qty with cash ÷ price (4dp floor)', () => {
    const { getByLabelText, getByTestId } = render(<TradeBar selectedTicker="AAPL" />);

    // 10000 / 190 = 52.63157… → floored to 52.6315
    fireEvent.click(getByTestId('trade-max-buy'));
    expect((getByLabelText('Qty') as HTMLInputElement).value).toBe('52.6315');
  });

  it('Test T-4-held: clicking Held fills qty with the full position', () => {
    const { getByLabelText, getByTestId } = render(<TradeBar selectedTicker="AAPL" />);

    fireEvent.click(getByTestId('trade-held'));
    expect((getByLabelText('Qty') as HTMLInputElement).value).toBe('10');
  });

  it('Test T-4-quote: bid × ask renders when the live update carries a quote', () => {
    const { usePriceStore } = jest.requireActual('@/stores/priceStore');
    const { getByTestId } = render(<TradeBar selectedTicker="AAPL" />);

    act(() => {
      usePriceStore.setState({
        prices: {
          AAPL: {
            ticker: 'AAPL', price: 190.0, previous_price: 189.9, timestamp: 1, change: 0.1,
            change_percent: 0.05, direction: 'up', bid: 189.98, ask: 190.02,
          },
        },
      });
    });

    expect(getByTestId('trade-bid-ask').textContent).toBe('Bid 189.98 × Ask 190.02');
  });

  // ---------------------------------------------------------------------------
  // Batch-3.2: limit orders
  // ---------------------------------------------------------------------------
  it('Test T-4-lmt: switching to Lmt reveals the limit price input; invalid limit blocks fetch', () => {
    const { getByTestId, getByLabelText, getByText, queryByLabelText } = render(
      <TradeBar selectedTicker="AAPL" />
    );

    expect(queryByLabelText('Limit price')).toBeNull();
    fireEvent.click(getByTestId('order-type-limit'));
    expect(getByLabelText('Limit price')).toBeTruthy();

    fireEvent.change(getByLabelText('Qty'), { target: { value: '5' } });
    fireEvent.click(getByText('Buy'));

    expect(global.fetch).not.toHaveBeenCalled();
    expect(getByText('Enter a valid limit price.')).toBeTruthy();
  });

  it('Test T-4-lmt-open: a resting limit order POSTs to /api/portfolio/orders and toasts placement', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        order: {
          id: 'o1', ticker: 'AAPL', side: 'buy', quantity: 5, limit_price: 185,
          status: 'open', reject_reason: null, created_at: '2026-07-06T00:00:00Z',
          filled_at: null, fill_price: null,
        },
      }),
    });

    const { getByTestId, getByLabelText, getByText } = render(<TradeBar selectedTicker="AAPL" />);

    fireEvent.click(getByTestId('order-type-limit'));
    fireEvent.change(getByLabelText('Qty'), { target: { value: '5' } });
    fireEvent.change(getByLabelText('Limit price'), { target: { value: '185' } });

    await act(async () => {
      fireEvent.click(getByText('Buy'));
    });

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/portfolio/orders',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          ticker: 'AAPL',
          quantity: 5,
          side: 'buy',
          kind: 'limit',
          limit_price: 185,
          time_in_force: 'gtc',
        }),
      })
    );
    await waitFor(() => {
      expect(getByTestId('trade-toast').textContent).toContain('Order placed: Buy 5 AAPL @ ≤$185.00');
    });
  });

  it('Test T-4-stp: stop order shows Stop input, POSTs kind=stop with DAY tif, toasts placement', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        order: {
          id: 'o3', ticker: 'AAPL', side: 'sell', quantity: 5, kind: 'stop',
          limit_price: null, stop_price: 180, time_in_force: 'day',
          expires_at: '2026-07-07T00:00:00Z', triggered_at: null,
          status: 'open', reject_reason: null, created_at: '2026-07-06T00:00:00Z',
          filled_at: null, fill_price: null,
        },
      }),
    });

    const { getByTestId, getByLabelText, getByText, queryByLabelText } = render(
      <TradeBar selectedTicker="AAPL" />
    );

    fireEvent.click(getByTestId('order-type-stop'));
    expect(getByLabelText('Stop price')).toBeTruthy();
    expect(queryByLabelText('Limit price')).toBeNull(); // pure stop has no limit input

    fireEvent.change(getByLabelText('Qty'), { target: { value: '5' } });
    fireEvent.change(getByLabelText('Stop price'), { target: { value: '180' } });
    fireEvent.click(getByTestId('tif-day'));

    await act(async () => {
      fireEvent.click(getByText('Sell'));
    });

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/portfolio/orders',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          ticker: 'AAPL',
          quantity: 5,
          side: 'sell',
          kind: 'stop',
          stop_price: 180,
          time_in_force: 'day',
        }),
      })
    );
    await waitFor(() => {
      expect(getByTestId('trade-toast').textContent).toContain(
        'Stop placed: Sell 5 AAPL @ stop $180.00'
      );
    });
  });

  it('Test T-4-stp-val: missing stop price blocks fetch with inline error', () => {
    const { getByTestId, getByLabelText, getByText } = render(<TradeBar selectedTicker="AAPL" />);

    fireEvent.click(getByTestId('order-type-stop'));
    fireEvent.change(getByLabelText('Qty'), { target: { value: '5' } });
    fireEvent.click(getByText('Sell'));

    expect(global.fetch).not.toHaveBeenCalled();
    expect(getByText('Enter a valid stop price.')).toBeTruthy();
  });

  it('Test T-4-conc: an oversized buy shows the concentration warning (non-blocking)', () => {
    // portfolio: total 10000, held 10 AAPL @190 — buying 15 more @190 → (10+15)*190/10000 = 47.5%
    const { getByLabelText, getByTestId, queryByTestId } = render(
      <TradeBar selectedTicker="AAPL" />
    );

    fireEvent.change(getByLabelText('Qty'), { target: { value: '15' } });
    expect(getByTestId('trade-concentration-warning').textContent).toContain('AAPL');
    expect(getByTestId('trade-concentration-warning').textContent).toContain('48%');

    // small buy → warning gone: (10+1)*190/10000 = 20.9%
    fireEvent.change(getByLabelText('Qty'), { target: { value: '1' } });
    expect(queryByTestId('trade-concentration-warning')).toBeNull();
  });

  it('Test T-4-lmt-fill: an immediately-filled (marketable) limit order toasts the fill', async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => ({
        order: {
          id: 'o2', ticker: 'AAPL', side: 'sell', quantity: 3, limit_price: 150,
          status: 'filled', reject_reason: null, created_at: '2026-07-06T00:00:00Z',
          filled_at: '2026-07-06T00:00:00Z', fill_price: 189.95,
        },
      }),
    });

    const { getByTestId, getByLabelText, getByText } = render(<TradeBar selectedTicker="AAPL" />);

    fireEvent.click(getByTestId('order-type-limit'));
    fireEvent.change(getByLabelText('Qty'), { target: { value: '3' } });
    fireEvent.change(getByLabelText('Limit price'), { target: { value: '150' } });

    await act(async () => {
      fireEvent.click(getByText('Sell'));
    });

    await waitFor(() => {
      expect(getByTestId('trade-toast').textContent).toContain('Sold 3 AAPL @ $189.95');
    });
  });

  it('Test T-4-toast: successful fill shows a toast with side, qty, ticker and price', async () => {
    (global.fetch as jest.Mock).mockImplementation(async (url: string) => {
      if (url === '/api/portfolio/trade') {
        return {
          ok: true,
          json: async () => ({
            status: 'ok',
            ticker: 'AAPL',
            side: 'buy',
            quantity: 5,
            price: 190.02,
            trade_id: 'toast-id',
          }),
        };
      }
      // fresh portfolio snapshot fetched by the mutator
      return { ok: true, json: async () => mockPortfolio };
    });

    const { getByLabelText, getByText, getByTestId } = render(
      <TradeBar selectedTicker={null} />
    );

    fireEvent.change(getByLabelText('Ticker'), { target: { value: 'AAPL' } });
    fireEvent.change(getByLabelText('Qty'), { target: { value: '5' } });

    await act(async () => {
      fireEvent.click(getByText('Buy'));
    });

    await waitFor(() => {
      expect(getByTestId('trade-toast').textContent).toContain('Bought 5 AAPL @ $190.02');
    });
  });
});
