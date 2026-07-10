/**
 * RulesTable.tsx — standing rules panel (PLATFORM_ROADMAP.md M2.2)
 *
 * Rules are one-shot AI-authored automations ("buy 5 NVDA if it drops 3%
 * today"). The backend evaluator fires them against live quotes; fired rules
 * can be re-armed. Polls every 5s so background firings surface on their own.
 */
import { useState } from 'react';
import useSWR from 'swr';
import { fetcher } from '@/lib/fetcher';
import { formatMoney, formatShares } from '@/lib/format';
import { useMarketProfile, type MarketProfile } from '@/lib/marketProfile';
import SymbolLink from '@/components/SymbolLink';
import { useUiStore } from '@/stores/uiStore';
import { useT, type TFunction } from '@/lib/i18n';
import type { RulesResponse, TradingRule, RuleStatus } from '@/types/market';

const STATUS_STYLE: Record<RuleStatus, string> = {
  active: 'text-terminal-up border-terminal-up/60',
  paused: 'text-terminal-muted border-terminal-border',
  fired: 'text-terminal-blue border-terminal-blue/60',
};

function conditionText(r: TradingRule, t: TFunction, profile: MarketProfile): string {
  switch (r.trigger_type) {
    case 'price_above':
      return t('rules.priceAbove', { value: formatMoney(r.threshold, profile) });
    case 'price_below':
      return t('rules.priceBelow', { value: formatMoney(r.threshold, profile) });
    case 'day_change_pct_above':
      return t('rules.dayAbove', { value: `${r.threshold > 0 ? '+' : ''}${r.threshold}%` });
    case 'day_change_pct_below':
      return t('rules.dayBelow', { value: `${r.threshold > 0 ? '+' : ''}${r.threshold}%` });
  }
}

export default function RulesTable() {
  const t = useT();
  const profile = useMarketProfile();
  const { data, mutate } = useSWR<RulesResponse>('/api/rules', fetcher, {
    refreshInterval: 5000,
  });
  const [actionError, setActionError] = useState<string | null>(null);
  const rules = data?.rules;
  const setPortfolioTab = useUiStore((s) => s.setPortfolioTab);
  const setBacktestPrefill = useUiStore((s) => s.setBacktestPrefill);

  // M5: hand the rule's config to the Backtest tab (buy-entry rules only —
  // the backtester models exits with TP/SL, not standalone sell triggers).
  const backtest = (rule: TradingRule) => {
    setBacktestPrefill({
      ticker: rule.ticker,
      trigger_type: rule.trigger_type,
      threshold: rule.threshold,
      quantity: rule.quantity,
    });
    setPortfolioTab('backtest');
  };

  const patchStatus = async (rule: TradingRule) => {
    setActionError(null);
    // active → paused; paused/fired → active (re-arm)
    const next = rule.status === 'active' ? 'paused' : 'active';
    try {
      const res = await fetch(`/api/rules/${encodeURIComponent(rule.id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: next }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error ?? `Update failed (${res.status})`);
      }
      await mutate();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Update failed');
      await mutate();
    }
  };

  const remove = async (rule: TradingRule) => {
    setActionError(null);
    try {
      const res = await fetch(`/api/rules/${encodeURIComponent(rule.id)}`, {
        method: 'DELETE',
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error ?? `Delete failed (${res.status})`);
      }
      await mutate();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Delete failed');
      await mutate();
    }
  };

  if (!rules || rules.length === 0) {
    return (
      <div className="p-4 text-terminal-muted text-xs">
        {t('rules.empty')}
        {actionError && (
          <p data-testid="rules-error" className="mt-1 text-terminal-down">
            {actionError}
          </p>
        )}
      </div>
    );
  }

  return (
    <div>
      <table data-testid="rules-table" className="w-full text-xs border-collapse">
        <thead>
          <tr className="text-terminal-muted border-b border-terminal-border">
            <th className="text-left py-1 pl-1 font-semibold">{t('rules.colRule')}</th>
            <th className="text-left py-1 font-semibold">{t('rules.colCondition')}</th>
            <th className="text-left py-1 font-semibold">{t('rules.colAction')}</th>
            <th className="text-left py-1 font-semibold">{t('rules.colStatus')}</th>
            <th className="text-right py-1 font-semibold">{t('rules.colFired')}</th>
            <th className="text-right py-1 pr-1 font-semibold" aria-label={t('rules.controls')} />
          </tr>
        </thead>
        <tbody>
          {rules.map((r) => (
            <tr
              key={r.id}
              data-testid={`rule-row-${r.id}`}
              className="border-b border-terminal-border hover:bg-terminal-surface/50"
            >
              <td className="py-1 pl-1 text-terminal-text max-w-[260px] truncate" title={r.description}>
                {r.description}
              </td>
              <td className="py-1 tabular-nums text-terminal-muted">
                <SymbolLink code={r.ticker} className="font-semibold text-terminal-text" />{' '}
                {conditionText(r, t, profile)}
              </td>
              <td
                className={`py-1 tabular-nums font-semibold uppercase ${
                  r.side === 'buy' ? 'text-terminal-up' : 'text-terminal-down'
                }`}
              >
                {r.side === 'buy' ? t('analytics.buy') : t('analytics.sell')}{' '}
                {formatShares(r.quantity, profile)}
              </td>
              <td className="py-1">
                <span
                  data-testid={`rule-status-${r.id}`}
                  className={`px-1.5 py-0.5 rounded border text-[10px] font-semibold uppercase ${STATUS_STYLE[r.status]}`}
                >
                  {t(`rules.status.${r.status}`)}
                </span>
              </td>
              <td className="text-right py-1 tabular-nums text-terminal-muted">{r.fire_count}</td>
              <td className="text-right py-1 pr-1 whitespace-nowrap">
                {r.side === 'buy' && (
                  <button
                    type="button"
                    data-testid={`rule-backtest-${r.id}`}
                    title={t('rules.testTitle')}
                    onClick={() => backtest(r)}
                    className="text-terminal-muted hover:text-terminal-accent text-[10px] font-semibold uppercase px-1"
                  >
                    {t('rules.test')}
                  </button>
                )}
                <button
                  type="button"
                  data-testid={`rule-toggle-${r.id}`}
                  title={r.status === 'active' ? t('rules.pauseTitle') : t('rules.armTitle')}
                  onClick={() => void patchStatus(r)}
                  className="text-terminal-muted hover:text-terminal-blue text-[10px] font-semibold uppercase px-1"
                >
                  {r.status === 'active' ? t('rules.pause') : t('rules.arm')}
                </button>
                <button
                  type="button"
                  data-testid={`rule-delete-${r.id}`}
                  aria-label={t('rules.deleteAria', { ticker: r.ticker })}
                  title={t('rules.deleteTitle')}
                  onClick={() => void remove(r)}
                  className="text-terminal-muted hover:text-terminal-down text-sm leading-none px-1"
                >
                  ×
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {actionError && (
        <p data-testid="rules-error" className="p-2 text-xs text-terminal-down">
          {actionError}
        </p>
      )}
    </div>
  );
}
