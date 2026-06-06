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

  it('Test 5: renders — placeholder for cash and total_value before data loads', () => {
    mockUseSWR.mockReturnValue({ data: undefined } as any);
    // Should not throw; should render '—' placeholders
    expect(() => render(<Header />)).not.toThrow();
    const dashes = screen.getAllByText('—');
    expect(dashes.length).toBeGreaterThanOrEqual(2);
  });
});
