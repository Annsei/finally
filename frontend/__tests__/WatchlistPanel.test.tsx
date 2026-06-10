/**
 * WatchlistPanel tests (TDD):
 * Test 1: SWR returns 2 tickers → renders a row for each
 * Test 2: Panel renders column headers Symbol, Price, Change %
 * Test 3: Before data loads (undefined) → shows empty-state heading 'No prices yet'
 * Test 4: Clicking a row updates selected ticker (only one selected at a time)
 * FIX 4 suite: manual add/remove UI — input validation, endpoint wiring, inline errors
 */
import React, { useState } from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

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
    onRemove,
  }: {
    ticker: string;
    isSelected: boolean;
    onSelect: () => void;
    onRemove?: () => void;
  }) => (
    <tr
      data-testid={`row-${ticker}`}
      data-selected={String(isSelected)}
      onClick={onSelect}
    >
      <td>
        {ticker}
        {onRemove && (
          <button
            data-testid={`watchlist-remove-${ticker}`}
            onClick={(e) => {
              e.stopPropagation();
              onRemove();
            }}
          >
            ×
          </button>
        )}
      </td>
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

// ---------------------------------------------------------------------------
// FIX 4: manual watchlist add/remove UI
// ---------------------------------------------------------------------------
describe('WatchlistPanel add/remove UI (FIX 4)', () => {
  const mockMutate = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    mockUseSWR.mockReturnValue({ data: mockWatchlistData, mutate: mockMutate } as any);
    global.fetch = jest.fn();
  });

  it('renders the add input and button with the E2E test-id contract', () => {
    render(<WatchlistPanel selectedTicker={null} onSelectTicker={jest.fn()} />);

    expect(screen.getByTestId('watchlist-add-input')).toBeInTheDocument();
    expect(screen.getByTestId('watchlist-add-button')).toBeInTheDocument();
  });

  it('add input is still rendered when the watchlist is empty (can add to empty list)', () => {
    mockUseSWR.mockReturnValue({ data: { tickers: [] }, mutate: mockMutate } as any);
    render(<WatchlistPanel selectedTicker={null} onSelectTicker={jest.fn()} />);

    expect(screen.getByTestId('watchlist-add-input')).toBeInTheDocument();
    expect(screen.getByText('No prices yet')).toBeInTheDocument();
  });

  it('uppercases typed input', () => {
    render(<WatchlistPanel selectedTicker={null} onSelectTicker={jest.fn()} />);

    const input = screen.getByTestId('watchlist-add-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'pypl' } });
    expect(input.value).toBe('PYPL');
  });

  it('valid add → POST /api/watchlist/ with {ticker}, mutates SWR key, clears input', async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ status: 'ok', ticker: 'PYPL' }),
    });

    render(<WatchlistPanel selectedTicker={null} onSelectTicker={jest.fn()} />);

    const input = screen.getByTestId('watchlist-add-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'PYPL' } });
    fireEvent.click(screen.getByTestId('watchlist-add-button'));

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith('/api/watchlist/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker: 'PYPL' }),
      });
    });
    await waitFor(() => expect(mockMutate).toHaveBeenCalled());
    expect(input.value).toBe('');
  });

  it('Enter key in the add input submits the add', async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ status: 'ok', ticker: 'NFLX' }),
    });

    render(<WatchlistPanel selectedTicker={null} onSelectTicker={jest.fn()} />);

    const input = screen.getByTestId('watchlist-add-input');
    fireEvent.change(input, { target: { value: 'NFLX' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
  });

  it('invalid ticker (digits) → inline error, no network call', async () => {
    render(<WatchlistPanel selectedTicker={null} onSelectTicker={jest.fn()} />);

    fireEvent.change(screen.getByTestId('watchlist-add-input'), { target: { value: 'A1' } });
    fireEvent.click(screen.getByTestId('watchlist-add-button'));

    expect(await screen.findByTestId('watchlist-error')).toHaveTextContent(
      'Ticker must be 1-10 letters (A-Z).'
    );
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('duplicate ticker → inline error, no network call', async () => {
    render(<WatchlistPanel selectedTicker={null} onSelectTicker={jest.fn()} />);

    // AAPL is already in mockWatchlistData
    fireEvent.change(screen.getByTestId('watchlist-add-input'), { target: { value: 'AAPL' } });
    fireEvent.click(screen.getByTestId('watchlist-add-button'));

    expect(await screen.findByTestId('watchlist-error')).toHaveTextContent(
      'AAPL is already in the watchlist.'
    );
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('backend 400 on add → inline error from response body', async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: 'Ticker must be 10 characters or fewer' }),
    });

    render(<WatchlistPanel selectedTicker={null} onSelectTicker={jest.fn()} />);

    fireEvent.change(screen.getByTestId('watchlist-add-input'), { target: { value: 'PYPL' } });
    fireEvent.click(screen.getByTestId('watchlist-add-button'));

    expect(await screen.findByTestId('watchlist-error')).toHaveTextContent(
      'Ticker must be 10 characters or fewer'
    );
    expect(mockMutate).not.toHaveBeenCalled();
  });

  it('remove control → DELETE /api/watchlist/{ticker} and mutates SWR key', async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ status: 'ok', ticker: 'AAPL' }),
    });

    render(<WatchlistPanel selectedTicker={null} onSelectTicker={jest.fn()} />);

    fireEvent.click(screen.getByTestId('watchlist-remove-AAPL'));

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith('/api/watchlist/AAPL', { method: 'DELETE' });
    });
    await waitFor(() => expect(mockMutate).toHaveBeenCalled());
  });

  it('failed remove → inline error shown', async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ error: 'Database locked' }),
    });

    render(<WatchlistPanel selectedTicker={null} onSelectTicker={jest.fn()} />);

    fireEvent.click(screen.getByTestId('watchlist-remove-AAPL'));

    expect(await screen.findByTestId('watchlist-error')).toHaveTextContent('Database locked');
  });
});
