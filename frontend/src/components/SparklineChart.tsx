import { useEffect, useRef } from 'react';
import { createChart, LineSeries } from 'lightweight-charts';
import type { ISeriesApi, IChartApi, UTCTimestamp, LineData } from 'lightweight-charts';
import { useTicker } from '@/stores/priceStore';

interface Props {
  ticker: string;
  width?: number;
  height?: number;
}

// Rolling buffer cap — keeps memory bounded over long sessions. Trim happens in
// batches (only once the buffer exceeds the cap by 20%) to avoid a setData()
// call on every tick.
const MAX_POINTS = 120;
const TRIM_THRESHOLD = Math.floor(MAX_POINTS * 1.2); // 144

export default function SparklineChart({ ticker, width = 80, height = 28 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const tickCountRef = useRef<number>(0);
  const bufferRef = useRef<LineData<UTCTimestamp>[]>([]);

  const priceUpdate = useTicker(ticker);

  // Mount: create chart + series; cleanup calls chart.remove() (Pitfall 4)
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      width,
      height,
      layout: {
        background: { color: 'transparent' },
        textColor: 'transparent',
      },
      rightPriceScale: { visible: false },
      timeScale: { visible: false },
      crosshair: { mode: 0 }, // CrosshairMode.Hidden
      grid: {
        vertLines: { visible: false },
        horzLines: { visible: false },
      },
      handleScroll: false,
      handleScale: false,
    });

    // v5 API: addSeries(SeriesType, options) — NOT addLineSeries() (Pitfall 1)
    const series = chart.addSeries(LineSeries, {
      color: '#209dd7',
      lineWidth: 1,
    });

    chartRef.current = chart;
    seriesRef.current = series as ISeriesApi<'Line'>;

    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Resize chart when width/height props change (WR-05)
  useEffect(() => {
    chartRef.current?.applyOptions({ width, height });
  }, [width, height]);

  // Update: append price point on each SSE tick using monotonic counter (WR-04).
  // The counter is never reset, so times stay strictly ascending across trims.
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

  return <div ref={containerRef} style={{ width, height }} />;
}
