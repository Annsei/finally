/**
 * SourceToggle.tsx — backtest data-source segmented switch (D1 §5).
 *
 * Two segments: Simulated (synthetic GBM, the legacy default) | History (real
 * daily bars from the user-synced daily_bars store). Pure controlled control —
 * the owner decides what switching implies (forcing runs to 1, relabelling the
 * days field, adding `source: "history"` to the submit payload).
 *
 * Testids (contract §5, E2E-pinned): the group carries the given `testid`
 * (`backtest-source` on the Backtest tab, `strategy-bt-source` on the strategy
 * detail launcher) and each segment `${testid}-synthetic` / `${testid}-history`.
 * Styling mirrors the Runs segmented buttons in BacktestPanel.
 */
import type { TFunction } from '@/lib/i18n';
import type { BacktestSource } from '@/types/market';

export type { BacktestSource } from '@/types/market';

const OPTIONS: { key: BacktestSource; labelKey: string }[] = [
  { key: 'synthetic', labelKey: 'backtest.sourceSynthetic' },
  { key: 'history', labelKey: 'backtest.sourceHistory' },
];

export default function SourceToggle({
  testid,
  value,
  onChange,
  disabled = false,
  t,
}: {
  testid: string;
  value: BacktestSource;
  onChange: (source: BacktestSource) => void;
  disabled?: boolean;
  t: TFunction;
}) {
  return (
    <div className="flex flex-col gap-1 pb-0.5">
      <span className="text-xs font-semibold text-terminal-muted uppercase tracking-wider">
        {t('backtest.source')}
      </span>
      <div
        data-testid={testid}
        role="group"
        aria-label={t('backtest.source')}
        className="flex gap-1"
      >
        {OPTIONS.map((opt) => (
          <button
            key={opt.key}
            type="button"
            data-testid={`${testid}-${opt.key}`}
            aria-pressed={value === opt.key}
            onClick={() => onChange(opt.key)}
            disabled={disabled}
            className={`px-1.5 py-0.5 rounded text-[10px] font-semibold transition-colors disabled:opacity-50 ${
              value === opt.key
                ? 'bg-terminal-bg text-terminal-text border border-terminal-blue'
                : 'text-terminal-muted border border-terminal-border hover:text-terminal-text'
            }`}
          >
            {t(opt.labelKey)}
          </button>
        ))}
      </div>
    </div>
  );
}
