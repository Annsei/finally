/**
 * MainChart tests (Batch 2 — candlestick + volume + timeframes + backfill):
 * Test 1: mount creates chart with CandlestickSeries AND HistogramSeries (volume)
 * Test 2: history backfill → both series setData with 1s bars (candle + colored volume)
 * Test 3: live tick in a NEW second → series.update with a fresh candle
 * Test 4: same-second ticks merge into the last candle (high/close/volume)
 * Test 5: ticker change resets both series with setData([])
 * Test 6: timeframe switch re-aggregates the backfill (5s buckets)
 * Test 7: title shows live price + colored day change (Batch-1 behavior kept)
 */
import React from 'react';
import { render, act, fireEvent } from '@testing-library/react';
import { usePriceStore } from '@/stores/priceStore';

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

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

import useSWR from 'swr';
import { createChart, CandlestickSeries, HistogramSeries } from 'lightweight-charts';
import MainChart from '@/components/MainChart';

const mockUseSWR = jest.mocked(useSWR);

const historyBars = [
  { time: 1717700000, open: 190.0, high: 190.5, low: 189.8, close: 190.2, volume: 100 },
  { time: 1717700001, open: 190.2, high: 190.3, low: 190.0, close: 190.1, volume: 50 },
  { time: 1717700003, open: 190.1, high: 190.8, low: 190.1, close: 190.7, volume: 25 },
];

// Grabs the candle + volume series mocks created on mount, in creation order
function getSeries() {
  const mc = jest.mocked(createChart);
  const chart = mc.mock.results[0].value as { addSeries: jest.Mock };
  expect(chart.addSeries).toHaveBeenNthCalledWith(1, CandlestickSeries, expect.any(Object));
  expect(chart.addSeries).toHaveBeenNthCalledWith(2, HistogramSeries, expect.any(Object));
  const candles = chart.addSeries.mock.results[0].value;
  const volume = chart.addSeries.mock.results[1].value;
  return { candles, volume };
}

const tick = (price: number, timestamp: number, volume = 10) =>
  act(() => {
    usePriceStore.setState({
      prices: {
        AAPL: {
          ticker: 'AAPL',
          price,
          previous_price: price - 0.1,
          timestamp,
          change: 0.1,
          change_percent: 0.05,
          direction: 'up',
          volume,
        },
      },
    });
  });

describe('MainChart', () => {
  beforeEach(() => {
    usePriceStore.setState({ prices: {}, connectionStatus: 'disconnected' });
    jest.clearAllMocks();
    mockUseSWR.mockReturnValue({ data: undefined } as any);
  });

  it('Test 1: mount creates candlestick and histogram (volume) series', () => {
    render(<MainChart ticker="AAPL" />);
    getSeries(); // asserts both series types internally
  });

  it('Test 2: history backfill populates both series (volume colored by candle direction)', () => {
    mockUseSWR.mockReturnValue({ data: { ticker: 'AAPL', bars: historyBars } } as any);

    render(<MainChart ticker="AAPL" />);
    const { candles, volume } = getSeries();

    expect(candles.setData).toHaveBeenLastCalledWith([
      { time: 1717700000, open: 190.0, high: 190.5, low: 189.8, close: 190.2 },
      { time: 1717700001, open: 190.2, high: 190.3, low: 190.0, close: 190.1 },
      { time: 1717700003, open: 190.1, high: 190.8, low: 190.1, close: 190.7 },
    ]);
    const volData = volume.setData.mock.calls[volume.setData.mock.calls.length - 1][0];
    expect(volData).toHaveLength(3);
    expect(volData[0]).toEqual({ time: 1717700000, value: 100, color: 'rgba(34, 197, 94, 0.5)' }); // up
    expect(volData[1].color).toBe('rgba(239, 68, 68, 0.5)'); // down candle → red volume
  });

  it('Test 3: a live tick in a new second updates both series with a fresh candle', () => {
    mockUseSWR.mockReturnValue({ data: { ticker: 'AAPL', bars: historyBars } } as any);

    render(<MainChart ticker="AAPL" />);
    const { candles, volume } = getSeries();

    tick(190.9, 1717700005.2, 42);

    expect(candles.update).toHaveBeenLastCalledWith({
      time: 1717700005,
      open: 190.9,
      high: 190.9,
      low: 190.9,
      close: 190.9,
    });
    expect(volume.update).toHaveBeenLastCalledWith({
      time: 1717700005,
      value: 42,
      color: 'rgba(34, 197, 94, 0.5)',
    });
  });

  it('Test 4: same-second ticks merge into the last candle', () => {
    render(<MainChart ticker="AAPL" />);
    const { candles, volume } = getSeries();

    tick(190.0, 1717700010.1, 10);
    tick(190.6, 1717700010.5, 5);
    tick(189.9, 1717700010.9, 5);

    expect(candles.update).toHaveBeenLastCalledWith({
      time: 1717700010,
      open: 190.0,
      high: 190.6,
      low: 189.9,
      close: 189.9,
    });
    expect(volume.update).toHaveBeenLastCalledWith(
      expect.objectContaining({ time: 1717700010, value: 20 })
    );
  });

  it('Test 5: ticker change resets both series', () => {
    const { rerender } = render(<MainChart ticker="AAPL" />);
    const { candles, volume } = getSeries();
    candles.setData.mockClear();
    volume.setData.mockClear();

    rerender(<MainChart ticker="MSFT" />);

    expect(candles.setData).toHaveBeenCalledWith([]);
    expect(volume.setData).toHaveBeenCalledWith([]);
  });

  it('Test 6: timeframe switch re-aggregates the backfill into 5s buckets', () => {
    mockUseSWR.mockReturnValue({ data: { ticker: 'AAPL', bars: historyBars } } as any);

    const { getByTestId } = render(<MainChart ticker="AAPL" />);
    const { candles } = getSeries();

    fireEvent.click(getByTestId('tf-5s'));

    // All three 1s bars share the 1717700000 5s-bucket:
    // open of first, max high, min low, close of last
    expect(candles.setData).toHaveBeenLastCalledWith([
      { time: 1717700000, open: 190.0, high: 190.8, low: 189.8, close: 190.7 },
    ]);
  });

  it('Test 7: title shows live price and colored day change (Batch-1 behavior)', () => {
    const { getByTestId, getByText } = render(<MainChart ticker="AAPL" />);

    act(() => {
      usePriceStore.setState({
        prices: {
          AAPL: {
            ticker: 'AAPL',
            price: 190.5,
            previous_price: 190.4,
            timestamp: 1717700000,
            change: 0.1,
            change_percent: 0.05,
            direction: 'up',
            prev_close: 188.0,
            day_change: 2.5,
            day_change_percent: 1.33,
          },
        },
      });
    });

    expect(getByText('190.50')).toBeTruthy();
    expect(getByTestId('main-chart-day-change').textContent).toBe('▲+2.50 (+1.33%)');
  });
});
