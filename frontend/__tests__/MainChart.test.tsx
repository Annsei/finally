/**
 * MainChart tests (TDD):
 * Test 1: On mount, createChart called once and addSeries called with LineSeries
 * Test 2: When store ticker price updates, series.update called with { time: 1, value: price }
 * Test 3: Re-rendering with a different ticker prop calls series.setData([]) (resets the line)
 */
import React from 'react';
import { render, act } from '@testing-library/react';
import { usePriceStore } from '@/stores/priceStore';

jest.mock('lightweight-charts', () => {
  const mockSeriesUpdate = jest.fn();
  const mockSeriesSetData = jest.fn();
  const mockSeriesApplyOptions = jest.fn();
  const mockChartRemove = jest.fn();
  const mockApplyOptions = jest.fn();
  const mockAddSeries = jest.fn().mockReturnValue({
    update: mockSeriesUpdate,
    setData: mockSeriesSetData,
    applyOptions: mockSeriesApplyOptions,
  });
  const mockCreateChart = jest.fn().mockReturnValue({
    addSeries: mockAddSeries,
    remove: mockChartRemove,
    applyOptions: mockApplyOptions,
  });
  const LineSeries = { __sentinelType: 'LineSeries' };
  const AreaSeries = { __sentinelType: 'AreaSeries' };
  return { createChart: mockCreateChart, LineSeries, AreaSeries };
});

import { createChart, LineSeries } from 'lightweight-charts';
import MainChart from '@/components/MainChart';

describe('MainChart', () => {
  beforeEach(() => {
    usePriceStore.setState({ prices: {}, connectionStatus: 'disconnected' });
    jest.clearAllMocks();
  });

  it('Test 1: On mount, createChart is called once and addSeries is called with LineSeries', () => {
    render(<MainChart ticker="AAPL" />);

    const mc = jest.mocked(createChart);
    expect(mc).toHaveBeenCalledTimes(1);

    const chart = mc.mock.results[0].value as { addSeries: jest.Mock };
    expect(chart.addSeries).toHaveBeenCalledWith(LineSeries, expect.any(Object));
  });

  it('Test 2: When the store ticker price updates, series.update called with { time: 1, value: price }', () => {
    render(<MainChart ticker="AAPL" />);

    const mc = jest.mocked(createChart);
    const chart = mc.mock.results[0].value as { addSeries: jest.Mock; remove: jest.Mock };
    const series = (chart.addSeries.mock.results[0] as jest.MockResult<{ update: jest.Mock; setData: jest.Mock }>).value;

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

  it('Test 3: Re-rendering with a different ticker prop calls series.setData([])', () => {
    const { rerender } = render(<MainChart ticker="AAPL" />);

    const mc = jest.mocked(createChart);
    const chart = mc.mock.results[0].value as { addSeries: jest.Mock };
    const series = (chart.addSeries.mock.results[0] as jest.MockResult<{ update: jest.Mock; setData: jest.Mock }>).value;

    rerender(<MainChart ticker="MSFT" />);

    expect(series.setData).toHaveBeenCalledWith([]);
  });
});
