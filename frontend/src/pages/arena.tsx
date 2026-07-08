/**
 * arena.tsx — /arena multi-user arena page (P1 §7). Exported statically as
 * arena/index.html.
 *
 * Left: the season leaderboard — the existing <Leaderboard/> component
 * mounted zero-modification (it also stays available in the desk's Board tab).
 * Right: season history (arena-seasons) from GET /api/seasons — period,
 * in-progress marker, and for ended seasons the archived results table
 * (rank/name/final_value/return_pct) with the champion highlighted in accent.
 */
import useSWR from 'swr';
import AppShell from '@/components/AppShell';
import Leaderboard from '@/components/Leaderboard';
import { fetcher } from '@/lib/fetcher';
import { useMarketProfile } from '@/lib/marketProfile';
import { useT } from '@/lib/i18n';
import { formatMoney } from '@/lib/format';
import type { Season, SeasonsResponse } from '@/types/market';

function formatDate(iso: string, locale: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(locale, { year: 'numeric', month: 'short', day: 'numeric' });
}

function SeasonCard({ season }: { season: Season }) {
  const t = useT();
  const profile = useMarketProfile();
  const money = { currency_symbol: profile.currency_symbol, locale: profile.locale };
  const inProgress = season.ended_at === null;
  const results = season.results ?? [];

  return (
    <div
      data-testid={`arena-season-${season.id}`}
      className="border-b border-terminal-border/60 py-2 last:border-b-0"
    >
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs font-semibold text-terminal-text">
          {t('arena.season', { id: season.id })}
        </span>
        <span className="text-[10px] text-terminal-muted tabular-nums">
          {formatDate(season.started_at, profile.locale)}
          {' – '}
          {season.ended_at ? formatDate(season.ended_at, profile.locale) : ''}
        </span>
        {inProgress && (
          <span
            data-testid={`arena-season-current-${season.id}`}
            className="text-[9px] font-semibold px-1 rounded border border-terminal-accent text-terminal-accent uppercase tracking-wider"
          >
            {t('arena.inProgress')}
          </span>
        )}
      </div>

      {!inProgress && results.length > 0 && (
        <table className="w-full text-xs border-collapse mt-1.5">
          <thead>
            <tr className="text-terminal-muted border-b border-terminal-border">
              <th className="text-left py-1 pl-1 font-semibold w-8">{t('arena.colRank')}</th>
              <th className="text-left py-1 font-semibold">{t('arena.colTrader')}</th>
              <th className="text-right py-1 font-semibold">{t('arena.colFinalValue')}</th>
              <th className="text-right py-1 pr-1 font-semibold">{t('arena.colReturn')}</th>
            </tr>
          </thead>
          <tbody>
            {results.map((entry) => {
              const champion = entry.rank === 1;
              const retColor =
                entry.return_pct > 0
                  ? 'text-terminal-up'
                  : entry.return_pct < 0
                    ? 'text-terminal-down'
                    : 'text-terminal-muted';
              return (
                <tr
                  key={entry.user_id}
                  data-testid={`arena-season-${season.id}-rank-${entry.rank}`}
                  className={`border-b border-terminal-border/40 ${
                    champion ? 'border-l-2 border-l-terminal-accent bg-terminal-surface' : ''
                  }`}
                >
                  <td
                    className={`py-1 pl-1 tabular-nums ${
                      champion ? 'text-terminal-accent font-semibold' : 'text-terminal-muted'
                    }`}
                  >
                    {champion ? '★' : entry.rank}
                  </td>
                  <td
                    className={`py-1 font-semibold ${
                      champion ? 'text-terminal-accent' : 'text-terminal-text'
                    }`}
                  >
                    {entry.name}
                  </td>
                  <td className="text-right py-1 tabular-nums text-terminal-text">
                    {formatMoney(entry.final_value, money)}
                  </td>
                  <td className={`text-right py-1 pr-1 tabular-nums ${retColor}`}>
                    {entry.return_pct > 0 ? '+' : ''}
                    {entry.return_pct.toFixed(2)}%
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function ArenaPage() {
  const t = useT();
  const { data } = useSWR<SeasonsResponse>('/api/seasons', fetcher);
  const seasons = data?.seasons ?? [];

  const sectionTitleClass =
    'px-2 py-1.5 text-xs font-semibold text-terminal-muted uppercase tracking-wider border-b border-terminal-border shrink-0';

  return (
    <AppShell>
      <div className="flex gap-4 h-full min-h-0">
        {/* Live season standings — existing component, zero modification */}
        <section className="flex-[3] min-w-0 flex flex-col min-h-0 border border-terminal-border rounded bg-terminal-surface/30">
          <div className="flex-1 min-h-0 overflow-auto">
            <Leaderboard />
          </div>
        </section>

        {/* Season history */}
        <section className="flex-[2] min-w-0 flex flex-col min-h-0 border border-terminal-border rounded bg-terminal-surface/30">
          <h2 className={sectionTitleClass}>{t('arena.seasonsTitle')}</h2>
          <div data-testid="arena-seasons" className="flex-1 min-h-0 overflow-auto px-2">
            {data && seasons.length === 0 ? (
              <p className="py-2 text-xs text-terminal-muted">{t('arena.seasonsEmpty')}</p>
            ) : (
              seasons.map((season) => <SeasonCard key={season.id} season={season} />)
            )}
          </div>
        </section>
      </div>
    </AppShell>
  );
}
