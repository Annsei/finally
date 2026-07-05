/**
 * MainChart.tsx — candlestick chart with volume pane (FRONTEND_REALISM.md §2.1/2.2)
 *
 * Data flow:
 *  - Backfill: GET /api/market/history serves ~2h of 1-second OHLCV bars, so
 *    the chart is populated the moment the page opens. The backend ring
 *    buffer is fed by the same funnel as the SSE stream, so replacing the
 *    local 1s bars wholesale when backfill arrives loses nothing.
 *  - Live: each SSE tick folds into the 1s bars AND the display-timeframe
 *    bars via applyTick (lib/candles.ts). Same-second ticks merge; stale
 *    ticks drop.
 *  - Timeframe switch re-aggregates the local 1s bars — no refetch.
 *
 * Memory: 1s bars capped at 7200; display bars trimmed in batches to 600
 * once they exceed 720 (same batched-trim strategy as SparklineChart).
 */
import { useEffect, useRef, useState } from 'react';
import { createChart, CandlestickSeries, HistogramSeries } from 'lightweight-charts';
import type {
  ISeriesApi,
  IChartApi,
  UTCTimestamp,
  CandlestickData,
  HistogramData,
  MouseEventParams,
} from 'lightweight-charts';
import useSWR from 'swr';
import { useTicker } from '@/stores/priceStore';
import { fetcher } from '@/lib/fetcher';
import { aggregateBars, applyTick, type Bar } from '@/lib/candles';
import type { MarketHistoryResponse } from '@/types/market';

interface Props {
  ticker: string;
}

const TIMEFRAMES = [
  { label: '1s', seconds: 1 },
  { label: '5s', seconds: 5 },
  { label: '1m', seconds: 60 },
] as const;

const MAX_1S_BARS = 7200;
const MAX_BARS = 600;
const TRIM_THRESHOLD = 720;

const UP = '#22c55e';
const DOWN = '#ef4444';

const volumeColor = (b: Bar) =>
  b.close >= b.open ? 'rgba(34, 197, 94, 0.5)' : 'rgba(239, 68, 68, 0.5)';

const toCandle = (b: Bar): CandlestickData<UTCTimestamp> => ({
  time: b.time as UTCTimestamp,
  open: b.open,
  high: b.high,
  low: b.low,
  close: b.close,
});

const toVolume = (b: Bar): HistogramData<UTCTimestamp> => ({
  time: b.time as UTCTimestamp,
  value: b.volume,
  color: volumeColor(b),
});

interface HoverBar {
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
}

export default function MainChart({ ticker }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const bars1sRef = useRef<Bar[]>([]);
  const displayBarsRef = useRef<Bar[]>([]);

  const [timeframe, setTimeframe] = useState<number>(1);
  const [hover, setHover] = useState<HoverBar | null>(null);

  const priceUpdate = useTicker(ticker);

  // Backfill — SWR key changes with the ticker; no focus revalidation (the
  // live stream keeps the chart current once backfill lands)
  const { data: history } = useSWR<MarketHistoryResponse>(
    `/api/market/history?ticker=${encodeURIComponent(ticker)}`,
    fetcher,
    { revalidateOnFocus: false, revalidateOnReconnect: false }
  );

  // Mount: create chart + candle/volume series; cleanup calls chart.remove()
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
        scaleMargins: { top: 0.05, bottom: 0.25 },
      },
      timeScale: {
        borderColor: '#30363d',
        // Real intraday timestamps — show HH:MM:SS instead of dates
        timeVisible: true,
        secondsVisible: true,
      },
    });

    // v5 API: addSeries(SeriesType, options)
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      borderUpColor: UP,
      borderDownColor: DOWN,
      wickUpColor: UP,
      wickDownColor: DOWN,
    });
    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    });
    // Volume pane occupies the bottom 20% of the chart
    if (typeof chart.priceScale === 'function') {
      chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    }

    chartRef.current = chart;
    candleSeriesRef.current = candles as ISeriesApi<'Candlestick'>;
    volumeSeriesRef.current = volume as ISeriesApi<'Histogram'>;

    // Crosshair legend — read the hovered candle + volume bar
    if (typeof chart.subscribeCrosshairMove === 'function') {
      chart.subscribeCrosshairMove((param: MouseEventParams) => {
        const c = param.seriesData?.get(candles) as CandlestickData<UTCTimestamp> | undefined;
        if (!param.time || !c) {
          setHover(null);
          return;
        }
        const v = param.seriesData?.get(volume) as HistogramData<UTCTimestamp> | undefined;
        setHover({
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
          volume: v?.value ?? null,
        });
      });
    }

    return () => {
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Ticker change: reset buffers and series before new backfill arrives
  useEffect(() => {
    bars1sRef.current = [];
    displayBarsRef.current = [];
    candleSeriesRef.current?.setData([]);
    volumeSeriesRef.current?.setData([]);
    setHover(null);
  }, [ticker]);

  // Backfill arrival / timeframe switch: rebuild the display series.
  // On backfill the local 1s bars are replaced wholesale — the server ring
  // buffer is fed by the same funnel as SSE, so it already contains any
  // ticks that streamed while the fetch was in flight.
  useEffect(() => {
    if (history?.ticker === ticker && history.bars.length) {
      bars1sRef.current = history.bars.slice(-MAX_1S_BARS);
    }
    const display = aggregateBars(bars1sRef.current, timeframe).slice(-MAX_BARS);
    displayBarsRef.current = display;
    if (display.length) {
      candleSeriesRef.current?.setData(display.map(toCandle));
      volumeSeriesRef.current?.setData(display.map(toVolume));
    }
  }, [history, timeframe, ticker]);

  // Live tick: fold into 1s bars and the display bars, update both series
  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current || !priceUpdate) return;

    const tick = {
      timestamp: priceUpdate.timestamp,
      price: priceUpdate.price,
      volume: priceUpdate.volume,
    };

    applyTick(bars1sRef.current, tick, 1);
    if (bars1sRef.current.length > MAX_1S_BARS) {
      bars1sRef.current = bars1sRef.current.slice(-MAX_1S_BARS);
    }

    const bar = applyTick(displayBarsRef.current, tick, timeframe);
    if (!bar) return;

    if (displayBarsRef.current.length > TRIM_THRESHOLD) {
      // Batched trim: drop oldest bars, rebase both series on the capped buffer
      displayBarsRef.current = displayBarsRef.current.slice(-MAX_BARS);
      candleSeriesRef.current.setData(displayBarsRef.current.map(toCandle));
      volumeSeriesRef.current.setData(displayBarsRef.current.map(toVolume));
    } else {
      candleSeriesRef.current.update(toCandle(bar));
      volumeSeriesRef.current.update(toVolume(bar));
    }
  }, [priceUpdate, timeframe]);

  // Title bar: crosshair OHLCV legend when hovering, otherwise live price +
  // day change vs previous close, colored by day direction
  const dayPct = priceUpdate?.day_change_percent ?? null;
  const dayChange = priceUpdate?.day_change ?? null;
  const dayColor =
    dayPct == null || dayPct === 0 ? '#8b949e' : dayPct > 0 ? UP : DOWN;
  const arrow = dayPct == null || dayPct === 0 ? '' : dayPct > 0 ? '▲' : '▼';

  return (
    <div>
      <div className="px-3 py-1 text-xs font-semibold flex items-baseline gap-2">
        <span style={{ color: '#ecad0a' }}>{ticker}</span>
        {hover ? (
          <span className="tabular-nums text-terminal-muted" data-testid="main-chart-ohlc">
            O <span className="text-terminal-text">{hover.open.toFixed(2)}</span>{' '}
            H <span className="text-terminal-text">{hover.high.toFixed(2)}</span>{' '}
            L <span className="text-terminal-text">{hover.low.toFixed(2)}</span>{' '}
            C <span className="text-terminal-text">{hover.close.toFixed(2)}</span>
            {hover.volume != null && (
              <>
                {' '}
                V <span className="text-terminal-text">{Math.round(hover.volume).toLocaleString('en-US')}</span>
              </>
            )}
          </span>
        ) : (
          <>
            {priceUpdate && (
              <span className="tabular-nums text-terminal-text">
                {priceUpdate.price.toFixed(2)}
              </span>
            )}
            {dayPct != null && dayChange != null && (
              <span
                className="tabular-nums"
                style={{ color: dayColor }}
                data-testid="main-chart-day-change"
              >
                {arrow}
                {dayChange > 0 ? '+' : ''}
                {dayChange.toFixed(2)} ({dayPct > 0 ? '+' : ''}
                {dayPct.toFixed(2)}%)
              </span>
            )}
          </>
        )}
        {/* Timeframe switcher */}
        <span className="ml-auto flex gap-1">
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf.label}
              type="button"
              data-testid={`tf-${tf.label}`}
              onClick={() => setTimeframe(tf.seconds)}
              className={`px-1.5 py-0.5 rounded text-[10px] font-semibold transition-colors ${
                timeframe === tf.seconds
                  ? 'bg-terminal-surface text-terminal-text border border-terminal-border'
                  : 'text-terminal-muted hover:text-terminal-text border border-transparent'
              }`}
            >
              {tf.label}
            </button>
          ))}
        </span>
      </div>
      <div ref={containerRef} style={{ width: '100%', height: '280px' }} />
    </div>
  );
}
