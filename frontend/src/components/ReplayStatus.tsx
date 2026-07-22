/**
 * ReplayStatus.tsx — market-replay indicators (D3 §3).
 *
 * Both components poll GET /api/market/replay (SWR, 10s) and render NOTHING —
 * literally null, zero DOM — unless the payload is exactly {active: true, …}.
 * Outside replay mode (the default deployment) every existing page therefore
 * stays byte-identical.
 *
 *   ReplayBadge   StatusBar chip: "回放 {date} · {i}/{n}" (amber, i18n
 *                 replay.badge), data-testid="replay-badge". Appended after
 *                 the session badge — the session badge itself is untouched.
 *                 The backend's day_index is 0-based (first day == 0); the
 *                 human-readable {i} is day_index + 1, so the first replay
 *                 day reads "1/{n}" and the last reads "{n}/{n}".
 *   ReplayBanner  /market page strip: window, current day, progress bar,
 *                 loop chip; finished (no-loop, window exhausted) shows
 *                 "回放已结束（价格冻结）", data-testid="replay-banner".
 *
 * Pure helpers (isReplayActive, replayProgressPct) are exported for direct
 * unit testing.
 */
import useSWR from 'swr';
import { fetcher } from '@/lib/fetcher';
import { useT } from '@/lib/i18n';
import type { ReplayStatusActive, ReplayStatusResponse } from '@/types/market';

export const REPLAY_STATUS_KEY = '/api/market/replay';
export const REPLAY_REFRESH_MS = 10_000;

/**
 * Type guard: true only for a well-formed ACTIVE replay payload. Anything
 * else — undefined (loading/error), {active:false}, or a foreign payload a
 * blanket SWR mock hands back (e.g. the session snapshot) — reads inactive,
 * so the indicators never render outside replay mode.
 */
export function isReplayActive(data: ReplayStatusResponse | undefined | null): data is ReplayStatusActive {
  return data != null && typeof data === 'object' && data.active === true;
}

/**
 * Progress through the replay window as an integer 0..100 (clamped).
 * `dayCount` is the 1-based day number — callers pass the endpoint's
 * 0-based `day_index + 1`, so a finished no-loop replay (day_index ==
 * total_days - 1) reads 100%.
 */
export function replayProgressPct(dayCount: number, totalDays: number): number {
  if (!Number.isFinite(dayCount) || !Number.isFinite(totalDays) || totalDays <= 0) return 0;
  return Math.round(Math.min(Math.max(dayCount / totalDays, 0), 1) * 100);
}

function useReplayStatus(): ReplayStatusResponse | undefined {
  const { data } = useSWR<ReplayStatusResponse>(REPLAY_STATUS_KEY, fetcher, {
    refreshInterval: REPLAY_REFRESH_MS,
  });
  return data;
}

/** StatusBar chip — additive; renders null unless replay is active. */
export function ReplayBadge() {
  const t = useT();
  const data = useReplayStatus();
  if (!isReplayActive(data)) return null;
  return (
    <span
      data-testid="replay-badge"
      data-finished={data.finished ? 'true' : 'false'}
      className="font-semibold tabular-nums text-terminal-amber"
    >
      {t('replay.badge', { date: data.current_date, i: data.day_index + 1, n: data.total_days })}
    </span>
  );
}

/** /market page strip — additive; renders null unless replay is active. */
export function ReplayBanner() {
  const t = useT();
  const data = useReplayStatus();
  if (!isReplayActive(data)) return null;

  const pct = replayProgressPct(data.day_index + 1, data.total_days);
  return (
    <section
      data-testid="replay-banner"
      data-finished={data.finished ? 'true' : 'false'}
      className="shrink-0 mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 border border-terminal-amber/60 rounded bg-terminal-amber/10 text-xs"
    >
      <span className="font-semibold uppercase tracking-wider text-terminal-amber">
        {t('replay.title')}
      </span>
      <span data-testid="replay-banner-window" className="tabular-nums text-terminal-text">
        {t('replay.window', { from: data.from, to: data.to })}
      </span>
      <span data-testid="replay-banner-day" className="tabular-nums text-terminal-text">
        {t('replay.day', { date: data.current_date, i: data.day_index + 1, n: data.total_days })}
      </span>
      <span
        role="progressbar"
        aria-label={t('replay.progressAria')}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        data-testid="replay-banner-progress"
        data-pct={pct}
        className="h-1.5 w-32 rounded bg-terminal-border/60 overflow-hidden"
      >
        <span
          data-testid="replay-banner-progress-fill"
          className="block h-full bg-terminal-amber"
          style={{ width: `${pct}%` }}
        />
      </span>
      <span
        data-testid="replay-banner-mode"
        className="text-[10px] px-1 rounded border border-terminal-amber/60 text-terminal-amber uppercase tracking-wide"
      >
        {t(data.loop ? 'replay.loop' : 'replay.once')}
      </span>
      {data.finished && (
        <span data-testid="replay-banner-finished" className="font-semibold text-terminal-amber">
          {t('replay.finished')}
        </span>
      )}
    </section>
  );
}
