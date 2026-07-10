/**
 * PlayerEquity.tsx — public player equity curve (P4 §4).
 *
 * Same recipe as PnLChart: lightweight-charts v5 BaselineSeries anchored at
 * the market's seed cash (profile.seed_cash — $10k US / ¥100k CN), with the
 * direction colour pair above/below the baseline. Data comes from the
 * player's public equity_curve ([{time, value}], ascending) instead of
 * /api/portfolio/history.
 */
import { useEffect, useRef } from 'react';
import { createChart, BaselineSeries } from 'lightweight-charts';
import type { ISeriesApi, IChartApi, UTCTimestamp } from 'lightweight-charts';
import { useMarketProfile, directionColors } from '@/lib/marketProfile';
import type { PlayerEquityPoint } from '@/types/market';

interface Point {
  time: UTCTimestamp;
  value: number;
}

/**
 * Normalize an equity_curve entry's time to Unix seconds. Accepts Unix
 * seconds, Unix milliseconds (heuristic: > 1e12), or ISO strings; null when
 * unparseable.
 */
export function toUtcSeconds(time: number | string | undefined | null): number | null {
  if (time == null) return null;
  if (typeof time === 'number') {
    if (!Number.isFinite(time)) return null;
    return Math.floor(time > 1e12 ? time / 1000 : time);
  }
  const ms = Date.parse(time);
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
}

/**
 * Curve → strictly-ascending chart points: parse times, sort ascending, and
 * keep the LAST value per second (lightweight-charts requires strictly
 * ascending times — same dedupe rule as PnLChart).
 */
export function equityPoints(curve: PlayerEquityPoint[] | undefined | null): Point[] {
  const sorted = (curve ?? [])
    .map((p) => ({ time: toUtcSeconds(p.time), value: p.value }))
    .filter((p): p is { time: number; value: number } => p.time !== null && Number.isFinite(p.value))
    .sort((a, b) => a.time - b.time);
  return sorted
    .filter((p, i) => i === sorted.length - 1 || sorted[i + 1].time !== p.time)
    .map((p) => ({ time: p.time as UTCTimestamp, value: p.value }));
}

// Direction fill tints (canvas can't read CSS vars) — PnLChart's constants.
const G28 = 'rgba(34, 197, 94, 0.28)';
const G03 = 'rgba(34, 197, 94, 0.03)';
const R28 = 'rgba(239, 68, 68, 0.28)';
const R03 = 'rgba(239, 68, 68, 0.03)';

export default function PlayerEquity({ curve }: { curve: PlayerEquityPoint[] }) {
  const profile = useMarketProfile();
  const { up: upHex, down: downHex } = directionColors(profile.up_is_red);
  const upFill1 = profile.up_is_red ? R28 : G28;
  const upFill2 = profile.up_is_red ? R03 : G03;
  const downFill1 = profile.up_is_red ? G03 : R03;
  const downFill2 = profile.up_is_red ? G28 : R28;

  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Baseline'> | null>(null);

  // Mount: create chart + baseline series; cleanup calls chart.remove()
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { color: 'transparent' },
        textColor: '#8b949e',
        attributionLogo: false,
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
        timeVisible: true,
        secondsVisible: false,
      },
    });

    // v5 API: addSeries(BaselineSeries, options) — anchored at the seed cash
    const series = chart.addSeries(BaselineSeries, {
      baseValue: { type: 'price', price: profile.seed_cash },
      topLineColor: upHex,
      topFillColor1: upFill1,
      topFillColor2: upFill2,
      bottomLineColor: downHex,
      bottomFillColor1: downFill1,
      bottomFillColor2: downFill2,
      lineWidth: 2,
    });

    chartRef.current = chart;
    seriesRef.current = series as ISeriesApi<'Baseline'>;

    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Recolor/re-anchor when the market profile resolves after mount.
  useEffect(() => {
    seriesRef.current?.applyOptions({
      baseValue: { type: 'price', price: profile.seed_cash },
      topLineColor: upHex,
      topFillColor1: upFill1,
      topFillColor2: upFill2,
      bottomLineColor: downHex,
      bottomFillColor1: downFill1,
      bottomFillColor2: downFill2,
    });
  }, [profile.seed_cash, upHex, downHex, upFill1, upFill2, downFill1, downFill2]);

  // Data: update when the curve changes.
  useEffect(() => {
    if (!seriesRef.current) return;
    const points = equityPoints(curve);
    if (!points.length) return;
    seriesRef.current.setData(points);
    chartRef.current?.timeScale?.()?.fitContent?.();
  }, [curve]);

  return (
    <div data-testid="player-equity" style={{ width: '100%' }}>
      <div ref={containerRef} style={{ width: '100%', height: '180px' }} />
    </div>
  );
}
