/**
 * BacktestPanel.tsx — strategy backtester (PLATFORM_ROADMAP.md M5)
 *
 * Replays a daily re-armed buy-entry rule over synthetic GBM history
 * (POST /api/backtest — stateless, seeded/reproducible) and renders the
 * strategy equity curve against a frictionless buy-and-hold baseline, stat
 * cards, and the trade-by-trade blotter. Monte Carlo mode (runs > 1) shows
 * the median run plus a p5/p95 distribution strip. A rule's "test" button
 * (RulesTable) prefills the form via uiStore.
 *
 * P2 §8: the result-rendering pieces (EquityChart, StatCard, StatsGrid,
 * RunsSummaryStrip, TradesBlotter) live in components/backtest/ so the
 * /run and /strategy pages can assemble the same DOM — this panel is now
 * pure composition over those parts (testids and markup unchanged).
 *
 * D1 §5 (additive): a `backtest-source` segmented switch selects Simulated
 * (legacy payload, `source` omitted) vs History (real daily bars — runs pinned
 * to 1, days = trading days 20..750, `source: "history"` in the payload). The
 * result block leads with a `backtest-source-badge` chip echoing config.source
 * and, for history runs, config.date_range.
 */
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useUiStore } from '@/stores/uiStore';
import { US_PROFILE, type MarketProfile } from '@/lib/marketProfile';
import { makeT, langFromLocale, type TFunction } from '@/lib/i18n';
import { formatMoney } from '@/lib/format';
import EquityChart, { equityColors } from '@/components/backtest/EquityChart';
import StatsGrid from '@/components/backtest/StatsGrid';
import RunsSummaryStrip from '@/components/backtest/RunsSummaryStrip';
import TradesBlotter from '@/components/backtest/TradesBlotter';
import SourceToggle from '@/components/backtest/SourceToggle';
import SourceBadge, { runSourceKind, runDateRange } from '@/components/backtest/SourceBadge';
import type {
  BacktestRequest,
  BacktestResponse,
  BacktestSource,
  RuleTriggerType,
} from '@/types/market';

const TRIGGERS: { key: RuleTriggerType; labelKey: string }[] = [
  { key: 'day_change_pct_below', labelKey: 'backtest.trigDayBelow' },
  { key: 'day_change_pct_above', labelKey: 'backtest.trigDayAbove' },
  { key: 'price_below', labelKey: 'backtest.trigPriceBelow' },
  { key: 'price_above', labelKey: 'backtest.trigPriceAbove' },
];

const RUN_CHOICES = [1, 10, 30] as const;

export default function BacktestPanel({ profile = US_PROFILE }: { profile?: MarketProfile }) {
  const t: TFunction = makeT(langFromLocale(profile.locale));
  const sym = profile.currency_symbol;
  const chartColors = equityColors(profile.up_is_red);
  // RulesTable activates this tab after writing a one-shot prefill. Capture the
  // handoff in the state initializers so the form mounts ready to use without a
  // second, effect-driven render.
  const [initialPrefill] = useState(() => useUiStore.getState().backtestPrefill);
  const defaultTicker = Object.keys(profile.names)[0] ?? 'AAPL';
  const [ticker, setTicker] = useState(initialPrefill?.ticker ?? defaultTicker);
  const [triggerType, setTriggerType] = useState<RuleTriggerType>(
    initialPrefill?.trigger_type ?? 'day_change_pct_below'
  );
  const [threshold, setThreshold] = useState(String(initialPrefill?.threshold ?? -2));
  const [qty, setQty] = useState(
    String(initialPrefill?.quantity ?? (profile.lot_size > 1 ? profile.lot_size : 5))
  );
  const [takeProfit, setTakeProfit] = useState('5');
  const [stopLoss, setStopLoss] = useState('3');
  const [days, setDays] = useState('30');
  const [runs, setRuns] = useState<number>(1);
  // D1 §5 — data-source switch. 'synthetic' keeps the legacy payload
  // byte-identical (no `source` field); 'history' forces runs to 1 and
  // reinterprets `days` as trading days (20..750).
  const [source, setSource] = useState<BacktestSource>('synthetic');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BacktestResponse | null>(null);
  // P2 §8 — save the rendered result to the Run Library (POST /api/backtest/runs
  // with the legacy field set incl. the echoed seed; the server re-runs the
  // same config+seed and persists it).
  const [saveLabel, setSaveLabel] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const setBacktestPrefill = useUiStore((s) => s.setBacktestPrefill);

  // The handoff is consumed by the initializers above; only the external store
  // cleanup belongs in an effect.
  useEffect(() => {
    if (!initialPrefill) return;
    setBacktestPrefill(null);
  }, [initialPrefill, setBacktestPrefill]);

  const isPriceTrigger = triggerType === 'price_above' || triggerType === 'price_below';
  const isHistory = source === 'history';

  // History runs are fully deterministic — no Monte Carlo (contract §3, runs
  // must be 1), so switching pins the runs selector to 1 and disables it.
  const selectSource = (next: BacktestSource) => {
    setSource(next);
    if (next === 'history') setRuns(1);
  };

  const run = async () => {
    const normalizedTicker = ticker.trim().toUpperCase();
    const thresholdNum = Number(threshold);
    const qtyNum = Number(qty);
    const daysNum = Number(days);
    const tpNum = takeProfit.trim() === '' ? null : Number(takeProfit);
    const slNum = stopLoss.trim() === '' ? null : Number(stopLoss);

    setError(null);
    const validTicker =
      profile.market === 'cn'
        ? /^\d{6}$/.test(normalizedTicker)
        : /^[A-Z]{1,10}$/.test(normalizedTicker);
    if (!validTicker) {
      setError(t('backtest.errTicker'));
      return;
    }
    if (!isFinite(thresholdNum) || (isPriceTrigger && thresholdNum <= 0)) {
      setError(isPriceTrigger ? t('backtest.errThresholdPrice') : t('backtest.errThreshold'));
      return;
    }
    if (!isFinite(qtyNum) || qtyNum <= 0) {
      setError(t('backtest.errQty'));
      return;
    }
    if (
      profile.lot_size > 1 &&
      (!Number.isInteger(qtyNum) || qtyNum % profile.lot_size !== 0)
    ) {
      setError(t('backtest.errWholeLot', { lot: profile.lot_size }));
      return;
    }
    // History mode reads `days` as trading days (contract §3: 20..750).
    const minDays = isHistory ? 20 : 5;
    const maxDays = isHistory ? 750 : 120;
    if (!Number.isInteger(daysNum) || daysNum < minDays || daysNum > maxDays) {
      setError(t(isHistory ? 'backtest.errDaysHistory' : 'backtest.errDays'));
      return;
    }
    if (tpNum != null && (!isFinite(tpNum) || tpNum <= 0)) {
      setError(t('backtest.errTp'));
      return;
    }
    if (slNum != null && (!isFinite(slNum) || slNum <= 0)) {
      setError(t('backtest.errSl'));
      return;
    }

    // `source` is omitted on the synthetic path so the legacy payload stays
    // byte-identical (contract §3: absent source = current synthetic path).
    const body: BacktestRequest = {
      ticker: normalizedTicker,
      trigger_type: triggerType,
      threshold: thresholdNum,
      quantity: qtyNum,
      take_profit_pct: tpNum,
      stop_loss_pct: slNum,
      days: daysNum,
      runs: isHistory ? 1 : runs,
      ...(isHistory ? { source: 'history' as const } : {}),
    };

    setLoading(true);
    try {
      const res = await fetch('/api/backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data?.error ?? `Backtest failed (${res.status})`);
      }
      setResult((await res.json()) as BacktestResponse);
      // Fresh result → the save affordance re-arms.
      setSaved(false);
      setSaveError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : t('backtest.errFailed'));
      setResult(null);
    } finally {
      setLoading(false);
    }
  };

  // Persist the rendered result: legacy fields in full — incl. the seed the
  // server echoed — so the stored run is byte-reproducible (contract §5).
  // History results re-run against the same daily bars instead (D1 §3:
  // deterministic, seed echoed as null) — the save passes `source` through.
  const save = async () => {
    if (!result || saving || saved) return;
    const cfg = result.config;
    setSaving(true);
    setSaveError(null);
    try {
      const res = await fetch('/api/backtest/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ticker: cfg.ticker,
          trigger_type: cfg.trigger_type,
          threshold: cfg.threshold,
          quantity: cfg.quantity,
          take_profit_pct: cfg.take_profit_pct,
          stop_loss_pct: cfg.stop_loss_pct,
          days: cfg.days,
          runs: cfg.runs,
          seed: cfg.seed,
          ...(runSourceKind(cfg) !== 'synthetic' ? { source: 'history' as const } : {}),
          ...(saveLabel.trim() !== '' ? { label: saveLabel.trim() } : {}),
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data?.error ?? `${t('runs.saveFailed')} (${res.status})`);
      }
      setSaved(true);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : t('runs.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const inputClass =
    'px-2 py-1.5 text-xs font-mono bg-terminal-bg border border-terminal-border text-terminal-text rounded focus:outline-none focus:border-terminal-blue tabular-nums placeholder:text-terminal-muted disabled:opacity-50';
  const labelClass = 'text-xs font-semibold text-terminal-muted uppercase tracking-wider';

  const stats = result?.stats;
  const summary = result?.runs_summary;

  return (
    <div className="p-3">
      {/* Config form — buy-entry strategy; exits via TP/SL */}
      <div className="flex items-end gap-2 flex-wrap">
        <div className="flex flex-col gap-1">
          <label htmlFor="bt-ticker" className={labelClass}>
            {t('tradebar.ticker')}
          </label>
          <input
            id="bt-ticker"
            aria-label={t('backtest.ariaTicker')}
            type="text"
            list="ticker-suggestions"
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            disabled={loading}
            className={`w-20 ${inputClass}`}
          />
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="bt-trigger" className={labelClass}>
            {t('backtest.buyWhen')}
          </label>
          <select
            id="bt-trigger"
            aria-label={t('backtest.ariaTrigger')}
            data-testid="backtest-trigger"
            value={triggerType}
            onChange={(e) => setTriggerType(e.target.value as RuleTriggerType)}
            disabled={loading}
            className={inputClass}
          >
            {TRIGGERS.map((tr) => (
              <option key={tr.key} value={tr.key}>
                {t(tr.labelKey, { sym })}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="bt-threshold" className={labelClass}>
            {isPriceTrigger ? t('backtest.priceLabel', { sym }) : t('backtest.dayPct')}
          </label>
          <input
            id="bt-threshold"
            aria-label={t('backtest.ariaThreshold')}
            type="number"
            step="any"
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
            disabled={loading}
            className={`w-20 ${inputClass}`}
          />
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="bt-qty" className={labelClass}>
            {t('backtest.qty')}
          </label>
          <input
            id="bt-qty"
            aria-label={t('backtest.ariaQty')}
            type="number"
            min="0"
            step="any"
            value={qty}
            onChange={(e) => setQty(e.target.value)}
            disabled={loading}
            className={`w-16 ${inputClass}`}
          />
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="bt-tp" className={labelClass}>
            {t('backtest.tp')}
          </label>
          <input
            id="bt-tp"
            aria-label={t('backtest.ariaTp')}
            type="number"
            min="0"
            step="any"
            placeholder="—"
            value={takeProfit}
            onChange={(e) => setTakeProfit(e.target.value)}
            disabled={loading}
            className={`w-16 ${inputClass}`}
          />
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="bt-sl" className={labelClass}>
            {t('backtest.sl')}
          </label>
          <input
            id="bt-sl"
            aria-label={t('backtest.ariaSl')}
            type="number"
            min="0"
            step="any"
            placeholder="—"
            value={stopLoss}
            onChange={(e) => setStopLoss(e.target.value)}
            disabled={loading}
            className={`w-16 ${inputClass}`}
          />
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="bt-days" className={labelClass}>
            {isHistory ? t('backtest.tradingDays') : t('backtest.days')}
          </label>
          <input
            id="bt-days"
            aria-label={t('backtest.ariaDays')}
            type="number"
            min={isHistory ? 20 : 5}
            max={isHistory ? 750 : 120}
            step="1"
            value={days}
            onChange={(e) => setDays(e.target.value)}
            disabled={loading}
            className={`w-16 ${inputClass}`}
          />
        </div>

        {/* D1 §5 — data-source switch (additive; the form above is untouched) */}
        <SourceToggle
          testid="backtest-source"
          value={source}
          onChange={selectSource}
          disabled={loading}
          t={t}
        />

        <div className="flex flex-col gap-1 pb-0.5">
          <span className={labelClass}>{t('backtest.runs')}</span>
          <div className="flex gap-1">
            {RUN_CHOICES.map((r) => (
              <button
                key={r}
                type="button"
                data-testid={`backtest-runs-${r}`}
                onClick={() => setRuns(r)}
                disabled={loading || isHistory}
                className={`px-1.5 py-0.5 rounded text-[10px] font-semibold transition-colors${isHistory ? ' disabled:opacity-50' : ''} ${
                  runs === r
                    ? 'bg-terminal-bg text-terminal-text border border-terminal-blue'
                    : 'text-terminal-muted border border-terminal-border hover:text-terminal-text'
                }`}
              >
                {r}×
              </button>
            ))}
          </div>
        </div>

        <button
          type="button"
          data-testid="backtest-run"
          onClick={() => void run()}
          disabled={loading}
          className="px-4 py-1.5 text-xs font-semibold rounded min-h-[36px] text-white disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
          style={{ backgroundColor: '#753991' }}
        >
          {loading ? t('backtest.running') : t('backtest.run')}
        </button>
      </div>

      <p className="mt-1.5 text-[10px] text-terminal-muted leading-tight">
        {isHistory ? t('backtest.helperHistory') : t('backtest.helper')}
      </p>

      {error && (
        <p data-testid="backtest-error" className="mt-1.5 text-xs text-terminal-down leading-tight">
          {error}
        </p>
      )}

      {/* Results */}
      {result && stats && (
        <div data-testid="backtest-stats" className="mt-3">
          {/* Data-source badge + evaluated date range (D1 §5, additive) */}
          <div className="mb-1.5">
            <SourceBadge
              testid="backtest-source-badge"
              source={runSourceKind(result.config)}
              dateRange={runDateRange(result.config)}
              t={t}
            />
          </div>

          {/* Stat cards */}
          <StatsGrid
            stats={stats}
            t={t}
            currencySymbol={profile.currency_symbol}
            locale={profile.locale}
          />

          {(stats.rejections.insufficient_cash > 0 || stats.commission_paid > 0) && (
            <p className="mt-1.5 text-[10px] text-terminal-muted">
              {stats.rejections.insufficient_cash > 0 && (
                <span data-testid="backtest-rejections" className="text-terminal-amber mr-3">
                  ⚠ {t('backtest.insufficientCash', { n: stats.rejections.insufficient_cash })}
                </span>
              )}
              {stats.commission_paid > 0 && (
                <span>
                  {t('backtest.commissionPaid', {
                    amount: formatMoney(stats.commission_paid, {
                      currency_symbol: profile.currency_symbol,
                      locale: profile.locale,
                    }),
                  })}
                </span>
              )}
            </p>
          )}

          {/* Monte Carlo distribution (runs > 1): median run charted below */}
          {summary && <RunsSummaryStrip summary={summary} t={t} />}

          {/* Equity vs buy & hold */}
          <div className="mt-2">
            <EquityChart
              equity={result.equity_curve}
              baseline={result.baseline_curve}
              colors={chartColors}
              baseValue={profile.seed_cash}
            />
          </div>

          {/* Trades blotter */}
          {result.trades.length > 0 && (
            <TradesBlotter
              trades={result.trades}
              t={t}
              currencySymbol={profile.currency_symbol}
              locale={profile.locale}
              lotSize={profile.lot_size}
            />
          )}

          {/* Save to Run Library (P2 §8) — appended below the existing result
              DOM so the panel's original markup stays untouched. */}
          <div className="mt-2 flex items-center gap-2 flex-wrap">
            <input
              type="text"
              data-testid="backtest-save-label"
              aria-label={t('runs.colLabel')}
              placeholder={t('runs.saveLabelPlaceholder')}
              value={saveLabel}
              onChange={(e) => setSaveLabel(e.target.value)}
              disabled={saving}
              className={`w-40 ${inputClass}`}
            />
            <button
              type="button"
              data-testid="backtest-save"
              onClick={() => void save()}
              disabled={saving || saved}
              className="px-3 py-1.5 text-xs font-semibold rounded text-white disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
              style={{ backgroundColor: '#753991' }}
            >
              {saving ? t('runs.saving') : t('runs.save')}
            </button>
            {saved && (
              <Link
                href="/runs"
                data-testid="backtest-save-toast"
                className="text-xs text-terminal-up hover:underline"
              >
                {t('runs.saved')}
              </Link>
            )}
            {saveError && (
              <span data-testid="backtest-save-error" className="text-xs text-terminal-down">
                {saveError}
              </span>
            )}
          </div>
        </div>
      )}

      {!result && !error && !loading && (
        <p className="mt-3 text-xs text-terminal-muted">{t('backtest.empty')}</p>
      )}
    </div>
  );
}
