import { useEffect, useRef } from 'react';
import { createChart, LineSeries } from 'lightweight-charts';
import type { ISeriesApi, IChartApi, UTCTimestamp } from 'lightweight-charts';
import { useTicker } from '@/stores/priceStore';

interface Props {
  ticker: string;
}

export default function MainChart({ ticker }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const tickCountRef = useRef<number>(0);

  const priceUpdate = useTicker(ticker);

  // Mount: create chart + series; cleanup calls chart.remove()
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

    // v5 API: addSeries(SeriesType, options)
    const series = chart.addSeries(LineSeries, {
      color: '#209dd7',
      lineWidth: 2,
    });

    chartRef.current = chart;
    seriesRef.current = series as ISeriesApi<'Line'>;

    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Ticker change: reset series data and counter (Pitfall 1 — prevents discontinuous jump)
  useEffect(() => {
    seriesRef.current?.setData([]);
    tickCountRef.current = 0;
  }, [ticker]);

  // Update: append price point on each SSE tick using monotonic counter
  useEffect(() => {
    if (!seriesRef.current || !priceUpdate) return;
    tickCountRef.current += 1;
    seriesRef.current.update({
      time: tickCountRef.current as UTCTimestamp,
      value: priceUpdate.price,
    });
  }, [priceUpdate]);

  return (
    <div>
      <div className="px-3 py-1 text-xs font-semibold" style={{ color: '#ecad0a' }}>
        {ticker}
      </div>
      <div ref={containerRef} style={{ width: '100%', height: '240px' }} />
    </div>
  );
}
