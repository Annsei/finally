/**
 * market.tsx — /market full-market page (P1 §4). Exported statically as
 * market/index.html (trailingSlash: true) so deep links and refreshes work
 * behind StaticFiles(html=True).
 *
 * Data: GET /api/market/quotes (SWR 10s — initial snapshot + sector) merged
 * with the priceStore live overlay (the app-level SSE stream pushes the whole
 * universe). Names come from profile.names (cn), direction colours only via
 * terminal-up/down classes or var(--color-up/down).
 *
 * Sections:
 *   market-grid       sortable quote table, rows market-row-${ticker}
 *   market-heatmap    DOM sector treemap, tiles market-heatmap-tile-${ticker}
 *   market-events     event archive (P1 §3.3) with market-events-more paging
 *
 * P4 additive sections (existing blocks above untouched):
 *   market-sentiment    DOM sentiment gauge (P4 §1), sidebar top
 *   market-correlation  NxN correlation heatmap (P4 §2), below the grid
 *
 * D1 additive section (existing blocks untouched):
 *   history-coverage    per-ticker daily-bar coverage + history-sync-button
 *                       (HistoryCoverageCard, D1 §5), sidebar bottom
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/compat/router';
import useSWR from 'swr';
import AppShell from '@/components/AppShell';
import EventArchive from '@/components/EventArchive';
import HistoryCoverageCard from '@/components/HistoryCoverageCard';
import MarketCorrelation from '@/components/MarketCorrelation';
import MarketSentiment from '@/components/MarketSentiment';
import SymbolLink from '@/components/SymbolLink';
import { fetcher } from '@/lib/fetcher';
import { usePriceStore } from '@/stores/priceStore';
import { useMarketProfile } from '@/lib/marketProfile';
import { useT } from '@/lib/i18n';
import { formatLargeCount } from '@/lib/format';
import type { MarketQuote, MarketQuotesResponse } from '@/types/market';

export type MarketSortKey = 'code' | 'day' | 'volume';
export interface MarketSort {
  key: MarketSortKey;
  dir: 'asc' | 'desc';
}

// Default: code ascending — deterministic, test-friendly (P1 §4).
export const DEFAULT_SORT: MarketSort = { key: 'code', dir: 'asc' };

/** Pure client-side sort — ties always break by ticker asc for determinism. */
export function sortQuotes(quotes: MarketQuote[], sort: MarketSort): MarketQuote[] {
  const mult = sort.dir === 'asc' ? 1 : -1;
  return [...quotes].sort((a, b) => {
    if (sort.key === 'code') {
      return mult * a.ticker.localeCompare(b.ticker);
    }
    const av = (sort.key === 'day' ? a.day_change_percent : a.volume) ?? 0;
    const bv = (sort.key === 'day' ? b.day_change_percent : b.volume) ?? 0;
    if (av !== bv) return mult * (av - bv);
    return a.ticker.localeCompare(b.ticker);
  });
}

/**
 * Heatmap saturation N for color-mix(in srgb, var(--color-up|down) N%,
 * transparent): linear in |day_change_percent|, full scale at a 3% move.
 */
export function heatSaturation(dayPct: number | undefined | null): number {
  if (dayPct == null || !Number.isFinite(dayPct)) return 0;
  return Math.round(Math.min(Math.abs(dayPct) / 3, 1) * 100);
}

// ---------------------------------------------------------------------------
// Quote row — per-row flash animation on the price cell (WatchlistRow pattern)
// ---------------------------------------------------------------------------
function MarketRow({
  quote,
  name,
  locale,
  onOpen,
}: {
  quote: MarketQuote;
  name?: string;
  locale: string;
  onOpen: (ticker: string) => void;
}) {
  const priceRef = useRef<HTMLTableCellElement>(null);
  const flashTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const cell = priceRef.current;
    if (!cell) return;
    if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current);
    cell.classList.remove('animate-flash-up', 'animate-flash-down');
    if (quote.direction === 'flat') return;

    void cell.offsetWidth; // force reflow so re-adding the class re-triggers
    const cls = quote.direction === 'up' ? 'animate-flash-up' : 'animate-flash-down';
    cell.classList.add(cls);
    flashTimeoutRef.current = setTimeout(() => cell.classList.remove(cls), 500);
    return () => {
      if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current);
    };
  }, [quote.direction, quote.timestamp]);

  // Steady-state colour is day-driven (vs prev close), flash is tick-driven.
  const dayPct = quote.day_change_percent ?? null;
  const dayColor =
    dayPct == null || dayPct === 0
      ? 'text-terminal-muted'
      : dayPct > 0
        ? 'text-terminal-up'
        : 'text-terminal-down';
  const arrow = dayPct == null || dayPct === 0 ? '' : dayPct > 0 ? '▲' : '▼';

  // A-share limit badges (limit-badge-* pattern) — never render on US quotes.
  const atLimitUp = quote.limit_up != null && quote.price >= quote.limit_up;
  const atLimitDown = quote.limit_down != null && quote.price <= quote.limit_down;

  return (
    <tr
      data-testid={`market-row-${quote.ticker}`}
      onClick={() => onOpen(quote.ticker)}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onOpen(quote.ticker);
        }
      }}
      tabIndex={0}
      aria-label={`${quote.ticker}${name ? ` ${name}` : ''}`}
      className="cursor-pointer border-b border-terminal-border/60 hover:bg-terminal-surface/50 focus:outline focus:outline-1 focus:outline-terminal-accent"
    >
      <td className="py-1 pl-1 font-semibold text-terminal-text">
        <span className="flex items-center gap-1">
          <SymbolLink code={quote.ticker} />
          {atLimitUp && (
            <span
              data-testid={`limit-badge-${quote.ticker}`}
              className="text-[9px] font-semibold px-1 rounded text-terminal-up border border-terminal-up/60"
            >
              涨停
            </span>
          )}
          {atLimitDown && (
            <span
              data-testid={`limit-badge-${quote.ticker}`}
              className="text-[9px] font-semibold px-1 rounded text-terminal-down border border-terminal-down/60"
            >
              跌停
            </span>
          )}
        </span>
        {name && (
          <span className="block text-[10px] font-normal text-terminal-muted leading-tight truncate">
            {name}
          </span>
        )}
      </td>
      <td ref={priceRef} className={`text-right py-1 tabular-nums ${dayColor}`}>
        {quote.price.toFixed(2)}
      </td>
      <td className={`text-right py-1 tabular-nums ${dayColor}`}>
        {dayPct != null ? `${arrow}${dayPct > 0 ? '+' : ''}${dayPct.toFixed(2)}%` : '—'}
      </td>
      <td className="text-right py-1 tabular-nums text-terminal-text">
        {quote.day_high != null ? quote.day_high.toFixed(2) : '—'}
      </td>
      <td className="text-right py-1 tabular-nums text-terminal-text">
        {quote.day_low != null ? quote.day_low.toFixed(2) : '—'}
      </td>
      <td className="text-right py-1 tabular-nums text-terminal-muted">
        {quote.volume != null ? formatLargeCount(quote.volume, locale) : '—'}
      </td>
      <td className="py-1 pr-1 text-right">
        <span className="text-[9px] px-1 rounded border border-terminal-border text-terminal-muted uppercase tracking-wide">
          {quote.sector}
        </span>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Sector heatmap — DOM tiles (no canvas); equal-size, grouped by sector.
// ---------------------------------------------------------------------------
function SectorHeatmap({
  quotes,
  onOpen,
}: {
  quotes: MarketQuote[];
  onOpen: (ticker: string) => void;
}) {
  const groups = useMemo(() => {
    const bySector = new Map<string, MarketQuote[]>();
    for (const q of quotes) {
      const sector = q.sector || 'other';
      const group = bySector.get(sector);
      if (group) group.push(q);
      else bySector.set(sector, [q]);
    }
    return [...bySector.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([sector, tiles]) => ({
        sector,
        tiles: tiles.sort((a, b) => a.ticker.localeCompare(b.ticker)),
      }));
  }, [quotes]);

  return (
    <div data-testid="market-heatmap" className="flex flex-col gap-2">
      {groups.map(({ sector, tiles }) => (
        <div key={sector}>
          <div className="text-[10px] font-semibold uppercase tracking-wider text-terminal-muted mb-1">
            {sector}
          </div>
          <div className="grid grid-cols-3 gap-1">
            {tiles.map((q) => {
              const pct = q.day_change_percent ?? 0;
              // Flat (0% or missing) tiles stay neutral — mirrors the grid
              // rows' handling of dayPct === 0 above.
              const direction = pct === 0 ? 'flat' : pct > 0 ? 'up' : 'down';
              const heat = heatSaturation(pct);
              const colorVar = pct > 0 ? 'var(--color-up)' : 'var(--color-down)';
              const textClass =
                direction === 'flat'
                  ? 'text-terminal-muted'
                  : direction === 'up'
                    ? 'text-terminal-up'
                    : 'text-terminal-down';
              return (
                <button
                  type="button"
                  key={q.ticker}
                  data-testid={`market-heatmap-tile-${q.ticker}`}
                  data-direction={direction}
                  data-heat={heat}
                  onClick={() => onOpen(q.ticker)}
                  className={`px-1 py-2 rounded text-center border border-terminal-border/40 hover:border-terminal-border transition-colors ${textClass}`}
                  style={{
                    background: `color-mix(in srgb, ${colorVar} ${heat}%, transparent)`,
                  }}
                >
                  <span className="block text-xs font-semibold text-terminal-text">
                    {q.ticker}
                  </span>
                  <span className="block text-[10px] tabular-nums">
                    {pct > 0 ? '+' : ''}
                    {pct.toFixed(2)}%
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function MarketPage() {
  const t = useT();
  const router = useRouter();
  const profile = useMarketProfile();
  const { data } = useSWR<MarketQuotesResponse>('/api/market/quotes', fetcher, {
    refreshInterval: 10_000,
  });
  // Live overlay — the single app-level SSE stream pushes the whole universe.
  const prices = usePriceStore((s) => s.prices);
  const [sort, setSort] = useState<MarketSort>(DEFAULT_SORT);

  const quotes = useMemo(() => {
    const base = data?.quotes ?? [];
    const merged: MarketQuote[] = base.map((q) => {
      const live = prices[q.ticker];
      return live ? { ...q, ...live, sector: q.sector } : q;
    });
    // SSE can outrun the first /quotes response — show live-only tickers too.
    const known = new Set(base.map((q) => q.ticker));
    for (const [ticker, update] of Object.entries(prices)) {
      if (!known.has(ticker)) merged.push({ ...update, sector: 'other' });
    }
    return sortQuotes(merged, sort);
  }, [data, prices, sort]);

  const openSymbol = (ticker: string) => {
    void router?.push({ pathname: '/symbol', query: { c: ticker } });
  };

  const toggleSort = (key: MarketSortKey) => {
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: key === 'code' ? 'asc' : 'desc' }
    );
  };

  const sortIndicator = (key: MarketSortKey) =>
    sort.key === key ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : '';

  const headerButton =
    'font-semibold uppercase tracking-wide hover:text-terminal-text transition-colors';

  return (
    <AppShell>
      <div className="flex gap-4 h-full min-h-0">
        {/* Left column: quote grid + correlation heatmap (P4 §2) */}
        <div className="flex-[3] min-w-0 flex flex-col gap-4 min-h-0">
        <section className="flex-1 min-w-0 flex flex-col min-h-0 border border-terminal-border rounded bg-terminal-surface/30">
          <h2 className="px-2 py-1.5 text-xs font-semibold text-terminal-muted uppercase tracking-wider border-b border-terminal-border shrink-0">
            {t('market.gridTitle')}
          </h2>
          <div className="flex-1 min-h-0 overflow-auto">
            {quotes.length === 0 ? (
              <p className="p-3 text-xs text-terminal-muted">{t('market.loading')}</p>
            ) : (
              <table data-testid="market-grid" className="w-full text-xs border-collapse">
                <thead>
                  <tr className="text-terminal-muted border-b border-terminal-border sticky top-0 bg-terminal-bg">
                    <th className="text-left py-1 pl-1">
                      <button
                        type="button"
                        data-testid="market-sort-code"
                        onClick={() => toggleSort('code')}
                        className={headerButton}
                      >
                        {t('market.colCode')}
                        {sortIndicator('code')}
                      </button>
                    </th>
                    <th className="text-right py-1 font-semibold uppercase tracking-wide">
                      {t('market.colPrice')}
                    </th>
                    <th className="text-right py-1">
                      <button
                        type="button"
                        data-testid="market-sort-day"
                        onClick={() => toggleSort('day')}
                        className={headerButton}
                      >
                        {t('market.colDayPct')}
                        {sortIndicator('day')}
                      </button>
                    </th>
                    <th className="text-right py-1 font-semibold uppercase tracking-wide">
                      {t('market.colHigh')}
                    </th>
                    <th className="text-right py-1 font-semibold uppercase tracking-wide">
                      {t('market.colLow')}
                    </th>
                    <th className="text-right py-1">
                      <button
                        type="button"
                        data-testid="market-sort-volume"
                        onClick={() => toggleSort('volume')}
                        className={headerButton}
                      >
                        {t('market.colVolume')}
                        {sortIndicator('volume')}
                      </button>
                    </th>
                    <th className="text-right py-1 pr-1 font-semibold uppercase tracking-wide">
                      {t('market.colSector')}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {quotes.map((q) => (
                    <MarketRow
                      key={q.ticker}
                      quote={q}
                      name={profile.names[q.ticker]}
                      locale={profile.locale}
                      onOpen={openSymbol}
                    />
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>

        {/* Correlation heatmap (P4 §2) — additive block below the grid */}
        <section className="shrink-0 max-h-[40%] border border-terminal-border rounded bg-terminal-surface/30 flex flex-col min-h-0">
          <h2 className="px-2 py-1.5 text-xs font-semibold text-terminal-muted uppercase tracking-wider border-b border-terminal-border shrink-0">
            {t('market.corrTitle')}
          </h2>
          <div className="overflow-auto min-h-0">
            <MarketCorrelation />
          </div>
        </section>
        </div>

        {/* Sentiment + heatmap + event archive */}
        <div className="flex-[2] min-w-0 flex flex-col gap-4 min-h-0">
          {/* Market sentiment gauge (P4 §1) — additive block, sidebar top */}
          <section className="shrink-0 border border-terminal-border rounded bg-terminal-surface/30 flex flex-col">
            <h2 className="px-2 py-1.5 text-xs font-semibold text-terminal-muted uppercase tracking-wider border-b border-terminal-border shrink-0">
              {t('market.sentimentTitle')}
            </h2>
            <MarketSentiment />
          </section>
          <section className="border border-terminal-border rounded bg-terminal-surface/30 flex flex-col min-h-0 max-h-[45%]">
            <h2 className="px-2 py-1.5 text-xs font-semibold text-terminal-muted uppercase tracking-wider border-b border-terminal-border shrink-0">
              {t('market.heatmapTitle')}
            </h2>
            <div className="p-2 overflow-auto min-h-0">
              <SectorHeatmap quotes={quotes} onOpen={openSymbol} />
            </div>
          </section>
          <section className="flex-1 border border-terminal-border rounded bg-terminal-surface/30 flex flex-col min-h-0">
            <h2 className="px-2 py-1.5 text-xs font-semibold text-terminal-muted uppercase tracking-wider border-b border-terminal-border shrink-0">
              {t('market.eventsTitle')}
            </h2>
            <div className="p-2 overflow-auto min-h-0 flex-1">
              <EventArchive prefix="market" emptyKey="market.eventsEmpty" />
            </div>
          </section>

          {/* Historical-data coverage + sync (D1 §5) — additive section; the
              blocks above are untouched */}
          <HistoryCoverageCard />
        </div>
      </div>
    </AppShell>
  );
}
