/**
 * MarketCorrelation.test.tsx — /market correlation heatmap (P4 §2).
 *
 * Pure helpers: corrIntensity (|r| → 0..100, clamped), corrColor
 *               (CONTRACT-PINNED blue #209dd7 positive / purple #753991
 *               negative — never the up/down direction colours),
 *               sectorBoundaries / sectorLegend.
 * Rendering:    NxN cells with market-corr-A-B testids, hover titles,
 *               diagonal muting, sector legend chips, i18n empty state.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import useSWR from 'swr';
import type { MarketCorrelationResponse } from '@/types/market';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

import MarketCorrelation, {
  CORR_POS,
  CORR_NEG,
  corrIntensity,
  corrColor,
  sectorBoundaries,
  sectorLegend,
} from '@/components/MarketCorrelation';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

const CORR_KEY = '/api/market/correlation?minutes=30';

function mockData(opts: { corr?: MarketCorrelationResponse; profile?: Record<string, unknown> }) {
  mockUseSWR.mockImplementation(((key: string) => {
    if (key === CORR_KEY && opts.corr) return { data: opts.corr, mutate: jest.fn() };
    if (key === '/api/market/profile' && opts.profile) {
      return { data: opts.profile, mutate: jest.fn() };
    }
    return { data: undefined, mutate: jest.fn() };
  }) as never);
}

const matrix3: MarketCorrelationResponse = {
  tickers: ['AAPL', 'MSFT', 'JPM'],
  sectors: { AAPL: 'tech', MSFT: 'tech', JPM: 'financials' },
  matrix: [
    [1.0, 0.83, -0.4],
    [0.83, 1.0, 0.0],
    [-0.4, 0.0, 1.0],
  ],
  minutes: 30,
};

describe('correlation helpers (P4 §2)', () => {
  it('corrIntensity is |r| as a 0..100 percentage, clamped', () => {
    expect(corrIntensity(1)).toBe(100);
    expect(corrIntensity(-1)).toBe(100);
    expect(corrIntensity(0.5)).toBe(50);
    expect(corrIntensity(-0.83)).toBe(83);
    expect(corrIntensity(0)).toBe(0);
    expect(corrIntensity(1.7)).toBe(100); // clamp
    expect(corrIntensity(undefined)).toBe(0);
    expect(corrIntensity(null)).toBe(0);
    expect(corrIntensity(NaN)).toBe(0);
  });

  it('corrColor mixes blue for positive r and purple for negative r at |r| intensity', () => {
    expect(corrColor(0.83)).toBe(`color-mix(in srgb, ${CORR_POS} 83%, transparent)`);
    expect(corrColor(-0.4)).toBe(`color-mix(in srgb, ${CORR_NEG} 40%, transparent)`);
    expect(corrColor(0)).toBe('transparent');
  });

  it('the pinned palette is blue/purple — NOT the up/down direction colours', () => {
    expect(CORR_POS).toBe('#209dd7');
    expect(CORR_NEG).toBe('#753991');
    for (const r of [1, 0.5, -0.5, -1]) {
      const c = corrColor(r);
      expect(c).not.toContain('var(--color-up)');
      expect(c).not.toContain('var(--color-down)');
      expect(c).not.toContain('#22c55e');
      expect(c).not.toContain('#ef4444');
    }
  });

  it('sectorBoundaries flags the first ticker of each new sector group', () => {
    expect(sectorBoundaries(matrix3.tickers, matrix3.sectors)).toEqual([false, false, true]);
    expect(sectorBoundaries([], {})).toEqual([]);
  });

  it('sectorLegend lists sectors once, in ticker order', () => {
    expect(sectorLegend(matrix3.tickers, matrix3.sectors)).toEqual(['tech', 'financials']);
    expect(sectorLegend(['X'], {})).toEqual(['other']);
  });
});

describe('MarketCorrelation (P4 §2)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockData({});
  });

  it('shows the i18n empty state while loading and when tickers is empty', () => {
    render(<MarketCorrelation />);
    expect(screen.getByTestId('market-correlation').textContent).toContain(
      'Not enough bar history yet'
    );
  });

  it('empty tickers payload (open-of-day) also renders the empty state', () => {
    mockData({ corr: { tickers: [], sectors: {}, matrix: [], minutes: 30 } });
    render(<MarketCorrelation />);
    expect(screen.getByTestId('market-correlation').textContent).toContain(
      'Not enough bar history yet'
    );
  });

  it('renders an NxN grid of market-corr-A-B cells with polarity attributes', () => {
    mockData({ corr: matrix3 });
    render(<MarketCorrelation />);

    // all 9 cells exist
    for (const a of matrix3.tickers) {
      for (const b of matrix3.tickers) {
        expect(screen.getByTestId(`market-corr-${a}-${b}`)).toBeTruthy();
      }
    }

    const pos = screen.getByTestId('market-corr-AAPL-MSFT');
    expect(pos.getAttribute('data-polarity')).toBe('pos');
    expect(pos.getAttribute('data-corr')).toBe('0.83');

    const neg = screen.getByTestId('market-corr-AAPL-JPM');
    expect(neg.getAttribute('data-polarity')).toBe('neg');
    expect(neg.getAttribute('data-corr')).toBe('-0.40');

    const diag = screen.getByTestId('market-corr-AAPL-AAPL');
    expect(diag.getAttribute('data-polarity')).toBe('diag');
  });

  it('cells carry the "A×B r=…" hover title', () => {
    mockData({ corr: matrix3 });
    render(<MarketCorrelation />);
    expect(screen.getByTestId('market-corr-AAPL-MSFT').getAttribute('title')).toBe(
      'AAPL×MSFT r=0.83'
    );
    expect(screen.getByTestId('market-corr-AAPL-JPM').getAttribute('title')).toBe(
      'AAPL×JPM r=-0.40'
    );
  });

  it('renders the sector legend chips in ticker order', () => {
    mockData({ corr: matrix3 });
    render(<MarketCorrelation />);
    expect(screen.getByTestId('market-corr-sector-tech')).toBeTruthy();
    expect(screen.getByTestId('market-corr-sector-financials')).toBeTruthy();
  });
});
