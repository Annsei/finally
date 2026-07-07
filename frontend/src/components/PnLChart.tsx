/**
 * PnLChart.tsx — portfolio value over time (FRONTEND_REALISM.md §2.4)
 *
 * BaselineSeries anchored at the $10,000 seed cash: green above the baseline,
 * red below — profit/loss is visible at a glance. Range selector filters the
 * snapshots client-side, using the LAST snapshot as the "now" reference so
 * rendering is deterministic (no wall-clock dependency).
 */
import { useEffect, useRef, useState } from 'react';
import { createChart, BaselineSeries } from 'lightweight-charts';
import type { ISeriesApi, IChartApi, UTCTimestamp } from 'lightweight-charts';
import useSWR from 'swr';
import { fetcher } from '@/lib/fetcher';
import type { PortfolioHistoryResponse } from '@/types/market';
import { useMarketProfile, directionColors } from '@/lib/marketProfile';
import { useT } from '@/lib/i18n';

// Portfolio baseline — the seed cash every session starts from (PLAN.md §7)
export const PNL_BASELINE = 10000;

type Range = '1H' | 'TODAY' | 'ALL';

interface Point {
  time: UTCTimestamp;
  value: number;
}

/** Filter ascending points by range, anchored to the last point's time. */
export function filterByRange(points: Point[], range: Range): Point[] {
  if (range === 'ALL' || points.length === 0) return points;
  const last = points[points.length - 1].time as number;
  if (range === '1H') {
    return points.filter((p) => (p.time as number) >= last - 3600);
  }
  // TODAY: since local midnight of the last point's day
  const d = new Date(last * 1000);
  d.setHours(0, 0, 0, 0);
  const dayStart = Math.floor(d.getTime() / 1000);
  return points.filter((p) => (p.time as number) >= dayStart);
}

// Direction fill tints (canvas can't read CSS vars). Above-baseline uses the
// "up" tint, below-baseline the "down" tint — swapped on the A-share market.
const G28 = 'rgba(34, 197, 94, 0.28)';
const G03 = 'rgba(34, 197, 94, 0.03)';
const R28 = 'rgba(239, 68, 68, 0.28)';
const R03 = 'rgba(239, 68, 68, 0.03)';

export default function PnLChart() {
  const t = useT();
  const profile = useMarketProfile();
  const { up: upHex, down: downHex } = directionColors(profile.up_is_red);
  const upFill1 = profile.up_is_red ? R28 : G28;
  const upFill2 = profile.up_is_red ? R03 : G03;
  const downFill1 = profile.up_is_red ? G03 : R03;
  const downFill2 = profile.up_is_red ? G28 : R28;

  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Baseline'> | null>(null);
  const [range, setRange] = useState<Range>('ALL');

  const { data } = useSWR<PortfolioHistoryResponse>('/api/portfolio/history', fetcher, {
    refreshInterval: 30_000,
  });

  // Mount: create chart + baseline series; cleanup calls chart.remove()
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { color: 'transparent' },
        textColor: '#8b949e',
        // Attribution lives in the README — the logo ghosts over dark charts
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
        // Real snapshot timestamps — show intraday time instead of dates
        timeVisible: true,
        secondsVisible: false,
      },
    });

    // v5 API: addSeries(BaselineSeries, options) — green above $10k, red below
    const series = chart.addSeries(BaselineSeries, {
      baseValue: { type: 'price', price: PNL_BASELINE },
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

  // Recolor when the market's direction colours resolve/change after mount.
  useEffect(() => {
    seriesRef.current?.applyOptions({
      topLineColor: upHex,
      topFillColor1: upFill1,
      topFillColor2: upFill2,
      bottomLineColor: downHex,
      bottomFillColor1: downFill1,
      bottomFillColor2: downFill2,
    });
  }, [upHex, downHex, upFill1, upFill2, downFill1, downFill2]);

  // Data: update chart when SWR data arrives/refreshes or the range changes.
  // Real recorded_at timestamps; snapshots are taken every 30s plus after
  // each trade, so two can share a second — sort ascending and keep the
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
    const deduped = sorted.filter(
      (p, i) => i === sorted.length - 1 || (sorted[i + 1].time as number) !== (p.time as number)
    );
    const points = filterByRange(deduped, range);
    if (!points.length) return;
    seriesRef.current.setData(points);
    chartRef.current?.timeScale?.()?.fitContent?.();
  }, [data, range]);

  const hasData = data?.snapshots?.length;

  return (
    <div style={{ width: '100%' }}>
      <div className="flex items-center justify-between px-3 py-1">
        <span className="text-xs font-semibold text-terminal-muted uppercase tracking-wide">
          {t('pnl.title')}
        </span>
        <span className="flex gap-1">
          {(['1H', 'TODAY', 'ALL'] as const).map((r) => (
            <button
              key={r}
              type="button"
              data-testid={`pnl-range-${r}`}
              onClick={() => setRange(r)}
              className={`px-1.5 py-0.5 rounded text-[10px] font-semibold transition-colors ${
                range === r
                  ? 'bg-terminal-surface text-terminal-text border border-terminal-border'
                  : 'text-terminal-muted hover:text-terminal-text border border-transparent'
              }`}
            >
              {r}
            </button>
          ))}
        </span>
      </div>
      {!hasData && (
        <div className="p-4 text-xs" style={{ color: '#8b949e' }}>
          {t('pnl.empty')}
        </div>
      )}
      <div ref={containerRef} style={{ width: '100%', height: '160px' }} />
    </div>
  );
}
