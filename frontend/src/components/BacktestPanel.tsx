/**
 * BacktestPanel.tsx — strategy backtester (PLATFORM_ROADMAP.md M5)
 *
 * Replays a daily re-armed buy-entry rule over synthetic GBM history
 * (POST /api/backtest — stateless, seeded/reproducible) and renders the
 * strategy equity curve against a frictionless buy-and-hold baseline, stat
 * cards, and the trade-by-trade blotter. Monte Carlo mode (runs > 1) shows
 * the median run plus a p5/p95 distribution strip. A rule's "test" button
 * (RulesTable) prefills the form via uiStore.
 */
import { useEffect, useRef, useState } from 'react';
import { createChart, BaselineSeries, LineSeries } from 'lightweight-charts';
import type { ISeriesApi, IChartApi, UTCTimestamp } from 'lightweight-charts';
import { formatQuantity } from '@/lib/format';
import { useUiStore } from '@/stores/uiStore';
import { US_PROFILE, directionColors, type MarketProfile } from '@/lib/marketProfile';
import { makeT, langFromLocale, type TFunction } from '@/lib/i18n';
import type {
  BacktestRequest,
  BacktestResponse,
  BacktestPoint,
  BacktestTradeReason,
  RuleTriggerType,
} from '@/types/market';

const TRIGGERS: { key: RuleTriggerType; labelKey: string }[] = [
  { key: 'day_change_pct_below', labelKey: 'backtest.trigDayBelow' },
  { key: 'day_change_pct_above', labelKey: 'backtest.trigDayAbove' },
  { key: 'price_below', labelKey: 'backtest.trigPriceBelow' },
  { key: 'price_above', labelKey: 'backtest.trigPriceAbove' },
];

const RUN_CHOICES = [1, 10, 30] as const;

const REASON_KEY: Record<BacktestTradeReason, string> = {
  trigger: 'backtest.reason.trigger',
  take_profit: 'backtest.reason.take_profit',
  stop_loss: 'backtest.reason.stop_loss',
  horizon_end: 'backtest.reason.horizon_end',
};

const signed = (v: number, digits = 2) => `${v >= 0 ? '+' : ''}${v.toFixed(digits)}`;
const pnlClass = (v: number) => (v >= 0 ? 'text-terminal-up' : 'text-terminal-down');

// Direction fill tints for the equity canvas (lightweight-charts can't read CSS
// vars). Above-baseline uses the "up" tint, below-baseline the "down" tint —
// swapped on the A-share market. Mirrors PnLChart so the two charts agree.
const G28 = 'rgba(34, 197, 94, 0.28)';
const G03 = 'rgba(34, 197, 94, 0.03)';
const R28 = 'rgba(239, 68, 68, 0.28)';
const R03 = 'rgba(239, 68, 68, 0.03)';

interface DirColors {
  upHex: string;
  downHex: string;
  upFill1: string;
  upFill2: string;
  downFill1: string;
  downFill2: string;
}

function equityColors(upIsRed: boolean): DirColors {
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
function EquityChart({
  equity,
  baseline,
  colors,
}: {
  equity: BacktestPoint[];
  baseline: BacktestPoint[];
  colors: DirColors;
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
        // Attribution lives in the README — the logo ghosts over dark charts
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: '#30363d' },
        horzLines: { color: '#30363d' },
      },
      rightPriceScale: { borderColor: '#30363d' },
      timeScale: { borderColor: '#30363d', timeVisible: false },
    });

    // Strategy equity: profit-tint above the $10k seed, loss-tint below. Colours
    // come from the market profile (swapped on A-shares), same as PnLChart.
    const equitySeries = chart.addSeries(BaselineSeries, {
      baseValue: { type: 'price', price: 10000 },
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
    });
  }, [upHex, downHex, upFill1, upFill2, downFill1, downFill2]);

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

function StatCard({
  label,
  value,
  className,
  testid,
}: {
  label: string;
  value: string;
  className?: string;
  testid?: string;
}) {
  return (
    <div className="px-2 py-1.5 rounded border border-terminal-border bg-terminal-bg">
      <div className="text-[10px] font-semibold text-terminal-muted uppercase tracking-wider">
        {label}
      </div>
      <div data-testid={testid} className={`text-sm font-semibold tabular-nums ${className ?? 'text-terminal-text'}`}>
        {value}
      </div>
    </div>
  );
}

export default function BacktestPanel({ profile = US_PROFILE }: { profile?: MarketProfile }) {
  const t: TFunction = makeT(langFromLocale(profile.locale));
  const sym = profile.currency_symbol;
  const chartColors = equityColors(profile.up_is_red);
  const [ticker, setTicker] = useState('AAPL');
  const [triggerType, setTriggerType] = useState<RuleTriggerType>('day_change_pct_below');
  const [threshold, setThreshold] = useState('-2');
  const [qty, setQty] = useState('5');
  const [takeProfit, setTakeProfit] = useState('5');
  const [stopLoss, setStopLoss] = useState('3');
  const [days, setDays] = useState('30');
  const [runs, setRuns] = useState<number>(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BacktestResponse | null>(null);

  const backtestPrefill = useUiStore((s) => s.backtestPrefill);
  const setBacktestPrefill = useUiStore((s) => s.setBacktestPrefill);

  // One-shot handoff from RulesTable's "test" button: apply, then clear.
  useEffect(() => {
    if (!backtestPrefill) return;
    setTicker(backtestPrefill.ticker);
    setTriggerType(backtestPrefill.trigger_type);
    setThreshold(String(backtestPrefill.threshold));
    setQty(String(backtestPrefill.quantity));
    setResult(null);
    setError(null);
    setBacktestPrefill(null);
  }, [backtestPrefill, setBacktestPrefill]);

  const isPriceTrigger = triggerType === 'price_above' || triggerType === 'price_below';

  const run = async () => {
    const normalizedTicker = ticker.trim().toUpperCase();
    const thresholdNum = Number(threshold);
    const qtyNum = Number(qty);
    const daysNum = Number(days);
    const tpNum = takeProfit.trim() === '' ? null : Number(takeProfit);
    const slNum = stopLoss.trim() === '' ? null : Number(stopLoss);

    setError(null);
    if (!normalizedTicker || !/^[A-Z]+$/.test(normalizedTicker)) {
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
    if (!Number.isInteger(daysNum) || daysNum < 5 || daysNum > 120) {
      setError(t('backtest.errDays'));
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

    const body: BacktestRequest = {
      ticker: normalizedTicker,
      trigger_type: triggerType,
      threshold: thresholdNum,
      quantity: qtyNum,
      take_profit_pct: tpNum,
      stop_loss_pct: slNum,
      days: daysNum,
      runs,
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
    } catch (e) {
      setError(e instanceof Error ? e.message : t('backtest.errFailed'));
      setResult(null);
    } finally {
      setLoading(false);
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
            aria-label="Backtest ticker"
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
            aria-label="Trigger type"
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
            aria-label="Threshold"
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
            aria-label="Backtest quantity"
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
            aria-label="Take profit percent"
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
            aria-label="Stop loss percent"
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
            {t('backtest.days')}
          </label>
          <input
            id="bt-days"
            aria-label="Days"
            type="number"
            min="5"
            max="120"
            step="1"
            value={days}
            onChange={(e) => setDays(e.target.value)}
            disabled={loading}
            className={`w-16 ${inputClass}`}
          />
        </div>

        <div className="flex flex-col gap-1 pb-0.5">
          <span className={labelClass}>{t('backtest.runs')}</span>
          <div className="flex gap-1">
            {RUN_CHOICES.map((r) => (
              <button
                key={r}
                type="button"
                data-testid={`backtest-runs-${r}`}
                onClick={() => setRuns(r)}
                disabled={loading}
                className={`px-1.5 py-0.5 rounded text-[10px] font-semibold transition-colors ${
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

      <p className="mt-1.5 text-[10px] text-terminal-muted leading-tight">{t('backtest.helper')}</p>

      {error && (
        <p data-testid="backtest-error" className="mt-1.5 text-xs text-terminal-down leading-tight">
          {error}
        </p>
      )}

      {/* Results */}
      {result && stats && (
        <div data-testid="backtest-stats" className="mt-3">
          {/* Stat cards */}
          <div className="grid grid-cols-4 lg:grid-cols-8 gap-1.5">
            <StatCard
              label={t('backtest.statReturn')}
              value={`${signed(stats.total_return_pct)}%`}
              className={pnlClass(stats.total_return_pct)}
              testid="backtest-return"
            />
            <StatCard
              label={t('backtest.statBuyHold')}
              value={`${signed(stats.buy_hold_return_pct)}%`}
              className={pnlClass(stats.buy_hold_return_pct)}
            />
            <StatCard label={t('backtest.statMaxDd')} value={`−${stats.max_drawdown_pct.toFixed(2)}%`} />
            <StatCard
              label={t('backtest.statWinRate')}
              value={stats.win_rate != null ? `${Math.round(stats.win_rate * 100)}%` : '—'}
            />
            <StatCard label={t('backtest.statEntries')} value={String(stats.fires)} />
            <StatCard label={t('backtest.statRoundTrips')} value={String(stats.round_trips)} />
            <StatCard
              label={t('backtest.statProfitFactor')}
              value={stats.profit_factor != null ? stats.profit_factor.toFixed(2) : '—'}
            />
            <StatCard
              label={t('backtest.statFinalEquity')}
              value={`$${stats.final_equity.toLocaleString('en-US', {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              })}`}
            />
          </div>

          {(stats.rejections.insufficient_cash > 0 || stats.commission_paid > 0) && (
            <p className="mt-1.5 text-[10px] text-terminal-muted">
              {stats.rejections.insufficient_cash > 0 && (
                <span data-testid="backtest-rejections" className="text-terminal-amber mr-3">
                  ⚠ {stats.rejections.insufficient_cash} entr
                  {stats.rejections.insufficient_cash === 1 ? 'y' : 'ies'} skipped — insufficient cash
                </span>
              )}
              {stats.commission_paid > 0 && (
                <span>Commission paid: ${stats.commission_paid.toFixed(2)}</span>
              )}
            </p>
          )}

          {/* Monte Carlo distribution (runs > 1): median run charted below */}
          {summary && (
            <div
              data-testid="backtest-runs-summary"
              className="mt-2 flex items-baseline gap-4 px-2 py-1.5 rounded border border-terminal-border bg-terminal-bg text-xs tabular-nums"
            >
              <span className="text-[10px] font-semibold text-terminal-muted uppercase tracking-wider">
                {summary.runs} runs
              </span>
              <span className="text-terminal-muted">
                Median{' '}
                <span className={pnlClass(summary.median_return_pct)}>
                  {signed(summary.median_return_pct)}%
                </span>
              </span>
              <span className="text-terminal-muted">
                P5 <span className={pnlClass(summary.p05_return_pct)}>{signed(summary.p05_return_pct)}%</span>
              </span>
              <span className="text-terminal-muted">
                P95 <span className={pnlClass(summary.p95_return_pct)}>{signed(summary.p95_return_pct)}%</span>
              </span>
              <span className="text-terminal-muted">
                Positive <span className="text-terminal-text">{Math.round(summary.positive_share * 100)}%</span>
              </span>
              <span className="text-terminal-muted">
                Median DD <span className="text-terminal-text">−{summary.median_max_drawdown_pct.toFixed(2)}%</span>
              </span>
            </div>
          )}

          {/* Equity vs buy & hold */}
          <div className="mt-2">
            <EquityChart
              equity={result.equity_curve}
              baseline={result.baseline_curve}
              colors={chartColors}
            />
          </div>

          {/* Trades blotter */}
          {result.trades.length > 0 && (
            <div className="mt-2 max-h-40 overflow-y-auto">
              <table data-testid="backtest-trades" className="w-full text-xs border-collapse">
                <thead>
                  <tr className="text-terminal-muted border-b border-terminal-border">
                    <th className="text-left py-1 pl-1 font-semibold">{t('backtest.colTime')}</th>
                    <th className="text-left py-1 font-semibold">{t('backtest.colSide')}</th>
                    <th className="text-right py-1 font-semibold">{t('backtest.colQty')}</th>
                    <th className="text-right py-1 font-semibold">{t('backtest.colPrice')}</th>
                    <th className="text-left py-1 pl-3 font-semibold">{t('backtest.colReason')}</th>
                    <th className="text-right py-1 pr-1 font-semibold">{t('backtest.colPnl')}</th>
                  </tr>
                </thead>
                <tbody>
                  {result.trades.map((tr, i) => (
                    <tr key={i} className="border-b border-terminal-border">
                      <td className="py-1 pl-1 tabular-nums text-terminal-muted">
                        {new Date(tr.time * 1000).toLocaleDateString('en-US', {
                          month: 'short',
                          day: 'numeric',
                        })}
                      </td>
                      <td
                        className={`py-1 font-semibold uppercase ${
                          tr.side === 'buy' ? 'text-terminal-up' : 'text-terminal-down'
                        }`}
                      >
                        {tr.side}
                      </td>
                      <td className="text-right py-1 tabular-nums">{formatQuantity(tr.quantity)}</td>
                      <td className="text-right py-1 tabular-nums">${tr.price.toFixed(2)}</td>
                      <td className="py-1 pl-3 text-terminal-muted">{t(REASON_KEY[tr.reason])}</td>
                      <td
                        className={`text-right py-1 pr-1 tabular-nums ${
                          tr.pnl != null ? pnlClass(tr.pnl) : 'text-terminal-muted'
                        }`}
                      >
                        {tr.pnl != null ? `${signed(tr.pnl)}` : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {!result && !error && !loading && (
        <p className="mt-3 text-xs text-terminal-muted">{t('backtest.empty')}</p>
      )}
    </div>
  );
}
