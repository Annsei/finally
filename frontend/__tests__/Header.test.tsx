/**
 * Header.test.tsx — TDD tests for Header component (FE-02)
 *
 * Tests:
 * 1. With connectionStatus 'connected' in the store, the dot has class bg-terminal-up
 * 2. With 'reconnecting', the dot has class bg-terminal-amber
 * 3. With 'disconnected', the dot has class bg-terminal-down
 * 4. When SWR returns { cash: 10000, total_value: 12345.67 }, the output contains '10,000' and '12,345.67'
 * 5. Before data loads (undefined), cash/value render '—' without throwing
 */
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { usePriceStore } from '@/stores/priceStore';

// Mock swr to control what data is returned
jest.mock('swr', () => {
  // We'll override __mockData per test
  const mockSWR = jest.fn();
  return { __esModule: true, default: mockSWR };
});

// Import swr after mock so we can configure it per test
import useSWR from 'swr';
const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

// Import Header AFTER mocking swr
import Header from '@/components/Header';

describe('Header component', () => {
  beforeEach(() => {
    // Reset Zustand store to initial state before each test
    usePriceStore.setState({ connectionStatus: 'disconnected', prices: {} });
    // Default: SWR returns undefined (loading state)
    mockUseSWR.mockReturnValue({ data: undefined } as any);
  });

  it('Test 1: dot has class bg-terminal-up when connectionStatus is connected', () => {
    usePriceStore.setState({ connectionStatus: 'connected' });
    render(<Header />);
    const dot = screen.getByTitle('connected');
    expect(dot.className).toContain('bg-terminal-up');
  });

  it('Test 2: dot has class bg-terminal-amber when connectionStatus is reconnecting', () => {
    usePriceStore.setState({ connectionStatus: 'reconnecting' });
    render(<Header />);
    const dot = screen.getByTitle('reconnecting');
    expect(dot.className).toContain('bg-terminal-amber');
  });

  it('Test 3: dot has class bg-terminal-down when connectionStatus is disconnected', () => {
    usePriceStore.setState({ connectionStatus: 'disconnected' });
    render(<Header />);
    const dot = screen.getByTitle('disconnected');
    expect(dot.className).toContain('bg-terminal-down');
  });

  it('Test 4: renders formatted cash and total_value when SWR returns portfolio data', async () => {
    mockUseSWR.mockReturnValue({
      data: { cash: 10000, total_value: 12345.67, positions: [] },
    } as any);
    render(<Header />);
    // Check formatted cash value — toLocaleString('en-US', { minimumFractionDigits: 2 }) = '10,000.00'
    expect(screen.getByText(/10,000/)).toBeTruthy();
    // Check formatted portfolio value — '12,345.67'
    expect(screen.getByText(/12,345\.67/)).toBeTruthy();
  });

  it('Test 4b (FIX 4): connection dot exposes data-testid="connection-status" and data-state', () => {
    for (const status of ['connected', 'reconnecting', 'disconnected'] as const) {
      usePriceStore.setState({ connectionStatus: status });
      const { unmount } = render(<Header />);
      const dot = screen.getByTestId('connection-status');
      expect(dot.getAttribute('data-state')).toBe(status);
      unmount();
    }
  });

  it('Test 5: renders — placeholder for cash and total_value before data loads', () => {
    mockUseSWR.mockReturnValue({ data: undefined } as any);
    // Should not throw; should render '—' placeholders
    expect(() => render(<Header />)).not.toThrow();
    // The '—' is rendered inside a span alongside '$', so we use getAllByText with exact:false
    // to find elements containing the dash placeholder text
    const dashes = screen.getAllByText(/—/);
    expect(dashes.length).toBeGreaterThanOrEqual(2);
  });

  // ---------------------------------------------------------------------------
  // Batch-2 realism: Day P&L = Σ qty × (price − prev_close) over positions
  // ---------------------------------------------------------------------------
  it('Test 6: Day P&L computes from live prices vs prev_close, colored by sign', () => {
    mockUseSWR.mockReturnValue({
      data: {
        cash: 5000,
        total_value: 8810,
        positions: [
          { ticker: 'AAPL', quantity: 10, avg_cost: 185, current_price: 190.5, unrealized_pnl: 55, pnl_pct: 2.97 },
          { ticker: 'NVDA', quantity: 2, avg_cost: 900, current_price: 880, unrealized_pnl: -40, pnl_pct: -2.2 },
        ],
      },
    } as any);
    usePriceStore.setState({
      prices: {
        AAPL: {
          ticker: 'AAPL', price: 190.5, previous_price: 190.4, timestamp: 1, change: 0.1,
          change_percent: 0.05, direction: 'up', prev_close: 188.0,
        },
        NVDA: {
          ticker: 'NVDA', price: 880.0, previous_price: 881.0, timestamp: 1, change: -1,
          change_percent: -0.11, direction: 'down', prev_close: 890.0,
        },
      } as any,
    });

    render(<Header />);

    // AAPL: 10 × (190.5 − 188) = +25; NVDA: 2 × (880 − 890) = −20 → +$5.00
    const dayPnl = screen.getByTestId('day-pnl');
    expect(dayPnl.textContent).toBe('+$5.00');
    expect(dayPnl.className).toContain('text-terminal-up');
  });

  it('Test 6b: negative Day P&L renders -$ with down coloring', () => {
    mockUseSWR.mockReturnValue({
      data: {
        cash: 5000,
        total_value: 6760,
        positions: [
          { ticker: 'NVDA', quantity: 2, avg_cost: 900, current_price: 880, unrealized_pnl: -40, pnl_pct: -2.2 },
        ],
      },
    } as any);
    usePriceStore.setState({
      prices: {
        NVDA: {
          ticker: 'NVDA', price: 880.0, previous_price: 881.0, timestamp: 1, change: -1,
          change_percent: -0.11, direction: 'down', prev_close: 890.0,
        },
      } as any,
    });

    render(<Header />);

    const dayPnl = screen.getByTestId('day-pnl');
    expect(dayPnl.textContent).toBe('-$20.00');
    expect(dayPnl.className).toContain('text-terminal-down');
  });

  it('Test 6c: Day P&L shows $0.00 with no positions and — before portfolio loads', () => {
    mockUseSWR.mockReturnValue({ data: { cash: 10000, total_value: 10000, positions: [] } } as any);
    const { unmount } = render(<Header />);
    expect(screen.getByTestId('day-pnl').textContent).toBe('$0.00');
    unmount();

    mockUseSWR.mockReturnValue({ data: undefined } as any);
    render(<Header />);
    expect(screen.getByTestId('day-pnl').textContent).toBe('—');
  });
});
