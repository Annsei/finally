/**
 * HistoryCoverageCard.tsx — /market historical-data readiness card (D1 §5).
 *
 * Renders per-ticker daily-bar coverage from GET /api/market/history/coverage
 * (`history-coverage` card, rows `history-coverage-row-${ticker}`) and hosts
 * the `history-sync-button`: POST /api/market/history/sync {source: "auto"}
 * — auto picks the market's real source and falls back to sample (D1 §2).
 * While the sync is in flight the button is disabled with a spinner; the
 * outcome renders as an inline toast (`history-sync-toast`) with success /
 * failure counts. Guest-usable — the endpoint needs only the cookie session
 * (Bearer keys are rejected server-side), so no auth gating here.
 *
 * Pure helpers (exported for jest): coverageRows (tolerant response
 * extraction), syncToastCounts (per-ticker results → {ok, failed}).
 */
import { useState } from 'react';
import useSWR from 'swr';
import { fetcher } from '@/lib/fetcher';
import { useT } from '@/lib/i18n';
import { sourceLabel } from '@/components/backtest/SourceBadge';
import type { HistoryCoverageRow, HistorySyncResponse } from '@/types/market';

export const HISTORY_COVERAGE_KEY = '/api/market/history/coverage';

/**
 * Coverage rows out of the endpoint payload. Tolerates a bare array or an
 * object wrapping the list under `coverage` / `tickers` / `results`; anything
 * else → [] (renders the empty state instead of crashing the page).
 */
export function coverageRows(data: unknown): HistoryCoverageRow[] {
  if (Array.isArray(data)) return data as HistoryCoverageRow[];
  if (data && typeof data === 'object') {
    const obj = data as Record<string, unknown>;
    for (const key of ['coverage', 'tickers', 'results']) {
      if (Array.isArray(obj[key])) return obj[key] as HistoryCoverageRow[];
    }
  }
  return [];
}

/**
 * Sync outcome counts — a ticker succeeded iff bars persisted (`bars` > 0).
 * `error` alone is NOT failure: auto-mode syncs that fall back to sample still
 * persist bars but carry the real source's error as an annotation (D1 §2
 * "失败回落 sample 并在响应标注"), and must count as successes.
 */
export function syncToastCounts(res: HistorySyncResponse | null | undefined): {
  ok: number;
  failed: number;
} {
  const results = Array.isArray(res?.results) ? res!.results : [];
  const failed = results.filter((r) => !(typeof r?.bars === 'number' && r.bars > 0)).length;
  return { ok: results.length - failed, failed };
}

export default function HistoryCoverageCard() {
  const t = useT();
  const { data, mutate } = useSWR<unknown>(HISTORY_COVERAGE_KEY, fetcher);
  const [syncing, setSyncing] = useState(false);
  const [toast, setToast] = useState<{ text: string; failed: boolean } | null>(null);

  // undefined = first fetch still in flight → loading copy, not the empty state.
  const rows = data === undefined ? null : coverageRows(data);

  const sync = async () => {
    if (syncing) return;
    setSyncing(true);
    setToast(null);
    try {
      const res = await fetch('/api/market/history/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source: 'auto' }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error ?? `${t('history.syncFailed')} (${res.status})`);
      }
      const counts = syncToastCounts((await res.json()) as HistorySyncResponse);
      setToast({ text: t('history.syncDone', counts), failed: counts.ok === 0 });
      await mutate();
    } catch (e) {
      setToast({
        text: e instanceof Error ? e.message : t('history.syncFailed'),
        failed: true,
      });
    } finally {
      setSyncing(false);
    }
  };

  return (
    <section
      data-testid="history-coverage"
      className="shrink-0 border border-terminal-border rounded bg-terminal-surface/30 flex flex-col"
    >
      <h2 className="px-2 py-1.5 text-xs font-semibold text-terminal-muted uppercase tracking-wider border-b border-terminal-border shrink-0 flex items-center justify-between gap-2">
        <span>{t('history.title')}</span>
        <button
          type="button"
          data-testid="history-sync-button"
          onClick={() => void sync()}
          disabled={syncing}
          className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold normal-case tracking-normal text-white disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
          style={{ backgroundColor: '#753991' }}
        >
          {syncing && (
            <span
              data-testid="history-sync-spinner"
              aria-hidden="true"
              className="inline-block h-2.5 w-2.5 rounded-full border border-white/40 border-t-white animate-spin"
            />
          )}
          {syncing ? t('history.syncing') : t('history.sync')}
        </button>
      </h2>
      <div className="p-2 overflow-auto min-h-0 max-h-48">
        {toast && (
          <p
            data-testid="history-sync-toast"
            className="mb-1.5 px-2 py-1 rounded text-[10px] leading-tight text-terminal-text bg-terminal-bg/60"
            // Success/failure framing, not market direction — fixed colours
            // (TradeBar toast precedent), never the --color-up/down variables.
            style={{ border: `1px solid ${toast.failed ? '#ef4444' : '#22c55e'}` }}
          >
            {toast.text}
          </p>
        )}
        {rows === null ? (
          <p className="text-xs text-terminal-muted">{t('history.loading')}</p>
        ) : rows.length === 0 ? (
          <p className="text-xs text-terminal-muted">{t('history.empty')}</p>
        ) : (
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr className="text-terminal-muted border-b border-terminal-border">
                <th className="text-left py-1 pl-1 font-semibold uppercase tracking-wide">
                  {t('history.colTicker')}
                </th>
                <th className="text-left py-1 font-semibold uppercase tracking-wide">
                  {t('history.colRange')}
                </th>
                <th className="text-right py-1 font-semibold uppercase tracking-wide">
                  {t('history.colBars')}
                </th>
                <th className="text-right py-1 pr-1 font-semibold uppercase tracking-wide">
                  {t('history.colSource')}
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.ticker}
                  data-testid={`history-coverage-row-${row.ticker}`}
                  className="border-b border-terminal-border/60"
                >
                  <td className="py-1 pl-1 font-semibold text-terminal-text">{row.ticker}</td>
                  <td className="py-1 tabular-nums text-terminal-text whitespace-nowrap">
                    {row.from} → {row.to}
                  </td>
                  <td className="text-right py-1 tabular-nums text-terminal-muted">{row.count}</td>
                  <td className="text-right py-1 pr-1">
                    <span className="text-[9px] px-1 rounded border border-terminal-border text-terminal-muted uppercase tracking-wide">
                      {sourceLabel(t, row.source)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
