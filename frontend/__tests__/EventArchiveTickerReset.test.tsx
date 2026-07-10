import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import useSWR from 'swr';
import { fetcher } from '@/lib/fetcher';
import type { MarketEvent } from '@/types/market';

jest.mock('swr', () => ({ __esModule: true, default: jest.fn() }));
jest.mock('@/lib/fetcher', () => ({ fetcher: jest.fn() }));

import EventArchive from '@/components/EventArchive';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;
const mockFetcher = fetcher as jest.MockedFunction<typeof fetcher>;

function event(id: string, ticker: string, timestamp: number): MarketEvent {
  return {
    id,
    ticker,
    headline: `${ticker} event`,
    change_percent: 1,
    direction: 'up',
    timestamp,
  };
}

describe('EventArchive ticker identity', () => {
  it('drops accumulated pages when the ticker changes', async () => {
    const newestA = event('a-new', 'AAPL', 300);
    const olderA = event('a-old', 'AAPL', 200);
    const newestB = event('b-new', 'MSFT', 400);

    mockUseSWR.mockImplementation(((key: string) => {
      if (key.includes('ticker=AAPL')) {
        return { data: { events: [newestA], has_more: true }, mutate: jest.fn() };
      }
      if (key.includes('ticker=MSFT')) {
        return { data: { events: [newestB], has_more: false }, mutate: jest.fn() };
      }
      return { data: undefined, mutate: jest.fn() };
    }) as never);
    mockFetcher.mockResolvedValue({ events: [olderA], has_more: false });

    const { rerender } = render(
      <EventArchive prefix="symbol" ticker="AAPL" emptyKey="symbol.eventsEmpty" />
    );
    fireEvent.click(screen.getByTestId('symbol-events-more'));
    await waitFor(() => expect(screen.getByTestId('symbol-event-a-old')).toBeInTheDocument());

    rerender(<EventArchive prefix="symbol" ticker="MSFT" emptyKey="symbol.eventsEmpty" />);

    expect(screen.getByTestId('symbol-event-b-new')).toBeInTheDocument();
    expect(screen.queryByTestId('symbol-event-a-new')).not.toBeInTheDocument();
    expect(screen.queryByTestId('symbol-event-a-old')).not.toBeInTheDocument();
  });
});
