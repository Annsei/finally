/**
 * WatchlistRow tests (TDD):
 * Test 1: direction 'up' → price cell gains class 'flash-up'
 * Test 2: direction 'down' → price cell gains class 'flash-down'
 * Test 3: direction 'flat' → no flash class added
 * Test 4: After 500ms (fake timers), flash class is removed from price cell
 * Test 5: isSelected=true → row has border-l-2, border-terminal-accent, bg-terminal-surface
 * Test 6: Clicking the row calls onSelect exactly once
 */
import React from 'react';
import { render, act, fireEvent } from '@testing-library/react';
import { usePriceStore } from '@/stores/priceStore';
import type { PriceUpdate } from '@/types/market';

jest.mock('@/components/SparklineChart', () => ({
  __esModule: true,
  default: ({ ticker }: { ticker: string }) => <div data-testid={`sparkline-${ticker}`} />,
}));

import WatchlistRow from '@/components/WatchlistRow';

const mkPrice = (direction: 'up' | 'down' | 'flat', ts = 1717700000): PriceUpdate => ({
  ticker: 'AAPL',
  price: 190.5,
  previous_price: 189.5,
  timestamp: ts,
  change: direction === 'down' ? -1 : 1,
  change_percent: direction === 'down' ? -0.53 : 0.53,
  direction,
});

const renderRow = (props: { isSelected?: boolean; onSelect?: () => void } = {}) => {
  const onSelect = props.onSelect ?? jest.fn();
  const result = render(
    <table>
      <tbody>
        <WatchlistRow
          ticker="AAPL"
          isSelected={props.isSelected ?? false}
          onSelect={onSelect}
        />
      </tbody>
    </table>
  );
  const row = result.container.querySelector('tr')!;
  // Second <td> is the price cell (Symbol | Price | Change% | Sparkline)
  const tds = result.container.querySelectorAll('td');
  const priceCell = tds[1] as HTMLElement;
  return { ...result, row, priceCell, onSelect };
};

describe('WatchlistRow', () => {
  beforeEach(() => {
    usePriceStore.setState({ prices: {}, connectionStatus: 'disconnected' });
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('Test 1: direction up → price cell gains class flash-up', () => {
    const { priceCell } = renderRow();

    act(() => {
      usePriceStore.setState({ prices: { AAPL: mkPrice('up') } });
    });

    expect(priceCell.classList.contains('flash-up')).toBe(true);
    expect(priceCell.classList.contains('flash-down')).toBe(false);
  });

  it('Test 2: direction down → price cell gains class flash-down', () => {
    const { priceCell } = renderRow();

    act(() => {
      usePriceStore.setState({ prices: { AAPL: mkPrice('down') } });
    });

    expect(priceCell.classList.contains('flash-down')).toBe(true);
    expect(priceCell.classList.contains('flash-up')).toBe(false);
  });

  it('Test 3: direction flat → no flash class added', () => {
    const { priceCell } = renderRow();

    act(() => {
      usePriceStore.setState({ prices: { AAPL: mkPrice('flat') } });
    });

    expect(priceCell.classList.contains('flash-up')).toBe(false);
    expect(priceCell.classList.contains('flash-down')).toBe(false);
  });

  it('Test 4: After 500ms, flash class is removed from price cell', () => {
    const { priceCell } = renderRow();

    act(() => {
      usePriceStore.setState({ prices: { AAPL: mkPrice('up') } });
    });
    expect(priceCell.classList.contains('flash-up')).toBe(true);

    act(() => {
      jest.advanceTimersByTime(500);
    });

    expect(priceCell.classList.contains('flash-up')).toBe(false);
  });

  it('Test 5: isSelected=true → row has border-l-2, border-terminal-accent, bg-terminal-surface', () => {
    const { row } = renderRow({ isSelected: true });

    expect(row.classList.contains('border-l-2')).toBe(true);
    expect(row.classList.contains('border-terminal-accent')).toBe(true);
    expect(row.classList.contains('bg-terminal-surface')).toBe(true);
  });

  it('Test 6: Clicking the row calls onSelect exactly once', () => {
    const onSelect = jest.fn();
    const { row } = renderRow({ onSelect });

    fireEvent.click(row);

    expect(onSelect).toHaveBeenCalledTimes(1);
  });
});
