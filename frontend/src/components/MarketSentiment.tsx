/**
 * MarketSentiment.tsx — /market sentiment gauge (P4 §1).
 *
 * Pure DOM (no canvas): a horizontal five-segment band with a score marker,
 * a large i18n band label, and three mini axis bars.
 *
 * Colour semantics (contract-pinned):
 *   - breadth bar uses the up/down DIRECTION variables — it literally is the
 *     advancers-vs-decliners ratio, so the CN red-up flip is correct there;
 *   - volatility / volume bars use the neutral accent/blue palette — they
 *     carry no gain/loss meaning and must never flip;
 *   - the band itself is temperature (blue → muted → accent), never
 *     direction-coloured.
 *
 * Data: GET /api/market/sentiment, SWR 10s.
 */
import useSWR from 'swr';
import { fetcher } from '@/lib/fetcher';
import { useT } from '@/lib/i18n';
import type { TFunction } from '@/lib/i18n';
import type { MarketSentimentResponse } from '@/types/market';

export const SENTIMENT_LABELS = ['frozen', 'cool', 'neutral', 'active', 'hot'] as const;
export type SentimentLabel = (typeof SENTIMENT_LABELS)[number];

/**
 * Five bands at thresholds 0/20/40/60/80 — mirrors the backend mapping.
 * Used as the fallback when the payload label is missing/unknown, and to
 * position the active segment for a given score.
 */
export function labelForScore(score: number | undefined | null): SentimentLabel {
  if (score == null || !Number.isFinite(score)) return 'neutral';
  const idx = Math.min(Math.max(Math.floor(score / 20), 0), 4);
  return SENTIMENT_LABELS[idx];
}

/** Clamp an axis value into 0..100 for a mini-bar width. */
export function axisWidth(v: number | undefined | null): number {
  if (v == null || !Number.isFinite(v)) return 0;
  return Math.min(Math.max(Math.round(v), 0), 100);
}

// Band segment fills — brand palette only (terminal blue → muted → accent).
const SEGMENT_COLORS: Record<SentimentLabel, string> = {
  frozen: '#209dd7',
  cool: 'color-mix(in srgb, #209dd7 55%, transparent)',
  neutral: '#8b949e',
  active: 'color-mix(in srgb, #ecad0a 55%, transparent)',
  hot: '#ecad0a',
};

// Solid text colour per band for the big label.
const LABEL_COLORS: Record<SentimentLabel, string> = {
  frozen: '#209dd7',
  cool: '#209dd7',
  neutral: '#8b949e',
  active: '#ecad0a',
  hot: '#ecad0a',
};

function AxisBar({
  labelKey,
  value,
  color,
  axis,
  t,
}: {
  labelKey: string;
  value: number;
  color: string;
  axis: string;
  t: TFunction;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-16 shrink-0 text-[10px] text-terminal-muted uppercase tracking-wide truncate">
        {t(labelKey)}
      </span>
      <span className="flex-1 h-1.5 rounded bg-terminal-border/40 overflow-hidden">
        <span
          data-testid={`market-sentiment-axis-${axis}`}
          data-value={value}
          data-color={color}
          className="block h-full rounded"
          style={{ width: `${value}%`, backgroundColor: color }}
        />
      </span>
      <span className="w-7 shrink-0 text-right text-[10px] tabular-nums text-terminal-text">
        {value}
      </span>
    </div>
  );
}

export default function MarketSentiment() {
  const t = useT();
  const { data } = useSWR<MarketSentimentResponse>('/api/market/sentiment', fetcher, {
    refreshInterval: 10_000,
  });

  if (!data) {
    return (
      <div data-testid="market-sentiment" className="p-2 text-xs text-terminal-muted">
        {t('market.sentimentLoading')}
      </div>
    );
  }

  const label: SentimentLabel = (SENTIMENT_LABELS as readonly string[]).includes(data.label)
    ? (data.label as SentimentLabel)
    : labelForScore(data.score);
  const score = axisWidth(data.score);
  const breadth = axisWidth(data.axes?.breadth);
  const volatility = axisWidth(data.axes?.volatility);
  const volume = axisWidth(data.axes?.volume);

  // Breadth is advancers-vs-decliners — direction colours are the point here.
  const breadthColor =
    breadth > 50 ? 'var(--color-up)' : breadth < 50 ? 'var(--color-down)' : '#8b949e';

  return (
    <div data-testid="market-sentiment" data-label={label} className="p-2 flex flex-col gap-2">
      {/* Score + big band label */}
      <div className="flex items-baseline gap-2">
        <span
          data-testid="market-sentiment-score"
          className="text-2xl font-semibold tabular-nums text-terminal-text leading-none"
        >
          {Math.round(data.score)}
        </span>
        <span className="text-[10px] text-terminal-muted">/100</span>
        <span
          data-testid="market-sentiment-label"
          className="ml-auto text-sm font-semibold uppercase tracking-wider"
          style={{ color: LABEL_COLORS[label] }}
        >
          {t(`market.sentimentLabel.${label}`)}
        </span>
      </div>

      {/* Five-segment band with score marker */}
      <div data-testid="market-sentiment-band" className="relative h-2">
        <div className="flex h-full rounded overflow-hidden">
          {SENTIMENT_LABELS.map((seg) => (
            <span
              key={seg}
              data-testid={`market-sentiment-seg-${seg}`}
              data-active={seg === label ? 'true' : 'false'}
              className={`flex-1 ${seg === label ? '' : 'opacity-40'}`}
              style={{ background: SEGMENT_COLORS[seg] }}
            />
          ))}
        </div>
        <span
          data-testid="market-sentiment-marker"
          className="absolute -top-0.5 -bottom-0.5 w-0.5 rounded bg-terminal-text"
          style={{ left: `calc(${score}% - 1px)` }}
        />
      </div>

      {/* Mini axis bars */}
      <div className="flex flex-col gap-1">
        <AxisBar labelKey="market.sentimentBreadth" value={breadth} color={breadthColor} axis="breadth" t={t} />
        <AxisBar labelKey="market.sentimentVolatility" value={volatility} color="#ecad0a" axis="volatility" t={t} />
        <AxisBar labelKey="market.sentimentVolume" value={volume} color="#209dd7" axis="volume" t={t} />
      </div>
    </div>
  );
}
