/**
 * NewsTicker tests (Batch 3 — market event feed):
 * Test 1: events render with time, directional arrow/color, and headline
 * Test 2: content is duplicated for the seamless marquee loop
 * Test 3: empty state renders the placeholder line
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import useSWR from 'swr';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

import NewsTicker from '@/components/NewsTicker';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

const mockEvents = {
  events: [
    {
      id: 'e2',
      ticker: 'NVDA',
      headline: 'NVDA surges +3.4% in sudden move',
      change_percent: 3.4,
      direction: 'up' as const,
      timestamp: 1783300000,
    },
    {
      id: 'e1',
      ticker: 'TSLA',
      headline: 'TSLA plunges -2.1% in sudden move',
      change_percent: -2.1,
      direction: 'down' as const,
      timestamp: 1783299900,
    },
  ],
};

describe('NewsTicker', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('Test 1: renders events with directional arrows and headlines', () => {
    mockUseSWR.mockReturnValue({ data: mockEvents } as any);

    render(<NewsTicker />);

    // Each headline appears (twice, due to the marquee duplication)
    expect(screen.getAllByText('NVDA surges +3.4% in sudden move').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('TSLA plunges -2.1% in sudden move').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('▲').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('▼').length).toBeGreaterThanOrEqual(1);
  });

  it('Test 2: content is duplicated for a seamless marquee loop', () => {
    mockUseSWR.mockReturnValue({ data: mockEvents } as any);

    const { container } = render(<NewsTicker />);

    const track = container.querySelector('.news-ticker-track');
    expect(track).toBeTruthy();
    // 2 events × 2 copies = 4 items
    expect(container.querySelectorAll('[data-testid^="news-item-"]')).toHaveLength(4);
  });

  it('Test 2b: an enriched event shows its LLM narrative instead of the template headline', () => {
    mockUseSWR.mockReturnValue({
      data: {
        events: [
          {
            ...mockEvents.events[0],
            narrative: 'NVDA jumps on rumored datacenter win',
          },
        ],
      },
    } as any);

    render(<NewsTicker />);

    expect(screen.getAllByText('NVDA jumps on rumored datacenter win').length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText('NVDA surges +3.4% in sudden move')).toBeNull();
  });

  it('Test 3: empty state renders the placeholder line', () => {
    mockUseSWR.mockReturnValue({ data: { events: [] } } as any);

    render(<NewsTicker />);

    expect(screen.getByText(/Market events appear here/i)).toBeInTheDocument();
    expect(document.querySelector('.news-ticker-track')).toBeNull();
  });

  it('exposes a keyboard-operable pause/resume control', () => {
    mockUseSWR.mockReturnValue({ data: mockEvents } as any);
    render(<NewsTicker />);

    const pause = screen.getByRole('button', { name: 'Pause market ticker' });
    expect(pause).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(pause);
    expect(screen.getByRole('button', { name: 'Resume market ticker' })).toHaveAttribute(
      'aria-pressed',
      'true'
    );
  });
});
