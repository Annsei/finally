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
        // Real intraday timestamps — show HH:MM:SS instead of dates
        timeVisible: true,
        secondsVisible: true,
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

  // Ticker change: reset series data and buffer (Pitfall 1 — prevents discontinuous jump)
  useEffect(() => {
    seriesRef.current?.setData([]);
    bufferRef.current = [];
  }, [ticker]);

  // Update: append price point on each SSE tick using the real update timestamp
  // floored to whole seconds. lightweight-charts requires non-decreasing times:
  // a tick landing in the same second replaces the previous point (update() with
  // an equal time overwrites the last bar), and clock regressions are dropped.
  useEffect(() => {
    if (!seriesRef.current || !priceUpdate) return;
    const time = Math.floor(priceUpdate.timestamp) as UTCTimestamp;
    const point: LineData<UTCTimestamp> = { time, value: priceUpdate.price };

    const buffer = bufferRef.current;
    const last = buffer[buffer.length - 1];
    if (last && (last.time as number) > (time as number)) return;
    if (last && last.time === time) {
      buffer[buffer.length - 1] = point;
    } else {
      buffer.push(point);
    }

    if (buffer.length > TRIM_THRESHOLD) {
      // Batched trim: drop oldest points, rebase the series on the capped buffer
      bufferRef.current = buffer.slice(-MAX_POINTS);
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
