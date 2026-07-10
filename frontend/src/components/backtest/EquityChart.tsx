/**
 * EquityChart.tsx — backtest equity vs buy-and-hold chart (P2 §8, extracted
 * verbatim from BacktestPanel as a pure refactor: DOM and testids unchanged).
 *
 * Also the single home of the backtest direction-colour constants (G28/R28
 * fill tints) and the `equityColors` resolver — every consumer imports from
 * here so the palette can never drift between the panel and the run pages.
 */
import { useEffect, useRef } from 'react';
import { createChart, BaselineSeries, LineSeries } from 'lightweight-charts';
import type { ISeriesApi, IChartApi, UTCTimestamp } from 'lightweight-charts';
import { directionColors } from '@/lib/marketProfile';
import type { BacktestPoint } from '@/types/market';

// Direction fill tints for the equity canvas (lightweight-charts can't read CSS
// vars). Above-baseline uses the "up" tint, below-baseline the "down" tint —
// swapped on the A-share market. Mirrors PnLChart so the two charts agree.
const G28 = 'rgba(34, 197, 94, 0.28)';
const G03 = 'rgba(34, 197, 94, 0.03)';
const R28 = 'rgba(239, 68, 68, 0.28)';
const R03 = 'rgba(239, 68, 68, 0.03)';

export interface DirColors {
  upHex: string;
  downHex: string;
  upFill1: string;
  upFill2: string;
  downFill1: string;
  downFill2: string;
}

export function equityColors(upIsRed: boolean): DirColors {
  const { up: upHex, down: downHex } = directionColors(upIsRed);
  return {
    upHex,
    downHex,
    upFill1: upIsRed ? R28 : G28,
    upFill2: upIsRed ? R03 : G03,
    downFill1: upIsRed ? G03 : R03,
    downFill2: upIsRed ? G28 : R28,
  };
}

// Equity vs buy-and-hold chart — mounted only when a result exists, so the
// chart is created fresh per mount (same lifecycle discipline as PnLChart).
//
// Callers must provide `baseValue`: backtests use the active market profile's
// seed cash, while strategy performance passes 0 for realized P&L.
export default function EquityChart({
  equity,
  baseline,
  colors,
  baseValue,
}: {
  equity: BacktestPoint[];
  baseline: BacktestPoint[];
  colors: DirColors;
  baseValue: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const equityRef = useRef<ISeriesApi<'Baseline'> | null>(null);
  const baselineRef = useRef<ISeriesApi<'Line'> | null>(null);
  const { upHex, downHex, upFill1, upFill2, downFill1, downFill2 } = colors;

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
      rightPriceScale: { borderColor: '#30363d' },
      timeScale: { borderColor: '#30363d', timeVisible: false },
    });

    // Strategy equity: profit-tint above the caller's base value (market seed, or 0
    // for the strategy-performance P&L curve), loss-tint below. Colours come
    // from the market profile (swapped on A-shares), same as PnLChart.
    const equitySeries = chart.addSeries(BaselineSeries, {
      baseValue: { type: 'price', price: baseValue },
      topLineColor: upHex,
      topFillColor1: upFill1,
      topFillColor2: upFill2,
      bottomLineColor: downHex,
      bottomFillColor1: downFill1,
      bottomFillColor2: downFill2,
      lineWidth: 2,
    });
    // Buy & hold reference: muted dashed line
    const baselineSeries = chart.addSeries(LineSeries, {
      color: '#8b949e',
      lineWidth: 1,
      lineStyle: 2, // dashed
      priceLineVisible: false,
      lastValueVisible: false,
    });

    chartRef.current = chart;
    equityRef.current = equitySeries as ISeriesApi<'Baseline'>;
    baselineRef.current = baselineSeries as ISeriesApi<'Line'>;

    return () => {
      chart.remove();
      chartRef.current = null;
      equityRef.current = null;
      baselineRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Recolor when the market's direction colours resolve/change after mount.
  useEffect(() => {
    equityRef.current?.applyOptions({
      topLineColor: upHex,
      topFillColor1: upFill1,
      topFillColor2: upFill2,
      bottomLineColor: downHex,
      bottomFillColor1: downFill1,
      bottomFillColor2: downFill2,
      baseValue: { type: 'price', price: baseValue },
    });
  }, [upHex, downHex, upFill1, upFill2, downFill1, downFill2, baseValue]);

  useEffect(() => {
    if (!equityRef.current || !baselineRef.current) return;
    const toPoints = (pts: BacktestPoint[]) =>
      pts.map((p) => ({ time: p.time as UTCTimestamp, value: p.value }));
    equityRef.current.setData(toPoints(equity));
    baselineRef.current.setData(toPoints(baseline));
    chartRef.current?.timeScale?.()?.fitContent?.();
  }, [equity, baseline]);

  return <div ref={containerRef} data-testid="backtest-chart" style={{ width: '100%', height: '180px' }} />;
}
