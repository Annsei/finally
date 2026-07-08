/**
 * StatCard.tsx — single backtest stat tile (P2 §8, extracted verbatim from
 * BacktestPanel as a pure refactor: DOM and testids unchanged).
 *
 * Also exports the shared P&L formatting helpers (`signed`, `pnlClass`) used
 * by StatsGrid / RunsSummaryStrip / TradesBlotter so the direction-colour
 * class logic lives in one place.
 */

export const signed = (v: number, digits = 2) => `${v >= 0 ? '+' : ''}${v.toFixed(digits)}`;
export const pnlClass = (v: number) => (v >= 0 ? 'text-terminal-up' : 'text-terminal-down');

export default function StatCard({
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
