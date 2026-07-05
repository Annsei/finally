/**
 * PnLChart tests (TDD):
 * Test 1: On mount, createChart called once and addSeries called with AreaSeries sentinel
 * Test 2: When useSWR returns snapshot data, series.setData is called with indexed time points
 * Test 3: Empty/no-data state does not throw and does not call setData with points
 */
import React from 'react';
import { render } from '@testing-library/react';

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

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

import useSWR from 'swr';
import { createChart, AreaSeries } from 'lightweight-charts';
import PnLChart from '@/components/PnLChart';

const mockUseSWR = jest.mocked(useSWR);

describe('PnLChart', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('Test 1: On mount, createChart is called once and addSeries is called with AreaSeries sentinel', () => {
    mockUseSWR.mockReturnValue({ data: undefined } as any);

    render(<PnLChart />);

    const mc = jest.mocked(createChart);
    expect(mc).toHaveBeenCalledTimes(1);

    const chart = mc.mock.results[0].value as { addSeries: jest.Mock };
    expect(chart.addSeries).toHaveBeenCalledWith(AreaSeries, expect.any(Object));
  });

  it('Test 2: When useSWR returns snapshot data, series.setData is called with real recorded_at timestamps', () => {
    const snapshots = [
      { total_value: 10000, recorded_at: '2026-06-07T00:00:00Z' },
      { total_value: 10500, recorded_at: '2026-06-07T00:00:30Z' },
      { total_value: 10250, recorded_at: '2026-06-07T00:01:00Z' },
    ];
    mockUseSWR.mockReturnValue({ data: { snapshots } } as any);

    render(<PnLChart />);

    const mc = jest.mocked(createChart);
    const chart = mc.mock.results[0].value as { addSeries: jest.Mock };
    const series = (chart.addSeries.mock.results[0] as jest.MockResult<{ setData: jest.Mock }>).value;

    const t = (iso: string) => Math.floor(Date.parse(iso) / 1000);
    expect(series.setData).toHaveBeenCalledWith([
      { time: t('2026-06-07T00:00:00Z'), value: 10000 },
      { time: t('2026-06-07T00:00:30Z'), value: 10500 },
      { time: t('2026-06-07T00:01:00Z'), value: 10250 },
    ]);
  });

  it('Test 2b: same-second snapshots (30s tick + post-trade) are deduped keeping the LAST value', () => {
    const snapshots = [
      { total_value: 10000, recorded_at: '2026-06-07T00:00:00Z' },
      // Two snapshots in the same second: periodic tick then post-trade — keep 10800
      { total_value: 10500, recorded_at: '2026-06-07T00:00:30Z' },
      { total_value: 10800, recorded_at: '2026-06-07T00:00:30.400Z' },
      { total_value: 10250, recorded_at: '2026-06-07T00:01:00Z' },
    ];
    mockUseSWR.mockReturnValue({ data: { snapshots } } as any);

    render(<PnLChart />);

    const mc = jest.mocked(createChart);
    const chart = mc.mock.results[0].value as { addSeries: jest.Mock };
    const series = (chart.addSeries.mock.results[0] as jest.MockResult<{ setData: jest.Mock }>).value;

    const t = (iso: string) => Math.floor(Date.parse(iso) / 1000);
    expect(series.setData).toHaveBeenCalledWith([
      { time: t('2026-06-07T00:00:00Z'), value: 10000 },
      { time: t('2026-06-07T00:00:30Z'), value: 10800 },
      { time: t('2026-06-07T00:01:00Z'), value: 10250 },
    ]);
  });

  it('Test 3: Empty/no-data state does not throw and does not call setData with points', () => {
    mockUseSWR.mockReturnValue({ data: undefined } as any);

    expect(() => render(<PnLChart />)).not.toThrow();

    const mc = jest.mocked(createChart);
    const chart = mc.mock.results[0].value as { addSeries: jest.Mock };
    const series = (chart.addSeries.mock.results[0] as jest.MockResult<{ setData: jest.Mock }>).value;

    // setData should NOT have been called with any points when there is no data
    expect(series.setData).not.toHaveBeenCalled();
  });
});
