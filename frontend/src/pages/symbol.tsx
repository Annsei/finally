/**
 * symbol.tsx — /symbol?c=CODE detail page (P1 §5).
 *
 * Static-export hydration (P1 §1): on first render router.query is {} —
 * `c` is undefined until the router is ready. The page renders the
 * `symbol-empty` placeholder in that state and only mounts the detail view
 * once the query resolves. Codes are uppercase-normalized.
 *
 * Reuses the desk's building blocks unchanged: <MainChart ticker={c}/> (multi
 * timeframe + direction colours + limit badges), <TradeBar selectedTicker={c}/>
 * with the AppShell trade-revalidation key set, useTicker(c) for live stats
 * (initial fallback: /api/market/quotes).
 */
import { useEffect, useRef } from 'react';
import { useRouter } from 'next/compat/router';
import useSWR, { useSWRConfig } from 'swr';
import AppShell, { TRADE_REVALIDATE_KEYS } from '@/components/AppShell';
import EventArchive from '@/components/EventArchive';
import MainChart from '@/components/MainChart';
import TradeBar from '@/components/TradeBar';
import { fetcher } from '@/lib/fetcher';
import { useTicker } from '@/stores/priceStore';
import { useUiStore } from '@/stores/uiStore';
import { useMarketProfile } from '@/lib/marketProfile';
import { useT } from '@/lib/i18n';
import { formatLargeCount, formatMoney, formatShares } from '@/lib/format';
import type {
  MarketQuotesResponse,
  PortfolioResponse,
  TradesResponse,
} from '@/types/market';

/**
 * Day amplitude: (high − low) / prev_close × 100 (P1 §5). Guarded — returns
 * null unless all inputs are finite and prev_close > 0.
 */
export function amplitudePct(
  high: number | undefined | null,
  low: number | undefined | null,
  prevClose: number | undefined | null
): number | null {
  if (high == null || low == null || prevClose == null) return null;
  if (!Number.isFinite(high) || !Number.isFinite(low) || !Number.isFinite(prevClose)) return null;
  if (prevClose <= 0) return null;
  return ((high - low) / prevClose) * 100;
}

function formatTradeTime(iso: string, locale: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleTimeString(locale, { hour12: false });
}

function StatRow({
  label,
  value,
  valueClass,
  testid,
}: {
  label: string;
  value: string;
  valueClass?: string;
  testid?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-2 py-0.5">
      <span className="text-terminal-muted">{label}</span>
      <span data-testid={testid} className={`tabular-nums ${valueClass ?? 'text-terminal-text'}`}>
        {value}
      </span>
    </div>
  );
}

function SymbolDetail({ code }: { code: string }) {
  const t = useT();
  const profile = useMarketProfile();
  const money = { currency_symbol: profile.currency_symbol, locale: profile.locale };
  const name = profile.names[code];

  // Live quote first, /api/market/quotes snapshot as the initial fallback.
  const live = useTicker(code);
  const { data: quotesData } = useSWR<MarketQuotesResponse>('/api/market/quotes', fetcher);
  const q = live ?? quotesData?.quotes.find((quote) => quote.ticker === code);

  const { data: portfolio } = useSWR<PortfolioResponse>('/api/portfolio/', fetcher, {
    refreshInterval: 5000,
  });
  const position = portfolio?.positions.find((p) => p.ticker === code);

  const tradesKey = `/api/portfolio/trades?ticker=${encodeURIComponent(code)}&limit=100`;
  const { data: tradesData } = useSWR<TradesResponse>(tradesKey, fetcher);
  const trades = tradesData?.trades ?? [];

  const { mutate } = useSWRConfig();
  const refreshAfterTrade = () => {
    for (const key of TRADE_REVALIDATE_KEYS) void mutate(key);
    void mutate(tradesKey);
  };

  const setPendingChatMessage = useUiStore((s) => s.setPendingChatMessage);
  const setChatOpen = useUiStore((s) => s.setChatOpen);
  const analyze = () => {
    // One-shot handoff (P1 §2): the globally docked ChatPanel consumes this,
    // auto-sends it as a user message, and clears it.
    setPendingChatMessage(t('symbol.aiPrompt', { ticker: code }));
    setChatOpen(true);
  };

  // Big-price flash — same tick-driven pattern as WatchlistRow.
  const priceRef = useRef<HTMLSpanElement>(null);
  const flashTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    const el = priceRef.current;
    if (!el || !live) return;
    if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current);
    el.classList.remove('animate-flash-up', 'animate-flash-down');
    if (live.direction === 'flat') return;
    void el.offsetWidth;
    const cls = live.direction === 'up' ? 'animate-flash-up' : 'animate-flash-down';
    el.classList.add(cls);
    flashTimeoutRef.current = setTimeout(() => el.classList.remove(cls), 500);
    return () => {
      if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current);
    };
  }, [live]);

  const dayPct = q?.day_change_percent ?? null;
  const dayColor =
    dayPct == null || dayPct === 0
      ? 'text-terminal-muted'
      : dayPct > 0
        ? 'text-terminal-up'
        : 'text-terminal-down';
  const arrow = dayPct == null || dayPct === 0 ? '' : dayPct > 0 ? '▲' : '▼';

  const atLimitUp = q?.limit_up != null && q.price >= q.limit_up;
  const atLimitDown = q?.limit_down != null && q.price <= q.limit_down;

  const amp = amplitudePct(q?.day_high, q?.day_low, q?.prev_close);

  const pnlColor =
    position == null || position.unrealized_pnl === 0
      ? 'text-terminal-muted'
      : position.unrealized_pnl > 0
        ? 'text-terminal-up'
        : 'text-terminal-down';

  const sectionClass =
    'border border-terminal-border rounded bg-terminal-surface/30 flex flex-col min-h-0';
  const sectionTitleClass =
    'px-2 py-1.5 text-xs font-semibold text-terminal-muted uppercase tracking-wider border-b border-terminal-border shrink-0';

  return (
    <div className="flex flex-col gap-3 h-full min-h-0">
      {/* Title row: code + name + live price (flash) + day % + limit badge */}
      <div className="flex items-baseline gap-3 flex-wrap shrink-0">
        <h1
          data-testid="symbol-title"
          className="text-xl font-semibold text-terminal-text tracking-wide"
        >
          {code}
        </h1>
        {name && <span className="text-sm text-terminal-muted">{name}</span>}
        <span
          ref={priceRef}
          data-testid="symbol-price"
          className={`text-2xl font-semibold tabular-nums ${dayColor}`}
        >
          {q ? formatMoney(q.price, money) : '—'}
        </span>
        <span className={`text-sm tabular-nums ${dayColor}`}>
          {dayPct != null ? `${arrow}${dayPct > 0 ? '+' : ''}${dayPct.toFixed(2)}%` : '—'}
        </span>
        {atLimitUp && (
          <span
            data-testid={`limit-badge-${code}`}
            className="text-[9px] font-semibold px-1 rounded text-terminal-up border border-terminal-up/60"
          >
            涨停
          </span>
        )}
        {atLimitDown && (
          <span
            data-testid={`limit-badge-${code}`}
            className="text-[9px] font-semibold px-1 rounded text-terminal-down border border-terminal-down/60"
          >
            跌停
          </span>
        )}
        <button
          type="button"
          data-testid="symbol-ai-analyze"
          onClick={analyze}
          className="ml-auto px-2 py-1 rounded text-[10px] font-semibold uppercase tracking-wider text-white"
          style={{ backgroundColor: '#753991' }}
        >
          {t('symbol.aiAnalyze')}
        </button>
      </div>

      <div className="flex gap-4 flex-1 min-h-0">
        {/* Main column: chart → trade bar → my fills */}
        <div className="flex-[3] min-w-0 flex flex-col gap-3 overflow-auto">
          <MainChart ticker={code} />
          <TradeBar selectedTicker={code} onTradeComplete={refreshAfterTrade} />
          <section data-testid="symbol-trades" className={sectionClass}>
            <h2 className={sectionTitleClass}>{t('symbol.tradesTitle')}</h2>
            <div className="p-2 overflow-auto min-h-0">
              {trades.length === 0 ? (
                <p className="text-xs text-terminal-muted">
                  {t('symbol.tradesEmpty', { ticker: code })}
                </p>
              ) : (
                <table className="w-full text-xs border-collapse">
                  <thead>
                    <tr className="text-terminal-muted border-b border-terminal-border">
                      <th className="text-left py-1 pl-1 font-semibold">{t('fills.colTime')}</th>
                      <th className="text-left py-1 font-semibold">{t('fills.colSide')}</th>
                      <th className="text-right py-1 font-semibold">{t('fills.colQty')}</th>
                      <th className="text-right py-1 font-semibold">{t('fills.colPrice')}</th>
                      <th className="text-right py-1 pr-1 font-semibold">
                        {t('fills.colRealized')}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {trades.map((trade) => (
                      <tr
                        key={trade.id}
                        data-testid={`symbol-trade-${trade.id}`}
                        className="border-b border-terminal-border/60"
                      >
                        <td className="py-1 pl-1 tabular-nums text-terminal-muted">
                          {formatTradeTime(trade.executed_at, profile.locale)}
                        </td>
                        <td
                          className={`py-1 font-semibold ${
                            trade.side === 'buy' ? 'text-terminal-up' : 'text-terminal-down'
                          }`}
                        >
                          {t(trade.side === 'buy' ? 'tradebar.buy' : 'tradebar.sell')}
                        </td>
                        <td className="text-right py-1 tabular-nums text-terminal-text">
                          {formatShares(trade.quantity, profile)}
                        </td>
                        <td className="text-right py-1 tabular-nums text-terminal-text">
                          {formatMoney(trade.price, money)}
                        </td>
                        <td
                          className={`text-right py-1 pr-1 tabular-nums ${
                            trade.realized_pnl == null
                              ? 'text-terminal-muted'
                              : trade.realized_pnl >= 0
                                ? 'text-terminal-up'
                                : 'text-terminal-down'
                          }`}
                        >
                          {trade.realized_pnl != null
                            ? `${trade.realized_pnl >= 0 ? '+' : '-'}${formatMoney(
                                Math.abs(trade.realized_pnl),
                                money
                              )}`
                            : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </section>
        </div>

        {/* Side column: day stats → my position → event history */}
        <div className="flex-[1] min-w-[220px] flex flex-col gap-3 overflow-auto">
          <section data-testid="symbol-stats" className={sectionClass}>
            <h2 className={sectionTitleClass}>{t('symbol.statsTitle')}</h2>
            <div className="p-2 text-xs">
              <StatRow
                label={t('symbol.prevClose')}
                value={q?.prev_close != null ? formatMoney(q.prev_close, money) : '—'}
              />
              <StatRow
                label={t('symbol.high')}
                value={q?.day_high != null ? formatMoney(q.day_high, money) : '—'}
              />
              <StatRow
                label={t('symbol.low')}
                value={q?.day_low != null ? formatMoney(q.day_low, money) : '—'}
              />
              <StatRow
                label={t('symbol.amplitude')}
                value={amp != null ? `${amp.toFixed(2)}%` : '—'}
                testid="symbol-amplitude"
              />
              <StatRow
                label={t('symbol.volume')}
                value={q?.volume != null ? formatLargeCount(q.volume, profile.locale) : '—'}
              />
              <StatRow
                label={t('symbol.bid')}
                value={q?.bid != null ? formatMoney(q.bid, money) : '—'}
              />
              <StatRow
                label={t('symbol.ask')}
                value={q?.ask != null ? formatMoney(q.ask, money) : '—'}
              />
              {q?.limit_up != null && (
                <StatRow
                  label={t('symbol.limitUp')}
                  value={formatMoney(q.limit_up, money)}
                  valueClass="text-terminal-up"
                  testid="symbol-limit-up"
                />
              )}
              {q?.limit_down != null && (
                <StatRow
                  label={t('symbol.limitDown')}
                  value={formatMoney(q.limit_down, money)}
                  valueClass="text-terminal-down"
                  testid="symbol-limit-down"
                />
              )}
            </div>
          </section>

          <section data-testid="symbol-position" className={sectionClass}>
            <h2 className={sectionTitleClass}>{t('symbol.positionTitle')}</h2>
            <div className="p-2 text-xs">
              {position ? (
                <>
                  <StatRow
                    label={t('symbol.posQty')}
                    value={formatShares(position.quantity, profile)}
                  />
                  <StatRow
                    label={t('symbol.posAvgCost')}
                    value={formatMoney(position.avg_cost, money)}
                  />
                  <StatRow
                    label={t('symbol.posPnl')}
                    value={`${position.unrealized_pnl >= 0 ? '+' : '-'}${formatMoney(
                      Math.abs(position.unrealized_pnl),
                      money
                    )} (${position.pnl_pct >= 0 ? '+' : ''}${position.pnl_pct.toFixed(2)}%)`}
                    valueClass={pnlColor}
                  />
                </>
              ) : (
                <p className="text-terminal-muted">
                  {t('symbol.positionEmpty', { ticker: code })}
                </p>
              )}
            </div>
          </section>

          <section className={sectionClass}>
            <h2 className={sectionTitleClass}>{t('symbol.eventsTitle')}</h2>
            <div className="p-2 overflow-auto min-h-0">
              <EventArchive prefix="symbol" ticker={code} emptyKey="symbol.eventsEmpty" />
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

export default function SymbolPage() {
  const router = useRouter();
  const raw = router?.query?.c;
  const code =
    typeof raw === 'string' && raw.trim() !== '' ? raw.trim().toUpperCase() : null;
  const t = useT();

  return (
    <AppShell>
      {code === null ? (
        <div
          data-testid="symbol-empty"
          className="flex items-center justify-center h-full text-terminal-muted text-xs"
        >
          {t('symbol.empty')}
        </div>
      ) : (
        <SymbolDetail code={code} />
      )}
    </AppShell>
  );
}
