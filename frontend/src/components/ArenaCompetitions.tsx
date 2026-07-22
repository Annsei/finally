/**
 * ArenaCompetitions.tsx — /arena timed private competitions (D2 §5).
 *
 * Purely ADDITIVE to the arena page: the existing leaderboard/seasons DOM and
 * testids stay untouched. Everything here is guest-usable (cookie session —
 * single-user mode works unchanged).
 *
 * - `comp-create` form: `comp-name` (1..40) + `comp-hours` (integer 1..168) →
 *   POST /api/competitions; on 201 the invite code renders (`comp-code`) with
 *   a copy button (`comp-copy`).
 * - `comp-join-code` input + `comp-join` button → POST /api/competitions/join;
 *   failures surface as an inline toast (`comp-toast`), matching the
 *   HistoryCoverageCard toast precedent (fixed success/failure colours — never
 *   the direction variables).
 * - My competitions list: `comp-row-${id}` (name / status chip / member count /
 *   `comp-countdown-${id}` ticking down locally every second via one shared
 *   setInterval). Clicking a row header expands `comp-board-${id}` —
 *   rank/name/value/return% with direction colours, SWR-refreshed every 10s;
 *   ended boards carry a final-standings marker (`comp-final-${id}`).
 *
 * Pure helpers (exported for jest): validateCompName, validateCompHours,
 * competitionsList, createdCompetition, boardRows, detailStatus,
 * compRemainingMs, formatCompCountdown, copyCompText.
 */
import { useEffect, useState } from 'react';
import useSWR from 'swr';
import { fetcher } from '@/lib/fetcher';
import { useT } from '@/lib/i18n';
import { formatMoney } from '@/lib/format';
import { useMarketProfile } from '@/lib/marketProfile';
import type {
  CompetitionBoardRow,
  CompetitionStatus,
  CompetitionSummary,
} from '@/types/market';

export const COMPETITIONS_KEY = '/api/competitions?scope=mine';

// ---------------------------------------------------------------------------
// Pure helpers — exported for direct jest coverage.
// ---------------------------------------------------------------------------

/** Trimmed competition name, valid at 1..40 characters (contract §3). */
export function validateCompName(raw: string): { ok: true; name: string } | { ok: false } {
  const name = raw.trim();
  return name.length >= 1 && name.length <= 40 ? { ok: true, name } : { ok: false };
}

/** Duration in whole hours, valid at 1..168 (contract §3/§5). */
export function validateCompHours(raw: string): { ok: true; hours: number } | { ok: false } {
  const trimmed = raw.trim();
  if (trimmed === '' || !/^\d+$/.test(trimmed)) return { ok: false };
  const hours = Number(trimmed);
  return Number.isInteger(hours) && hours >= 1 && hours <= 168 ? { ok: true, hours } : { ok: false };
}

/**
 * List rows out of GET /api/competitions. Tolerates `{competitions: [...]}`
 * or a bare array; anything else → [] (renders the empty state, never crashes).
 */
export function competitionsList(data: unknown): CompetitionSummary[] {
  if (Array.isArray(data)) return data as CompetitionSummary[];
  if (data && typeof data === 'object') {
    const list = (data as Record<string, unknown>).competitions;
    if (Array.isArray(list)) return list as CompetitionSummary[];
  }
  return [];
}

/**
 * The created competition out of a POST /api/competitions 201 body —
 * `{competition: {...}}` per contract, with a flat-object fallback.
 */
export function createdCompetition(data: unknown): CompetitionSummary | null {
  if (!data || typeof data !== 'object') return null;
  const obj = data as Record<string, unknown>;
  const comp = obj.competition && typeof obj.competition === 'object' ? obj.competition : obj;
  const record = comp as Record<string, unknown>;
  return typeof record.code === 'string' ? (record as unknown as CompetitionSummary) : null;
}

/**
 * Board rows out of GET /api/competitions/{id}. Tolerates the board under the
 * detail root or nested under `competition`; anything else → [].
 */
export function boardRows(data: unknown): CompetitionBoardRow[] {
  if (!data || typeof data !== 'object') return [];
  const obj = data as Record<string, unknown>;
  if (Array.isArray(obj.board)) return obj.board as CompetitionBoardRow[];
  const nested = obj.competition;
  if (nested && typeof nested === 'object') {
    const board = (nested as Record<string, unknown>).board;
    if (Array.isArray(board)) return board as CompetitionBoardRow[];
  }
  return [];
}

/** Detail status out of GET /api/competitions/{id} (root or nested). */
export function detailStatus(data: unknown): CompetitionStatus | null {
  if (!data || typeof data !== 'object') return null;
  const obj = data as Record<string, unknown>;
  if (typeof obj.status === 'string') return obj.status as CompetitionStatus;
  const nested = obj.competition;
  if (nested && typeof nested === 'object') {
    const status = (nested as Record<string, unknown>).status;
    if (typeof status === 'string') return status as CompetitionStatus;
  }
  return null;
}

/**
 * Milliseconds left on the local countdown: running → until ends_at,
 * upcoming → until starts_at, ended (or unparsable timestamps) → 0.
 */
export function compRemainingMs(
  comp: Pick<CompetitionSummary, 'status' | 'starts_at' | 'ends_at'>,
  nowMs: number
): number {
  if (comp.status === 'ended') return 0;
  const target = new Date(comp.status === 'upcoming' ? comp.starts_at : comp.ends_at).getTime();
  if (isNaN(target)) return 0;
  return Math.max(0, target - nowMs);
}

/**
 * `H:MM:SS` countdown (hours unpadded — up to 168 for week-long runs),
 * clamped at 0:00:00 for negative/invalid input.
 */
export function formatCompCountdown(ms: number): string {
  const total = Math.max(0, Math.floor((Number.isFinite(ms) ? ms : 0) / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

/** Clipboard write that never throws — true on success. */
export async function copyCompText(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Clipboard API refused (permissions/insecure context) — report failure.
  }
  return false;
}

async function errorFrom(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => ({}));
  const msg = body?.error ?? body?.detail;
  return typeof msg === 'string' ? msg : `${fallback} (${res.status})`;
}

// ---------------------------------------------------------------------------
// Status chip — lifecycle state, not market direction: accent for running,
// muted/blue for the rest (direction colours stay reserved for P&L).
// ---------------------------------------------------------------------------
const STATUS_CHIP: Record<CompetitionStatus, string> = {
  upcoming: 'border-terminal-blue text-terminal-blue',
  running: 'border-terminal-accent text-terminal-accent',
  ended: 'border-terminal-border text-terminal-muted',
};

function statusChipClass(status: CompetitionStatus): string {
  return STATUS_CHIP[status] ?? STATUS_CHIP.ended;
}

// ---------------------------------------------------------------------------
// Expanded board — its own SWR subscription so only open rows poll (10s).
// ---------------------------------------------------------------------------
function CompetitionBoard({ id }: { id: string }) {
  const t = useT();
  const profile = useMarketProfile();
  const money = { currency_symbol: profile.currency_symbol, locale: profile.locale };
  const { data } = useSWR<unknown>(`/api/competitions/${id}`, fetcher, {
    refreshInterval: 10_000,
  });
  const rows = data === undefined ? null : boardRows(data);
  const ended = detailStatus(data) === 'ended';

  return (
    <div data-testid={`comp-board-${id}`} className="mt-1.5 border-t border-terminal-border/60 pt-1.5">
      {ended && (
        <span
          data-testid={`comp-final-${id}`}
          className="inline-block mb-1 text-[9px] font-semibold px-1 rounded border border-terminal-accent text-terminal-accent uppercase tracking-wider"
        >
          {t('arena.compFinal')}
        </span>
      )}
      {rows === null ? (
        <p className="text-xs text-terminal-muted">{t('arena.compBoardLoading')}</p>
      ) : rows.length === 0 ? (
        <p className="text-xs text-terminal-muted">{t('arena.compBoardEmpty')}</p>
      ) : (
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr className="text-terminal-muted border-b border-terminal-border">
              <th className="text-left py-1 pl-1 font-semibold w-8">{t('arena.compColRank')}</th>
              <th className="text-left py-1 font-semibold">{t('arena.compColTrader')}</th>
              <th className="text-right py-1 font-semibold">{t('arena.compColValue')}</th>
              <th className="text-right py-1 pr-1 font-semibold">{t('arena.compColReturn')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const retColor =
                row.return_pct > 0
                  ? 'text-terminal-up'
                  : row.return_pct < 0
                    ? 'text-terminal-down'
                    : 'text-terminal-muted';
              return (
                <tr
                  key={row.user_id}
                  data-testid={`comp-board-${id}-rank-${row.rank}`}
                  className="border-b border-terminal-border/40"
                >
                  <td className="py-1 pl-1 tabular-nums text-terminal-muted">{row.rank}</td>
                  <td className="py-1 font-semibold text-terminal-text">{row.name}</td>
                  <td className="text-right py-1 tabular-nums text-terminal-text">
                    {formatMoney(row.value, money)}
                  </td>
                  <td className={`text-right py-1 pr-1 tabular-nums ${retColor}`}>
                    {row.return_pct > 0 ? '+' : ''}
                    {row.return_pct.toFixed(2)}%
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

// ---------------------------------------------------------------------------
// Main section
// ---------------------------------------------------------------------------
export default function ArenaCompetitions() {
  const t = useT();
  const { data, mutate } = useSWR<unknown>(COMPETITIONS_KEY, fetcher, {
    refreshInterval: 10_000,
  });
  const comps = data === undefined ? null : competitionsList(data);

  // Create form
  const [name, setName] = useState('');
  const [hours, setHours] = useState('24');
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<CompetitionSummary | null>(null);
  const [copied, setCopied] = useState<'idle' | 'copied' | 'failed'>('idle');

  // Join
  const [joinCode, setJoinCode] = useState('');
  const [joining, setJoining] = useState(false);

  // Shared inline toast (create/join validation + server errors)
  const [toast, setToast] = useState<{ text: string; failed: boolean } | null>(null);

  // Expanded row → board
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // One shared 1s clock drives every row's local countdown; cleaned up on
  // unmount so jest fake-timer runs never leak intervals.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const create = async () => {
    if (creating) return;
    const validName = validateCompName(name);
    if (!validName.ok) {
      setToast({ text: t('arena.compErrName'), failed: true });
      return;
    }
    const validHours = validateCompHours(hours);
    if (!validHours.ok) {
      setToast({ text: t('arena.compErrHours'), failed: true });
      return;
    }
    setCreating(true);
    setToast(null);
    try {
      const res = await fetch('/api/competitions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: validName.name, hours: validHours.hours }),
      });
      if (!res.ok) throw new Error(await errorFrom(res, t('arena.compCreateFailed')));
      setCreated(createdCompetition(await res.json()));
      setCopied('idle');
      setName('');
      await mutate();
    } catch (e) {
      setToast({
        text: e instanceof Error ? e.message : t('arena.compCreateFailed'),
        failed: true,
      });
    } finally {
      setCreating(false);
    }
  };

  const join = async () => {
    if (joining) return;
    const code = joinCode.trim().toUpperCase();
    if (!code) {
      setToast({ text: t('arena.compErrCode'), failed: true });
      return;
    }
    setJoining(true);
    setToast(null);
    try {
      const res = await fetch('/api/competitions/join', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code }),
      });
      if (!res.ok) throw new Error(await errorFrom(res, t('arena.compJoinFailed')));
      setJoinCode('');
      await mutate();
    } catch (e) {
      setToast({
        text: e instanceof Error ? e.message : t('arena.compJoinFailed'),
        failed: true,
      });
    } finally {
      setJoining(false);
    }
  };

  const copy = async () => {
    if (!created?.code) return;
    setCopied((await copyCompText(created.code)) ? 'copied' : 'failed');
  };

  const inputClass =
    'px-2 py-1 text-xs bg-terminal-bg border border-terminal-border text-terminal-text rounded focus:outline-none focus:border-terminal-blue placeholder:text-terminal-muted';

  return (
    <div className="p-2 space-y-2">
      {/* Create */}
      <form
        data-testid="comp-create"
        className="flex items-center gap-1.5 flex-wrap"
        onSubmit={(e) => {
          e.preventDefault();
          void create();
        }}
      >
        <input
          data-testid="comp-name"
          aria-label={t('arena.compNameAria')}
          className={`${inputClass} flex-1 min-w-0`}
          placeholder={t('arena.compNamePlaceholder')}
          maxLength={40}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <input
          data-testid="comp-hours"
          aria-label={t('arena.compHoursAria')}
          className={`${inputClass} w-16 text-right tabular-nums`}
          type="number"
          min={1}
          max={168}
          step={1}
          value={hours}
          onChange={(e) => setHours(e.target.value)}
        />
        <span className="text-[10px] text-terminal-muted">{t('arena.compHoursUnit')}</span>
        <button
          type="submit"
          data-testid="comp-create-submit"
          disabled={creating}
          className="px-2 py-1 rounded text-[10px] font-semibold text-white disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
          style={{ backgroundColor: '#753991' }}
        >
          {creating ? t('arena.compCreating') : t('arena.compCreate')}
        </button>
      </form>

      {/* Freshly created → share code + copy */}
      {created?.code && (
        <div
          data-testid="comp-created"
          className="flex items-center gap-2 px-2 py-1.5 rounded border border-terminal-accent/60 bg-terminal-surface/60"
        >
          <span className="text-[10px] uppercase tracking-wider text-terminal-muted">
            {t('arena.compCodeLabel')}
          </span>
          <span
            data-testid="comp-code"
            className="text-sm font-semibold tracking-[0.2em] text-terminal-accent tabular-nums"
          >
            {created.code}
          </span>
          <button
            type="button"
            data-testid="comp-copy"
            onClick={() => void copy()}
            className="ml-auto px-2 py-0.5 rounded border border-terminal-border text-[10px] font-semibold text-terminal-text hover:border-terminal-blue"
          >
            {copied === 'copied'
              ? t('arena.compCopied')
              : copied === 'failed'
                ? t('arena.compCopyFailed')
                : t('arena.compCopy')}
          </button>
        </div>
      )}

      {/* Join by invite code */}
      <div className="flex items-center gap-1.5">
        <input
          data-testid="comp-join-code"
          aria-label={t('arena.compJoinAria')}
          className={`${inputClass} flex-1 min-w-0 uppercase tracking-widest`}
          placeholder={t('arena.compJoinPlaceholder')}
          maxLength={6}
          value={joinCode}
          onChange={(e) => setJoinCode(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              void join();
            }
          }}
        />
        <button
          type="button"
          data-testid="comp-join"
          onClick={() => void join()}
          disabled={joining}
          className="px-2 py-1 rounded text-[10px] font-semibold text-white disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
          style={{ backgroundColor: '#753991' }}
        >
          {joining ? t('arena.compJoining') : t('arena.compJoin')}
        </button>
      </div>

      {toast && (
        <p
          data-testid="comp-toast"
          className="px-2 py-1 rounded text-[10px] leading-tight text-terminal-text bg-terminal-bg/60"
          // Success/failure framing, not market direction — fixed colours
          // (TradeBar / HistoryCoverageCard toast precedent).
          style={{ border: `1px solid ${toast.failed ? '#ef4444' : '#22c55e'}` }}
        >
          {toast.text}
        </p>
      )}

      {/* My competitions */}
      <div data-testid="comp-list" className="space-y-1">
        {comps === null ? (
          <p className="text-xs text-terminal-muted">{t('arena.compLoading')}</p>
        ) : comps.length === 0 ? (
          <p className="text-xs text-terminal-muted">{t('arena.compListEmpty')}</p>
        ) : (
          comps.map((comp) => {
            const expanded = expandedId === comp.id;
            return (
              <div
                key={comp.id}
                data-testid={`comp-row-${comp.id}`}
                className="px-2 py-1.5 rounded border border-terminal-border/60 bg-terminal-surface/40"
              >
                <button
                  type="button"
                  data-testid={`comp-row-toggle-${comp.id}`}
                  aria-expanded={expanded}
                  onClick={() => setExpandedId(expanded ? null : comp.id)}
                  className="w-full flex items-center gap-2 text-left"
                >
                  <span className="text-xs font-semibold text-terminal-text truncate">
                    {comp.name}
                  </span>
                  <span
                    className={`text-[9px] font-semibold px-1 rounded border uppercase tracking-wider shrink-0 ${statusChipClass(comp.status)}`}
                  >
                    {t(`arena.compStatus.${comp.status}`)}
                  </span>
                  <span className="text-[10px] text-terminal-muted shrink-0">
                    {t('arena.compMembers', { n: comp.member_count })}
                  </span>
                  <span
                    data-testid={`comp-countdown-${comp.id}`}
                    className="ml-auto text-[10px] tabular-nums text-terminal-muted shrink-0"
                  >
                    {comp.status === 'ended' ? '—' : formatCompCountdown(compRemainingMs(comp, now))}
                  </span>
                </button>
                {expanded && <CompetitionBoard id={comp.id} />}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
