/**
 * SparklineChart tests (TDD):
 * Test 1: On mount, createChart called once and addSeries called with LineSeries
 * Test 2: When store ticker price updates, series.update called with { time, value: price }
 * Test 3: On unmount, chart.remove() called once
 * Test 4: Width/height prop changes are applied to the existing chart
 */
import React from 'react';
import { render, act } from '@testing-library/react';
import { usePriceStore } from '@/stores/priceStore';

jest.mock('lightweight-charts', () => {
  const mockSeriesUpdate = jest.fn();
  const mockSeriesSetData = jest.fn();
  const mockChartRemove = jest.fn();
  const mockApplyOptions = jest.fn();
  const mockAddSeries = jest.fn().mockReturnValue({
    update: mockSeriesUpdate,
    setData: mockSeriesSetData,
  });
  const mockCreateChart = jest.fn().mockReturnValue({
    addSeries: mockAddSeries,
    remove: mockChartRemove,
    applyOptions: mockApplyOptions,
  });
  const LineSeries = { __sentinelType: 'LineSeries' };
  return { createChart: mockCreateChart, LineSeries };
});

import { createChart, LineSeries } from 'lightweight-charts';
import SparklineChart from '@/components/SparklineChart';

describe('SparklineChart', () => {
  beforeEach(() => {
    usePriceStore.setState({ prices: {}, connectionStatus: 'disconnected' });
    jest.clearAllMocks();
  });

  it('Test 1: On mount, createChart is called once and addSeries is called with LineSeries', () => {
    render(<SparklineChart ticker="AAPL" />);

    const mc = jest.mocked(createChart);
    expect(mc).toHaveBeenCalledTimes(1);

    const chart = mc.mock.results[0].value as { addSeries: jest.Mock };
    expect(chart.addSeries).toHaveBeenCalledWith(LineSeries, expect.any(Object));
  });

  it('Test 2: When the store ticker price updates, series.update called with { time, value: price }', () => {
    render(<SparklineChart ticker="AAPL" />);

    const mc = jest.mocked(createChart);
    const chart = mc.mock.results[0].value as { addSeries: jest.Mock; remove: jest.Mock };
    const series = (chart.addSeries.mock.results[0] as jest.MockResult<{ update: jest.Mock }>).value;

    act(() => {
      usePriceStore.setState({
        prices: {
          AAPL: {
            ticker: 'AAPL',
            price: 190.5,
            previous_price: 189.5,
            timestamp: 1717700000.75,
            change: 1,
            change_percent: 0.53,
            direction: 'up',
          },
        },
      });
    });

    expect(series.update).toHaveBeenCalledWith(
      expect.objectContaining({ time: 1, value: 190.5 })
    );
  });

  it('Test 3: On unmount, chart.remove() is called once', () => {
    const { unmount } = render(<SparklineChart ticker="AAPL" />);

    const mc = jest.mocked(createChart);
    const chart = mc.mock.results[0].value as { addSeries: jest.Mock; remove: jest.Mock };

    unmount();

    expect(chart.remove).toHaveBeenCalledTimes(1);
  });

  it('Test 4: Width/height prop changes are applied to the existing chart', () => {
    const { rerender } = render(<SparklineChart ticker="AAPL" width={80} height={28} />);

    const mc = jest.mocked(createChart);
    const chart = mc.mock.results[0].value as { applyOptions: jest.Mock };

    rerender(<SparklineChart ticker="AAPL" width={120} height={40} />);

    expect(chart.applyOptions).toHaveBeenLastCalledWith({ width: 120, height: 40 });
  });

  it('Test 5 (FIX 5): buffer is trimmed to 120 points once it exceeds the cap by 20%', () => {
    render(<SparklineChart ticker="AAPL" />);

    const mc = jest.mocked(createChart);
    const chart = mc.mock.results[0].value as { addSeries: jest.Mock };
    const series = (
      chart.addSeries.mock.results[0] as jest.MockResult<{ update: jest.Mock; setData: jest.Mock }>
    ).value;

    // Dispatch 145 ticks: trim threshold is 144, so the 145th triggers the trim
    for (let i = 1; i <= 145; i++) {
      act(() => {
        usePriceStore.setState({
          prices: {
            AAPL: {
              ticker: 'AAPL',
              price: 100 + i * 0.01,
              previous_price: 100,
              timestamp: 1717700000 + i,
              change: 0.01,
              change_percent: 0.01,
              direction: 'up',
            },
          },
        });
      });
    }

    // setData was called with the capped, front-trimmed buffer
    expect(series.setData).toHaveBeenCalledTimes(1);
    const trimmed = series.setData.mock.calls[0][0] as Array<{ time: number; value: number }>;
    expect(trimmed).toHaveLength(120);

    // Front-trimmed: oldest retained point is tick 26 (145 - 120 + 1), newest is 145
    expect(trimmed[0].time).toBe(26);
    expect(trimmed[trimmed.length - 1].time).toBe(145);

    // Times remain strictly ascending (monotonic counter is never reset)
    for (let i = 1; i < trimmed.length; i++) {
      expect(trimmed[i].time).toBeGreaterThan(trimmed[i - 1].time);
    }

    // update() was used for the 144 pre-trim ticks (not for the trim tick itself)
    expect(series.update).toHaveBeenCalledTimes(144);
  });
});
