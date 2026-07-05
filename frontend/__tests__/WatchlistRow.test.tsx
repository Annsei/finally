/**
 * WatchlistRow tests (TDD):
 * Test 1: direction 'up' → price cell gains class 'animate-flash-up'
 * Test 2: direction 'down' → price cell gains class 'animate-flash-down'
 * Test 3: direction 'flat' → no flash class added
 * Test 4: flat tick clears an active flash class
 * Test 5: After 500ms (fake timers), flash class is removed from price cell
 * Test 6: isSelected=true → row has border-l-2, border-terminal-accent, bg-terminal-surface
 * Test 7: Clicking the row calls onSelect exactly once
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
  change: direction === 'down' ? -1 : direction === 'flat' ? 0 : 1,
  change_percent: direction === 'down' ? -0.53 : direction === 'flat' ? 0 : 0.53,
  direction,
});

const renderRow = (
  props: { isSelected?: boolean; onSelect?: () => void; onRemove?: () => void } = {}
) => {
  const onSelect = props.onSelect ?? jest.fn();
  const result = render(
    <table>
      <tbody>
        <WatchlistRow
          ticker="AAPL"
          isSelected={props.isSelected ?? false}
          onSelect={onSelect}
          onRemove={props.onRemove}
        />
      </tbody>
    </table>
  );
  const row = result.container.querySelector('tr')!;
  // Second <td> is the price cell (Symbol | Price | Change% | Sparkline | Remove)
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

  it('Test 1: direction up → price cell gains class animate-flash-up', () => {
    const { priceCell } = renderRow();

    act(() => {
      usePriceStore.setState({ prices: { AAPL: mkPrice('up') } });
    });

    expect(priceCell.classList.contains('animate-flash-up')).toBe(true);
    expect(priceCell.classList.contains('animate-flash-down')).toBe(false);
  });

  it('Test 2: direction down → price cell gains class animate-flash-down', () => {
    const { priceCell } = renderRow();

    act(() => {
      usePriceStore.setState({ prices: { AAPL: mkPrice('down') } });
    });

    expect(priceCell.classList.contains('animate-flash-down')).toBe(true);
    expect(priceCell.classList.contains('animate-flash-up')).toBe(false);
  });

  it('Test 3: direction flat → no flash class added', () => {
    const { priceCell } = renderRow();

    act(() => {
      usePriceStore.setState({ prices: { AAPL: mkPrice('flat') } });
    });

    expect(priceCell.classList.contains('animate-flash-up')).toBe(false);
    expect(priceCell.classList.contains('animate-flash-down')).toBe(false);
  });

  it('Test 4: flat tick clears an active flash class', () => {
    const { priceCell } = renderRow();

    act(() => {
      usePriceStore.setState({ prices: { AAPL: mkPrice('up') } });
    });
    expect(priceCell.classList.contains('animate-flash-up')).toBe(true);

    act(() => {
      usePriceStore.setState({ prices: { AAPL: mkPrice('flat', 1717700001) } });
    });

    expect(priceCell.classList.contains('animate-flash-up')).toBe(false);
    expect(priceCell.classList.contains('animate-flash-down')).toBe(false);
  });

  it('Test 5: After 500ms, flash class is removed from price cell', () => {
    const { priceCell } = renderRow();

    act(() => {
      usePriceStore.setState({ prices: { AAPL: mkPrice('up') } });
    });
    expect(priceCell.classList.contains('animate-flash-up')).toBe(true);

    act(() => {
      jest.advanceTimersByTime(500);
    });

    expect(priceCell.classList.contains('animate-flash-up')).toBe(false);
  });

  it('Test 6: isSelected=true → row has border-l-2, border-terminal-accent, bg-terminal-surface', () => {
    const { row } = renderRow({ isSelected: true });

    expect(row.classList.contains('border-l-2')).toBe(true);
    expect(row.classList.contains('border-terminal-accent')).toBe(true);
    expect(row.classList.contains('bg-terminal-surface')).toBe(true);
  });

  it('Test 7: Clicking the row calls onSelect exactly once', () => {
    const onSelect = jest.fn();
    const { row } = renderRow({ onSelect });

    fireEvent.click(row);

    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it('Test 8 (FIX 4): renders remove button with E2E test-id watchlist-remove-AAPL', () => {
    const { getByTestId } = renderRow({ onRemove: jest.fn() });

    expect(getByTestId('watchlist-remove-AAPL')).toBeTruthy();
  });

  it('Test 9 (FIX 4): clicking remove calls onRemove without triggering onSelect', () => {
    const onSelect = jest.fn();
    const onRemove = jest.fn();
    const { getByTestId } = renderRow({ onSelect, onRemove });

    fireEvent.click(getByTestId('watchlist-remove-AAPL'));

    expect(onRemove).toHaveBeenCalledTimes(1);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('Test 10 (FIX 4): no remove button rendered when onRemove prop is absent', () => {
    const { queryByTestId } = renderRow();

    expect(queryByTestId('watchlist-remove-AAPL')).toBeNull();
  });

  // ---------------------------------------------------------------------------
  // Batch-1 realism: day change vs prev close, arrows, coloring, range bar
  // ---------------------------------------------------------------------------
  it('Test 11: day change % renders with ▲ arrow and day coloring on price + change cells', () => {
    const { container, priceCell } = renderRow();

    act(() => {
      usePriceStore.setState({
        prices: {
          AAPL: {
            ...mkPrice('up'),
            prev_close: 188.0,
            day_change: 2.5,
            day_change_percent: 1.33,
            day_high: 191.0,
            day_low: 187.5,
          },
        },
      });
    });

    const changeCell = container.querySelectorAll('td')[2] as HTMLElement;
    expect(changeCell.textContent).toBe('▲+1.33%');
    expect(changeCell.className).toContain('text-terminal-up');
    expect(priceCell.className).toContain('text-terminal-up');
  });

  it('Test 12: negative day change renders ▼ with down coloring', () => {
    const { container } = renderRow();

    act(() => {
      usePriceStore.setState({
        prices: {
          AAPL: {
            ...mkPrice('down'),
            prev_close: 193.0,
            day_change: -2.5,
            day_change_percent: -1.3,
            day_high: 193.2,
            day_low: 189.9,
          },
        },
      });
    });

    const changeCell = container.querySelectorAll('td')[2] as HTMLElement;
    expect(changeCell.textContent).toBe('▼-1.30%');
    expect(changeCell.className).toContain('text-terminal-down');
  });

  it('Test 13: without day fields the change cell falls back to — and neutral color', () => {
    const { container } = renderRow();

    act(() => {
      usePriceStore.setState({ prices: { AAPL: mkPrice('up') } });
    });

    const changeCell = container.querySelectorAll('td')[2] as HTMLElement;
    expect(changeCell.textContent).toBe('—');
    expect(changeCell.className).toContain('text-terminal-muted');
    expect(container.querySelector('[data-testid="day-range-bar"]')).toBeNull();
  });

  it('Test 14: day-range bar renders with the marker positioned between low and high', () => {
    const { container } = renderRow();

    act(() => {
      usePriceStore.setState({
        prices: {
          AAPL: {
            ...mkPrice('up'),
            price: 190.0, // (190 − 188) / (192 − 188) = 50%
            prev_close: 188.0,
            day_change: 2.0,
            day_change_percent: 1.06,
            day_high: 192.0,
            day_low: 188.0,
          },
        },
      });
    });

    const bar = container.querySelector('[data-testid="day-range-bar"]') as HTMLElement;
    expect(bar).toBeTruthy();
    expect(bar.title).toBe('Day range 188.00 – 192.00');
    const marker = bar.firstElementChild as HTMLElement;
    expect(marker.style.left).toBe('calc(50% - 1px)');
  });
});
