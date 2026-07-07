/**
 * MainChartCn.test.tsx (FinAlly-CN, CN-4a)
 *
 * Two A-share wirings on the main chart header:
 *  - 涨停/跌停 badge: renders when the quote carries the day's limits and the
 *    live price hits them (same approach as WatchlistRow). US quotes carry no
 *    limit fields, so no badge ever shows.
 *  - Volume readout localises via formatLargeCount(profile.locale): zh-CN →
 *    万/亿, en-US → the previous grouped-integer display (byte-identical).
 *
 * NEW test file — no existing test is touched.
 */
import React from 'react';
import { render, act } from '@testing-library/react';
import { usePriceStore } from '@/stores/priceStore';
import type { PriceUpdate } from '@/types/market';

jest.mock('lightweight-charts', () => {
  const makeSeries = () => ({
    update: jest.fn(),
    setData: jest.fn(),
    applyOptions: jest.fn(),
  });
  const mockCreateChart = jest.fn(() => ({
    addSeries: jest.fn(makeSeries),
    remove: jest.fn(),
    applyOptions: jest.fn(),
    priceScale: jest.fn(() => ({ applyOptions: jest.fn() })),
    subscribeCrosshairMove: jest.fn(),
    timeScale: jest.fn(() => ({ fitContent: jest.fn() })),
  }));
  return {
    createChart: mockCreateChart,
    LineSeries: { __sentinelType: 'LineSeries' },
    AreaSeries: { __sentinelType: 'AreaSeries' },
    BaselineSeries: { __sentinelType: 'BaselineSeries' },
    CandlestickSeries: { __sentinelType: 'CandlestickSeries' },
    HistogramSeries: { __sentinelType: 'HistogramSeries' },
  };
});

jest.mock('swr', () => ({ __esModule: true, default: jest.fn() }));

import useSWR from 'swr';
import { createChart } from 'lightweight-charts';
import MainChart from '@/components/MainChart';

const mockUseSWR = jest.mocked(useSWR);

const cnProfile = {
  market: 'cn',
  currency_symbol: '¥',
  locale: 'zh-CN',
  lot_size: 100,
  t_plus: 1,
  up_is_red: true,
  seed_cash: 100000,
  midday_break: true,
  names: { '600519': '贵州茅台' },
  price_limit_pct: { '600519': 10 },
};

const mkPrice = (over: Partial<PriceUpdate>): PriceUpdate => ({
  ticker: '600519',
  price: 1700,
  previous_price: 1699,
  timestamp: 1,
  change: 1,
  change_percent: 0.06,
  direction: 'up',
  ...over,
});

function useSWRImpl(profile: unknown) {
  return (key: string) => {
    if (key === '/api/market/profile') return { data: profile } as never;
    // history fetch → undefined (chart still mounts, no backfill needed here)
    return { data: undefined } as never;
  };
}

// Drives the component's crosshair callback with a synthetic hovered candle +
// volume bar, keyed by the exact series objects the mock created on mount.
function hoverVolume(volumeValue: number) {
  const chart = jest.mocked(createChart).mock.results[0].value as {
    addSeries: jest.Mock;
    subscribeCrosshairMove: jest.Mock;
  };
  const candles = chart.addSeries.mock.results[0].value;
  const volume = chart.addSeries.mock.results[1].value;
  const cb = chart.subscribeCrosshairMove.mock.calls[0][0] as (p: unknown) => void;
  act(() => {
    cb({
      time: 1717700000,
      seriesData: new Map<unknown, unknown>([
        [candles, { open: 1700, high: 1710, low: 1695, close: 1705 }],
        [volume, { value: volumeValue }],
      ]),
    });
  });
}

describe('MainChart — A-share 涨跌停 badge', () => {
  beforeEach(() => {
    usePriceStore.setState({ prices: {}, connectionStatus: 'disconnected' });
    jest.clearAllMocks();
    mockUseSWR.mockImplementation(useSWRImpl(cnProfile) as never);
  });

  it('shows a 涨停 badge when the live price hits the upper limit', () => {
    const { getByTestId } = render(<MainChart ticker="600519" />);
    act(() => {
      usePriceStore.setState({
        prices: { '600519': mkPrice({ price: 1870, limit_up: 1870, limit_down: 1530 }) },
      });
    });
    expect(getByTestId('main-chart-limit-badge').textContent).toBe('涨停');
  });

  it('shows a 跌停 badge when the live price hits the lower limit', () => {
    const { getByTestId } = render(<MainChart ticker="600519" />);
    act(() => {
      usePriceStore.setState({
        prices: { '600519': mkPrice({ price: 1530, limit_up: 1870, limit_down: 1530 }) },
      });
    });
    expect(getByTestId('main-chart-limit-badge').textContent).toBe('跌停');
  });

  it('shows no badge when the quote carries no limit fields (US contract)', () => {
    mockUseSWR.mockImplementation(useSWRImpl(undefined) as never); // US default
    const { queryByTestId } = render(<MainChart ticker="AAPL" />);
    act(() => {
      usePriceStore.setState({
        prices: { AAPL: mkPrice({ ticker: 'AAPL', price: 190 }) }, // no limit_up/down
      });
    });
    expect(queryByTestId('main-chart-limit-badge')).toBeNull();
  });
});

describe('MainChart — volume readout localises via formatLargeCount', () => {
  beforeEach(() => {
    usePriceStore.setState({ prices: {}, connectionStatus: 'disconnected' });
    jest.clearAllMocks();
  });

  it('zh-CN collapses the hovered volume into 万', () => {
    mockUseSWR.mockImplementation(useSWRImpl(cnProfile) as never);
    const { getByTestId } = render(<MainChart ticker="600519" />);
    hoverVolume(35000);
    expect(getByTestId('main-chart-ohlc').textContent).toContain('3.5万');
  });

  it('US default keeps the grouped-integer display (byte-identical)', () => {
    mockUseSWR.mockImplementation(useSWRImpl(undefined) as never); // US default → en-US
    const { getByTestId } = render(<MainChart ticker="AAPL" />);
    hoverVolume(35000);
    const text = getByTestId('main-chart-ohlc').textContent ?? '';
    expect(text).toContain('35,000');
    expect(text).not.toContain('万');
  });
});
