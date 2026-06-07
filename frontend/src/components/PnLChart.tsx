import { useEffect, useRef } from 'react';
import { createChart, AreaSeries } from 'lightweight-charts';
import type { ISeriesApi, IChartApi, UTCTimestamp } from 'lightweight-charts';
import useSWR from 'swr';
import { fetcher } from '@/lib/fetcher';
import type { PortfolioHistoryResponse } from '@/types/market';

export default function PnLChart() {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Area'> | null>(null);

  const { data } = useSWR<PortfolioHistoryResponse>('/api/portfolio/history', fetcher, {
    refreshInterval: 30_000,
  });

  // Mount: create chart + area series; cleanup calls chart.remove()
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { color: 'transparent' },
        textColor: '#8b949e',
      },
      grid: {
        vertLines: { color: '#30363d' },
        horzLines: { color: '#30363d' },
      },
      rightPriceScale: {
        borderColor: '#30363d',
      },
      timeScale: {
        borderColor: '#30363d',
        textColor: '#8b949e',
      },
    });

    // v5 API: addSeries(AreaSeries, options)
    const series = chart.addSeries(AreaSeries, {
      lineColor: '#209dd7',
      topColor: 'rgba(34, 197, 94, 0.4)',
      bottomColor: 'rgba(34, 197, 94, 0.0)',
      lineWidth: 2,
    });

    chartRef.current = chart;
    seriesRef.current = series as ISeriesApi<'Area'>;

    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Data: update chart when SWR data arrives or refreshes
  // Use array index + 1 as time value (Pitfall 4 — avoids timestamp parsing gaps)
  useEffect(() => {
    if (!data?.snapshots?.length || !seriesRef.current) return;
    const points = data.snapshots.map((s, i) => ({
      time: (i + 1) as UTCTimestamp,
      value: s.total_value,
    }));
    seriesRef.current.setData(points);
  }, [data]);

  const hasData = data?.snapshots?.length;

  return (
    <div style={{ width: '100%' }}>
      {!hasData && (
        <div className="p-4 text-xs" style={{ color: '#8b949e' }}>
          No portfolio history yet.
        </div>
      )}
      <div ref={containerRef} style={{ width: '100%', height: '160px' }} />
    </div>
  );
}
