/**
 * MarketCorrelation.tsx — /market NxN correlation heatmap (P4 §2).
 *
 * COLOURS ARE CONTRACT-PINNED and deliberately NOT the up/down direction
 * pair: correlation has no gain/loss semantics, so the CN red-up flip must
 * not apply. Positive r → blue #209dd7, negative r → purple #753991, mixed
 * toward transparent at |r| intensity; diagonal cells are muted.
 *
 * Tickers arrive pre-sorted by sector (backend groups them so sector blocks
 * read as visible squares); sector boundaries get an accent divider and a
 * legend line lists the groups in order.
 *
 * Data: GET /api/market/correlation?minutes=30, SWR 30s.
 */
import { Fragment } from 'react';
import useSWR from 'swr';
import { fetcher } from '@/lib/fetcher';
import { useT } from '@/lib/i18n';
import type { MarketCorrelationResponse } from '@/types/market';

// Contract §2: positive blue / negative purple — intentionally hardcoded,
// these are NOT direction colours.
export const CORR_POS = '#209dd7';
export const CORR_NEG = '#753991';
export const CORR_DIAG = '#30363d'; // muted (terminal border grey)

/** Cell intensity: |r| clamped to [0,1], as a 0..100 color-mix percentage. */
export function corrIntensity(r: number | undefined | null): number {
  if (r == null || !Number.isFinite(r)) return 0;
  return Math.round(Math.min(Math.abs(r), 1) * 100);
}

/** Cell background: blue for r ≥ 0, purple for r < 0, at |r| intensity. */
export function corrColor(r: number | undefined | null): string {
  const pct = corrIntensity(r);
  if (pct === 0) return 'transparent';
  return `color-mix(in srgb, ${(r as number) >= 0 ? CORR_POS : CORR_NEG} ${pct}%, transparent)`;
}

/**
 * True at index i when tickers[i] starts a new sector group (i > 0). Used for
 * the accent divider between sector blocks.
 */
export function sectorBoundaries(
  tickers: string[],
  sectors: Record<string, string>
): boolean[] {
  return tickers.map(
    (tk, i) => i > 0 && (sectors[tk] ?? 'other') !== (sectors[tickers[i - 1]] ?? 'other')
  );
}

/** Ordered unique sector names, following the ticker order. */
export function sectorLegend(tickers: string[], sectors: Record<string, string>): string[] {
  const out: string[] = [];
  for (const tk of tickers) {
    const s = sectors[tk] ?? 'other';
    if (out[out.length - 1] !== s) out.push(s);
  }
  return out;
}

export default function MarketCorrelation() {
  const t = useT();
  const { data } = useSWR<MarketCorrelationResponse>('/api/market/correlation?minutes=30', fetcher, {
    refreshInterval: 30_000,
  });

  const tickers = data?.tickers ?? [];
  if (!data || tickers.length === 0) {
    return (
      <div data-testid="market-correlation" className="p-2 text-xs text-terminal-muted">
        {t('market.corrEmpty')}
      </div>
    );
  }

  const sectors = data.sectors ?? {};
  const boundary = sectorBoundaries(tickers, sectors);
  const legend = sectorLegend(tickers, sectors);

  return (
    <div data-testid="market-correlation" className="p-2 min-w-0">
      {/* Sector group legend (ticker order) */}
      <div className="flex flex-wrap gap-1 mb-1">
        {legend.map((s) => (
          <span
            key={s}
            data-testid={`market-corr-sector-${s}`}
            className="text-[9px] px-1 rounded border border-terminal-border text-terminal-muted uppercase tracking-wide"
          >
            {s}
          </span>
        ))}
      </div>

      <div className="overflow-auto">
        <div
          className="grid gap-px"
          style={{ gridTemplateColumns: `auto repeat(${tickers.length}, minmax(0.75rem, 1fr))` }}
        >
          {/* corner + column heads */}
          <span />
          {tickers.map((tk, i) => (
            <span
              key={`col-${tk}`}
              className={`text-[8px] leading-3 text-terminal-muted text-center truncate ${
                boundary[i] ? 'border-l border-terminal-accent/60' : ''
              }`}
            >
              {tk}
            </span>
          ))}

          {/* one row per ticker */}
          {tickers.map((a, i) => (
            <Fragment key={`row-${a}`}>
              <span
                className={`text-[8px] leading-3 text-terminal-muted text-right pr-1 truncate ${
                  boundary[i] ? 'border-t border-terminal-accent/60' : ''
                }`}
              >
                {a}
              </span>
              {tickers.map((b, j) => {
                const r = data.matrix?.[i]?.[j] ?? 0;
                const diag = i === j;
                return (
                  <span
                    key={`${a}-${b}`}
                    data-testid={`market-corr-${a}-${b}`}
                    data-corr={r.toFixed(2)}
                    data-polarity={diag ? 'diag' : r >= 0 ? 'pos' : 'neg'}
                    title={`${a}×${b} r=${r.toFixed(2)}`}
                    className={`h-3 min-w-3 rounded-[1px] ${
                      boundary[i] ? 'border-t border-terminal-accent/60' : ''
                    } ${boundary[j] ? 'border-l border-terminal-accent/60' : ''}`}
                    style={{ background: diag ? CORR_DIAG : corrColor(r) }}
                  />
                );
              })}
            </Fragment>
          ))}
        </div>
      </div>
    </div>
  );
}
