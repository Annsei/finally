import { useEffect, useRef } from 'react';
import { createChart, LineSeries } from 'lightweight-charts';
import type { ISeriesApi, IChartApi, UTCTimestamp, LineData } from 'lightweight-charts';
import { useTicker } from '@/stores/priceStore';

interface Props {
  ticker: string;
}

// Rolling buffer cap — keeps memory bounded over long sessions. Trim happens in
// batches (only once the buffer exceeds the cap by 20%) to avoid a setData()
// call on every tick.
const MAX_POINTS = 600;
const TRIM_THRESHOLD = Math.floor(MAX_POINTS * 1.2); // 720

export default function MainChart({ ticker }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const tickCountRef = useRef<number>(0);
  const bufferRef = useRef<LineData<UTCTimestamp>[]>([]);

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

  // Ticker change: reset series data, buffer, and counter (Pitfall 1 — prevents discontinuous jump)
  useEffect(() => {
    seriesRef.current?.setData([]);
    bufferRef.current = [];
    tickCountRef.current = 0;
  }, [ticker]);

  // Update: append price point on each SSE tick using monotonic counter.
  // The counter is only reset alongside the buffer (ticker change), so times
  // stay strictly ascending across trims.
  useEffect(() => {
    if (!seriesRef.current || !priceUpdate) return;
    tickCountRef.current += 1;
    const point: LineData<UTCTimestamp> = {
      time: tickCountRef.current as UTCTimestamp,
      value: priceUpdate.price,
    };
    bufferRef.current.push(point);

    if (bufferRef.current.length > TRIM_THRESHOLD) {
      // Batched trim: drop oldest points, rebase the series on the capped buffer
      bufferRef.current = bufferRef.current.slice(-MAX_POINTS);
      seriesRef.current.setData(bufferRef.current);
    } else {
      seriesRef.current.update(point);
    }
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
