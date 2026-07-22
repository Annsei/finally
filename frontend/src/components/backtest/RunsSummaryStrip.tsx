/**
 * RunsSummaryStrip.tsx — Monte Carlo (runs > 1) distribution strip (P2 §8,
 * extracted verbatim from BacktestPanel as a pure refactor: DOM and testids
 * unchanged). The caller decides whether a summary exists before mounting.
 */
import type { TFunction } from '@/lib/i18n';
import type { BacktestRunsSummary } from '@/types/market';
import { signed, pnlClass } from '@/components/backtest/StatCard';

export default function RunsSummaryStrip({
  summary,
  t,
}: {
  summary: BacktestRunsSummary;
  t: TFunction;
}) {
  return (
    <div
      data-testid="backtest-runs-summary"
      className="mt-2 flex items-baseline gap-4 px-2 py-1.5 rounded border border-terminal-border bg-terminal-bg text-xs tabular-nums"
    >
      <span className="text-[10px] font-semibold text-terminal-muted uppercase tracking-wider">
        {t('backtest.summaryRuns', { n: summary.runs })}
      </span>
      <span className="text-terminal-muted">
        {t('backtest.summaryMedian')}{' '}
        <span className={pnlClass(summary.median_return_pct)}>
          {signed(summary.median_return_pct)}%
        </span>
      </span>
      <span className="text-terminal-muted">
        {t('backtest.summaryP5')}{' '}
        <span className={pnlClass(summary.p05_return_pct)}>{signed(summary.p05_return_pct)}%</span>
      </span>
      <span className="text-terminal-muted">
        {t('backtest.summaryP95')}{' '}
        <span className={pnlClass(summary.p95_return_pct)}>{signed(summary.p95_return_pct)}%</span>
      </span>
      <span className="text-terminal-muted">
        {t('backtest.summaryPositive')}{' '}
        <span className="text-terminal-text">{Math.round(summary.positive_share * 100)}%</span>
      </span>
      <span className="text-terminal-muted">
        {t('backtest.summaryMedianDd')}{' '}
        <span className="text-terminal-text">−{summary.median_max_drawdown_pct.toFixed(2)}%</span>
      </span>
    </div>
  );
}
