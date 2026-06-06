import { useEffect, useRef } from 'react';
import { createChart, LineSeries } from 'lightweight-charts';
import type { ISeriesApi, IChartApi, UTCTimestamp } from 'lightweight-charts';
import { useTicker } from '@/stores/priceStore';

interface Props {
  ticker: string;
  width?: number;
  height?: number;
}

export default function SparklineChart({ ticker, width = 80, height = 28 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Line'> | null>(null);

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

  // Update: append price point on each SSE tick
  useEffect(() => {
    if (!seriesRef.current || !priceUpdate) return;
    seriesRef.current.update({
      time: Math.floor(priceUpdate.timestamp) as UTCTimestamp,
      value: priceUpdate.price,
    });
  }, [priceUpdate]);

  return <div ref={containerRef} style={{ width, height }} />;
}
