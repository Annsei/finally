/**
 * WatchlistPanel tests (TDD):
 * Test 1: SWR returns 2 tickers → renders a row for each
 * Test 2: Panel renders column headers Symbol, Price, Change %
 * Test 3: Before data loads (undefined) → shows empty-state heading 'No prices yet'
 * Test 4: Clicking a row updates selected ticker (only one selected at a time)
 */
import React, { useState } from 'react';
import { render, screen, fireEvent } from '@testing-library/react';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

jest.mock('@/components/WatchlistRow', () => ({
  __esModule: true,
  default: ({
    ticker,
    isSelected,
    onSelect,
  }: {
    ticker: string;
    isSelected: boolean;
    onSelect: () => void;
  }) => (
    <tr
      data-testid={`row-${ticker}`}
      data-selected={String(isSelected)}
      onClick={onSelect}
    >
      <td>{ticker}</td>
    </tr>
  ),
}));

import useSWR from 'swr';
const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

import WatchlistPanel from '@/components/WatchlistPanel';

const mockWatchlistData = {
  tickers: [
    { ticker: 'AAPL', added_at: '', price: 190, change_percent: 0.5, direction: 'up' as const },
    { ticker: 'GOOGL', added_at: '', price: 175, change_percent: -0.3, direction: 'down' as const },
  ],
};

function TestWrapper() {
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  return (
    <WatchlistPanel selectedTicker={selectedTicker} onSelectTicker={setSelectedTicker} />
  );
}

describe('WatchlistPanel', () => {
  beforeEach(() => {
    mockUseSWR.mockReturnValue({ data: undefined } as any);
  });

  it('Test 1: SWR returns data → renders a row per ticker', () => {
    mockUseSWR.mockReturnValue({ data: mockWatchlistData } as any);
    render(<TestWrapper />);

    expect(screen.getByTestId('row-AAPL')).toBeInTheDocument();
    expect(screen.getByTestId('row-GOOGL')).toBeInTheDocument();
  });

  it('Test 2: Renders column headers Symbol, Price, Change %', () => {
    mockUseSWR.mockReturnValue({ data: mockWatchlistData } as any);
    render(<WatchlistPanel selectedTicker={null} onSelectTicker={jest.fn()} />);

    expect(screen.getByText('Symbol')).toBeInTheDocument();
    expect(screen.getByText('Price')).toBeInTheDocument();
    expect(screen.getByText('Change %')).toBeInTheDocument();
  });

  it('Test 3: Before data loads → shows empty-state heading No prices yet', () => {
    mockUseSWR.mockReturnValue({ data: undefined } as any);
    render(<WatchlistPanel selectedTicker={null} onSelectTicker={jest.fn()} />);

    expect(screen.getByText('No prices yet')).toBeInTheDocument();
  });

  it('Test 4: Clicking a row updates selected ticker; only one selected at a time', () => {
    mockUseSWR.mockReturnValue({ data: mockWatchlistData } as any);
    render(<TestWrapper />);

    const aaplRow = screen.getByTestId('row-AAPL');
    const googlRow = screen.getByTestId('row-GOOGL');

    expect(aaplRow.getAttribute('data-selected')).toBe('false');
    expect(googlRow.getAttribute('data-selected')).toBe('false');

    fireEvent.click(aaplRow);
    expect(aaplRow.getAttribute('data-selected')).toBe('true');
    expect(googlRow.getAttribute('data-selected')).toBe('false');

    fireEvent.click(googlRow);
    expect(googlRow.getAttribute('data-selected')).toBe('true');
    expect(aaplRow.getAttribute('data-selected')).toBe('false');
  });
});
