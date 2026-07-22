/**
 * MarketSentiment.test.tsx — /market sentiment gauge (P4 §1).
 *
 * Pure helpers: labelForScore (five bands at 0/20/40/60/80), axisWidth clamp.
 * Rendering:    loading state, score + i18n label + active band segment, axis
 *               mini-bars — breadth on the DIRECTION colour variables
 *               (advancers-vs-decliners semantics), volatility/volume on the
 *               neutral accent/blue palette; zh band labels.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import useSWR from 'swr';
import type { MarketSentimentResponse } from '@/types/market';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

import MarketSentiment, {
  SENTIMENT_LABELS,
  labelForScore,
  axisWidth,
} from '@/components/MarketSentiment';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

const payload = (over: Partial<MarketSentimentResponse> = {}): MarketSentimentResponse => ({
  score: 72,
  label: 'active',
  axes: { breadth: 80, volatility: 55, volume: 70 },
  sample_size: 10,
  ...over,
});

function mockData(opts: {
  sentiment?: MarketSentimentResponse;
  profile?: Record<string, unknown>;
}) {
  mockUseSWR.mockImplementation(((key: string) => {
    if (key === '/api/market/sentiment' && opts.sentiment) {
      return { data: opts.sentiment, mutate: jest.fn() };
    }
    if (key === '/api/market/profile' && opts.profile) {
      return { data: opts.profile, mutate: jest.fn() };
    }
    return { data: undefined, mutate: jest.fn() };
  }) as never);
}

describe('sentiment helpers (P4 §1)', () => {
  it('labelForScore maps the five bands at thresholds 0/20/40/60/80', () => {
    expect(labelForScore(0)).toBe('frozen');
    expect(labelForScore(19)).toBe('frozen');
    expect(labelForScore(20)).toBe('cool');
    expect(labelForScore(39.9)).toBe('cool');
    expect(labelForScore(40)).toBe('neutral');
    expect(labelForScore(59)).toBe('neutral');
    expect(labelForScore(60)).toBe('active');
    expect(labelForScore(79)).toBe('active');
    expect(labelForScore(80)).toBe('hot');
    expect(labelForScore(100)).toBe('hot');
  });

  it('labelForScore clamps out-of-range and defaults missing input to neutral', () => {
    expect(labelForScore(-10)).toBe('frozen');
    expect(labelForScore(500)).toBe('hot');
    expect(labelForScore(undefined)).toBe('neutral');
    expect(labelForScore(null)).toBe('neutral');
    expect(labelForScore(NaN)).toBe('neutral');
  });

  it('axisWidth clamps into 0..100 and zeroes missing values', () => {
    expect(axisWidth(50)).toBe(50);
    expect(axisWidth(-5)).toBe(0);
    expect(axisWidth(130)).toBe(100);
    expect(axisWidth(49.6)).toBe(50);
    expect(axisWidth(undefined)).toBe(0);
    expect(axisWidth(null)).toBe(0);
    expect(axisWidth(NaN)).toBe(0);
  });
});

describe('MarketSentiment (P4 §1)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockData({});
  });

  it('renders the loading state before the payload arrives', () => {
    render(<MarketSentiment />);
    expect(screen.getByTestId('market-sentiment').textContent).toContain(
      'Measuring market temperature…'
    );
  });

  it('renders the score, the i18n band label, and marks only the active segment', () => {
    mockData({ sentiment: payload({ score: 72, label: 'active' }) });
    render(<MarketSentiment />);

    expect(screen.getByTestId('market-sentiment-score').textContent).toBe('72');
    expect(screen.getByTestId('market-sentiment-label').textContent).toBe('Active');

    for (const seg of SENTIMENT_LABELS) {
      expect(screen.getByTestId(`market-sentiment-seg-${seg}`).getAttribute('data-active')).toBe(
        seg === 'active' ? 'true' : 'false'
      );
    }
  });

  it('falls back to the score-derived band when the payload label is unknown', () => {
    mockData({ sentiment: payload({ score: 90, label: 'bogus' }) });
    render(<MarketSentiment />);
    expect(screen.getByTestId('market-sentiment-label').textContent).toBe('Hot');
    expect(screen.getByTestId('market-sentiment-seg-hot').getAttribute('data-active')).toBe('true');
  });

  it('axis mini-bars carry their clamped values as widths', () => {
    mockData({ sentiment: payload({ axes: { breadth: 80, volatility: 140, volume: -3 } }) });
    render(<MarketSentiment />);

    const breadth = screen.getByTestId('market-sentiment-axis-breadth');
    expect(breadth.getAttribute('data-value')).toBe('80');
    expect(breadth.style.width).toBe('80%');
    expect(screen.getByTestId('market-sentiment-axis-volatility').getAttribute('data-value')).toBe('100');
    expect(screen.getByTestId('market-sentiment-axis-volume').getAttribute('data-value')).toBe('0');
  });

  it('breadth uses the direction colour variables (advancers > 50 → up, < 50 → down)', () => {
    mockData({ sentiment: payload({ axes: { breadth: 80, volatility: 50, volume: 50 } }) });
    const { unmount } = render(<MarketSentiment />);
    expect(screen.getByTestId('market-sentiment-axis-breadth').getAttribute('data-color')).toBe(
      'var(--color-up)'
    );
    unmount();

    mockData({ sentiment: payload({ axes: { breadth: 20, volatility: 50, volume: 50 } }) });
    render(<MarketSentiment />);
    expect(screen.getByTestId('market-sentiment-axis-breadth').getAttribute('data-color')).toBe(
      'var(--color-down)'
    );
  });

  it('volatility and volume use the NEUTRAL accent/blue palette, never direction colours', () => {
    mockData({ sentiment: payload() });
    render(<MarketSentiment />);

    expect(screen.getByTestId('market-sentiment-axis-volatility').getAttribute('data-color')).toBe(
      '#ecad0a'
    );
    expect(screen.getByTestId('market-sentiment-axis-volume').getAttribute('data-color')).toBe(
      '#209dd7'
    );
  });

  it('cn: renders the Chinese band label from the zh dictionary', () => {
    mockData({
      sentiment: payload({ score: 85, label: 'hot' }),
      profile: { market: 'cn', locale: 'zh-CN', up_is_red: true },
    });
    render(<MarketSentiment />);
    expect(screen.getByTestId('market-sentiment-label').textContent).toBe('沸腾');
  });
});
