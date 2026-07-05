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
        // Real snapshot timestamps — show intraday time instead of dates
        timeVisible: true,
        secondsVisible: false,
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

  // Data: update chart when SWR data arrives or refreshes.
  // Use the real recorded_at timestamps; snapshots are taken every 30s plus
  // after each trade, so two can share a second — sort ascending and keep the
  // LAST value per second (lightweight-charts requires strictly ascending times).
  useEffect(() => {
    if (!data?.snapshots?.length || !seriesRef.current) return;
    const sorted = data.snapshots
      .map((s) => ({
        time: Math.floor(Date.parse(s.recorded_at) / 1000) as UTCTimestamp,
        value: s.total_value,
      }))
      .filter((p) => Number.isFinite(p.time as number))
      .sort((a, b) => (a.time as number) - (b.time as number));
    const points = sorted.filter(
      (p, i) => i === sorted.length - 1 || (sorted[i + 1].time as number) !== (p.time as number)
    );
    if (!points.length) return;
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
