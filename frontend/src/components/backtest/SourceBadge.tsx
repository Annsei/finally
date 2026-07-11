/**
 * SourceBadge.tsx — data-source chip for backtest results and stored runs
 * (D1 §5): sample / yfinance / akshare / synthetic (i18n), plus the evaluated
 * `date_range` for history-mode results.
 *
 * `runSourceKind` normalizes whatever marker a stored config or run-list item
 * carries: pre-D1 payloads have no `source` at all, and strategy-shaped
 * configs use `source: "strategy"` as an engine discriminator (backtest.py) —
 * both read as the legacy synthetic path unless a `date_range` proves the run
 * evaluated real daily bars.
 */
import type { TFunction } from '@/lib/i18n';
import type { BacktestDateRange } from '@/types/market';

// Marker values with dedicated i18n labels; anything else renders verbatim.
const KNOWN_KINDS = new Set(['synthetic', 'history', 'sample', 'yfinance', 'akshare']);

/** Anything carrying optional source markers: config objects or run list items. */
export interface SourceCarrier {
  source?: unknown;
  data_source?: unknown;
  date_range?: unknown;
}

/**
 * Resolve the badge kind for a run config / list item. Precedence: an explicit
 * provider/data-source string (skipping the strategy-config discriminator
 * `"strategy"`), then `date_range` presence (history run whose provider is
 * unknown), then the synthetic default.
 */
export function runSourceKind(carrier: SourceCarrier | null | undefined): string {
  if (!carrier || typeof carrier !== 'object') return 'synthetic';
  for (const raw of [carrier.data_source, carrier.source]) {
    if (typeof raw === 'string' && raw.trim() !== '' && raw !== 'strategy') return raw;
  }
  return carrier.date_range != null ? 'history' : 'synthetic';
}

/** Validated {from, to} out of a stored config / list item, else null. */
export function runDateRange(carrier: SourceCarrier | null | undefined): BacktestDateRange | null {
  const raw = carrier?.date_range;
  if (
    raw != null &&
    typeof raw === 'object' &&
    typeof (raw as BacktestDateRange).from === 'string' &&
    typeof (raw as BacktestDateRange).to === 'string'
  ) {
    return raw as BacktestDateRange;
  }
  return null;
}

/** i18n label for a badge kind; unknown markers degrade to the raw string. */
export function sourceLabel(t: TFunction, kind: string): string {
  return KNOWN_KINDS.has(kind) ? t(`history.source.${kind}`) : kind;
}

export default function SourceBadge({
  testid,
  source,
  dateRange = null,
  t,
}: {
  testid: string;
  source: string;
  dateRange?: BacktestDateRange | null;
  t: TFunction;
}) {
  const isSynthetic = source === 'synthetic';
  return (
    <span
      data-testid={testid}
      data-source={source}
      className={`inline-flex items-center gap-1 text-[9px] font-semibold px-1 py-0.5 rounded border uppercase tracking-wider ${
        isSynthetic
          ? 'text-terminal-muted border-terminal-border'
          : 'text-terminal-blue border-terminal-blue/60'
      }`}
    >
      {sourceLabel(t, source)}
      {dateRange && (
        <span className="font-normal normal-case tracking-normal tabular-nums text-terminal-muted">
          {dateRange.from} → {dateRange.to}
        </span>
      )}
    </span>
  );
}
