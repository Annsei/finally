/**
 * developers.tsx — /developers developer portal (P3 §8). Exported statically
 * as developers/index.html (trailingSlash: true).
 *
 * Four blocks:
 *   dev-keys        key list over SWR GET /api/keys — rows dev-key-row-${id}
 *                   with freeze toggle (dev-key-freeze-${id}, immediate),
 *                   revoke (dev-key-revoke-${id}, two-click confirm) and an
 *                   expandable constraint editor (dev-key-edit-${id};
 *                   empty input = null = unrestricted, PATCHed explicitly).
 *   dev-key-create  create form (label + optional constraints) → on success
 *                   the ONE-TIME plaintext renders in dev-key-secret with a
 *                   dev-key-copy button. The secret lives only in component
 *                   state — navigate away or refresh and it is gone for good.
 *   dev-audit       per-key audit ledger (key dropdown + table). Result badges
 *                   use the terminal-up/down/amber semantic classes; digest is
 *                   muted. dev-audit-more pages older entries via the
 *                   created_at `before` cursor (EventArchive pattern).
 *   dev-quickstart  curl + Python snippets (Bearer header; origin resolved
 *                   from location.origin after mount — SSR-safe), a Swagger
 *                   link (/api/docs) and the examples/finally_bot.py pointer.
 *
 * Key management is cookie-only on the backend (§6): Bearer calls get 403, so
 * this page always acts as the signed-in (or Guest) browser identity.
 *
 * Pure helpers (exported for jest): parseTickersInput, tickersInputValue,
 * buildConstraints, constraintSummary, resultBadgeClass, curlSnippet,
 * pythonSnippet, copyText.
 */
import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import useSWR from 'swr';
import AppShell from '@/components/AppShell';
import { fetcher } from '@/lib/fetcher';
import { useMarketProfile } from '@/lib/marketProfile';
import { useT, type TFunction } from '@/lib/i18n';
import type {
  ApiAuditEntry,
  ApiAuditResponse,
  ApiKeyCreateResponse,
  ApiKeyInfo,
  ApiKeysResponse,
} from '@/types/market';

export const KEYS_KEY = '/api/keys';
const AUDIT_PAGE_SIZE = 50;

// Build-time (static export) placeholder — replaced with the real
// location.origin after mount, so the prerendered HTML never hydration-mismatches.
const DEFAULT_ORIGIN = 'http://localhost:8000';

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/**
 * Comma-separated ticker input → uppercase, deduped list; empty/whitespace-only
 * input → null (= unrestricted, the API's explicit-null semantics).
 */
export function parseTickersInput(input: string): string[] | null {
  const tickers = input
    .split(',')
    .map((s) => s.trim().toUpperCase())
    .filter((s) => s.length > 0);
  if (tickers.length === 0) return null;
  return [...new Set(tickers)];
}

/** Inverse of parseTickersInput for prefilling the edit form. */
export function tickersInputValue(tickers: string[] | null | undefined): string {
  return tickers?.join(',') ?? '';
}

export type ConstraintsPayload = {
  allowed_tickers: string[] | null;
  max_order_qty: number | null;
  daily_trade_cap: number | null;
};

export type ConstraintsResult =
  | ({ ok: true } & ConstraintsPayload)
  | { ok: false; errorKey: 'dev.errMaxQty' | 'dev.errDailyCap' };

/**
 * Validate + normalize the three optional constraint inputs. Empty string →
 * null (unrestricted). max qty must parse > 0; daily cap must be an integer ≥ 1.
 */
export function buildConstraints(
  tickersInput: string,
  maxQtyInput: string,
  dailyCapInput: string
): ConstraintsResult {
  let maxOrderQty: number | null = null;
  if (maxQtyInput.trim() !== '') {
    const qty = Number(maxQtyInput);
    if (!Number.isFinite(qty) || qty <= 0) return { ok: false, errorKey: 'dev.errMaxQty' };
    maxOrderQty = qty;
  }
  let dailyCap: number | null = null;
  if (dailyCapInput.trim() !== '') {
    const cap = Number(dailyCapInput);
    if (!Number.isInteger(cap) || cap < 1) return { ok: false, errorKey: 'dev.errDailyCap' };
    dailyCap = cap;
  }
  return {
    ok: true,
    allowed_tickers: parseTickersInput(tickersInput),
    max_order_qty: maxOrderQty,
    daily_trade_cap: dailyCap,
  };
}

/** Human summary of a key's guardrails — t('dev.unrestricted') when none. */
export function constraintSummary(key: ApiKeyInfo, t: TFunction): string {
  const parts: string[] = [];
  if (key.allowed_tickers != null && key.allowed_tickers.length > 0) {
    parts.push(t('dev.constraintTickers', { list: key.allowed_tickers.join(',') }));
  }
  if (key.max_order_qty != null) {
    parts.push(t('dev.constraintMaxQty', { qty: key.max_order_qty }));
  }
  if (key.daily_trade_cap != null) {
    parts.push(t('dev.constraintDailyCap', { n: key.daily_trade_cap }));
  }
  return parts.length > 0 ? parts.join(' · ') : t('dev.unrestricted');
}

/**
 * Audit result → semantic badge classes (P3 §8): ok = up colour,
 * denied/error = down colour, rate_limited = amber; anything else muted.
 */
export function resultBadgeClass(result: string): string {
  if (result === 'ok') return 'text-terminal-up border-terminal-up/60';
  if (result === 'denied' || result === 'error')
    return 'text-terminal-down border-terminal-down/60';
  if (result === 'rate_limited') return 'text-terminal-amber border-terminal-amber/60';
  return 'text-terminal-muted border-terminal-border';
}

/** curl quickstart — Bearer header against the live origin. */
export function curlSnippet(origin: string): string {
  return [
    `curl -X POST ${origin}/api/portfolio/trade \\`,
    `  -H "Authorization: Bearer fk_YOUR_KEY" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -d '{"ticker": "NVDA", "side": "buy", "quantity": 1}'`,
  ].join('\n');
}

/** Python (requests) quickstart — same Bearer header, env-driven key. */
export function pythonSnippet(origin: string): string {
  return [
    'import os, requests',
    '',
    `BASE = os.environ.get("FINALLY_URL", "${origin}")`,
    'HEADERS = {"Authorization": "Bearer " + os.environ["FINALLY_API_KEY"]}',
    '',
    'quotes = requests.get(BASE + "/api/market/quotes", headers=HEADERS).json()',
    'resp = requests.post(',
    '    BASE + "/api/portfolio/trade",',
    '    headers=HEADERS,',
    '    json={"ticker": "NVDA", "side": "buy", "quantity": 1},',
    ')',
    'print(resp.status_code, resp.json())',
  ].join('\n');
}

/**
 * Copy text to the clipboard: navigator.clipboard first, hidden-textarea
 * execCommand fallback (older browsers / non-secure contexts). Never throws.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Clipboard API refused (permissions/insecure context) — try the fallback.
  }
  if (typeof document === 'undefined' || typeof document.execCommand !== 'function') {
    return false;
  }
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  try {
    textarea.select();
    return document.execCommand('copy');
  } catch {
    return false;
  } finally {
    // The textarea holds the key plaintext — remove it even when
    // execCommand throws, so the secret never lingers in the DOM.
    document.body.removeChild(textarea);
  }
}

// ---------------------------------------------------------------------------
// Shared bits
// ---------------------------------------------------------------------------

const inputClass =
  'px-2 py-1 text-xs font-mono bg-terminal-bg border border-terminal-border text-terminal-text rounded focus:outline-none focus:border-terminal-blue placeholder:text-terminal-muted';

const sectionClass =
  'border border-terminal-border rounded bg-terminal-surface/30 flex flex-col min-h-0';

const sectionTitleClass =
  'px-2 py-1.5 text-xs font-semibold text-terminal-muted uppercase tracking-wider border-b border-terminal-border shrink-0';

function formatWhen(iso: string | null | undefined, locale: string, never: string): string {
  if (!iso) return never;
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString(locale, { hour12: false });
}

async function errorFrom(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => ({}));
  return body?.error ?? `${fallback} (${res.status})`;
}

// ---------------------------------------------------------------------------
// Key row — display + inline constraint editor
// ---------------------------------------------------------------------------
function KeyRow({
  apiKey,
  locale,
  armedRevoke,
  onFreeze,
  onRevoke,
  onSave,
}: {
  apiKey: ApiKeyInfo;
  locale: string;
  armedRevoke: boolean;
  onFreeze: (key: ApiKeyInfo) => Promise<void>;
  onRevoke: (key: ApiKeyInfo) => Promise<void>;
  onSave: (key: ApiKeyInfo, payload: ConstraintsPayload) => Promise<boolean>;
}) {
  const t = useT();
  const [editing, setEditing] = useState(false);
  const [tickersInput, setTickersInput] = useState('');
  const [maxQtyInput, setMaxQtyInput] = useState('');
  const [capInput, setCapInput] = useState('');
  const [saving, setSaving] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const frozen = Boolean(apiKey.frozen);

  const toggleEdit = () => {
    if (!editing) {
      // Prefill from the live key so "save with no changes" is a no-op PATCH.
      setTickersInput(tickersInputValue(apiKey.allowed_tickers));
      setMaxQtyInput(apiKey.max_order_qty != null ? String(apiKey.max_order_qty) : '');
      setCapInput(apiKey.daily_trade_cap != null ? String(apiKey.daily_trade_cap) : '');
      setEditError(null);
    }
    setEditing(!editing);
  };

  const save = async () => {
    const constraints = buildConstraints(tickersInput, maxQtyInput, capInput);
    if (!constraints.ok) {
      setEditError(t(constraints.errorKey));
      return;
    }
    setSaving(true);
    setEditError(null);
    const payload: ConstraintsPayload = {
      allowed_tickers: constraints.allowed_tickers,
      max_order_qty: constraints.max_order_qty,
      daily_trade_cap: constraints.daily_trade_cap,
    };
    const saved = await onSave(apiKey, payload);
    setSaving(false);
    if (saved) setEditing(false);
    else setEditError(t('dev.updateFailed'));
  };

  const actionButton =
    'text-[10px] font-semibold uppercase tracking-wider transition-colors disabled:opacity-50';

  return (
    <>
      <tr
        data-testid={`dev-key-row-${apiKey.id}`}
        className="border-b border-terminal-border/60"
      >
        <td className="py-1 pl-1 font-semibold text-terminal-text">{apiKey.label}</td>
        <td className="py-1 font-mono text-terminal-muted">{apiKey.prefix}…</td>
        <td className="py-1 tabular-nums text-terminal-muted">
          {formatWhen(apiKey.created_at, locale, '—')}
        </td>
        <td className="py-1 tabular-nums text-terminal-muted">
          {formatWhen(apiKey.last_used_at, locale, t('dev.neverUsed'))}
        </td>
        <td className="py-1 text-terminal-muted">{constraintSummary(apiKey, t)}</td>
        <td className="py-1">
          <span
            data-testid={`dev-key-status-${apiKey.id}`}
            className={`text-[9px] font-semibold px-1 rounded border uppercase tracking-wide ${
              frozen
                ? 'text-terminal-amber border-terminal-amber/60'
                : 'text-terminal-up border-terminal-up/60'
            }`}
          >
            {frozen ? t('dev.frozen') : t('dev.active')}
          </span>
        </td>
        <td className="py-1 pr-1 text-right whitespace-nowrap">
          <span className="inline-flex items-center gap-2">
            <button
              type="button"
              data-testid={`dev-key-edit-${apiKey.id}`}
              onClick={toggleEdit}
              className={`${actionButton} ${
                editing ? 'text-terminal-accent' : 'text-terminal-muted hover:text-terminal-accent'
              }`}
            >
              {editing ? t('dev.cancel') : t('dev.edit')}
            </button>
            <button
              type="button"
              data-testid={`dev-key-freeze-${apiKey.id}`}
              onClick={() => void onFreeze(apiKey)}
              className={`${actionButton} text-terminal-muted hover:text-terminal-amber`}
            >
              {frozen ? t('dev.unfreeze') : t('dev.freeze')}
            </button>
            <button
              type="button"
              data-testid={`dev-key-revoke-${apiKey.id}`}
              onClick={() => void onRevoke(apiKey)}
              className={`${actionButton} ${
                armedRevoke ? 'text-terminal-down' : 'text-terminal-muted hover:text-terminal-down'
              }`}
            >
              {armedRevoke ? t('dev.confirmRevoke') : t('dev.revoke')}
            </button>
          </span>
        </td>
      </tr>
      {editing && (
        <tr data-testid={`dev-key-editor-${apiKey.id}`} className="border-b border-terminal-border/60">
          <td colSpan={7} className="py-2 pl-1 pr-1">
            <div className="flex items-center gap-2 flex-wrap">
              <input
                type="text"
                data-testid={`dev-key-edit-tickers-${apiKey.id}`}
                aria-label={t('dev.tickersAria')}
                placeholder={t('dev.tickersPlaceholder')}
                value={tickersInput}
                onChange={(e) => setTickersInput(e.target.value.toUpperCase())}
                className={`w-64 ${inputClass}`}
              />
              <input
                type="number"
                data-testid={`dev-key-edit-max-qty-${apiKey.id}`}
                aria-label={t('dev.maxQtyAria')}
                placeholder={t('dev.maxQtyPlaceholder')}
                value={maxQtyInput}
                onChange={(e) => setMaxQtyInput(e.target.value)}
                min="0"
                step="any"
                className={`w-36 ${inputClass}`}
              />
              <input
                type="number"
                data-testid={`dev-key-edit-cap-${apiKey.id}`}
                aria-label={t('dev.dailyCapAria')}
                placeholder={t('dev.dailyCapPlaceholder')}
                value={capInput}
                onChange={(e) => setCapInput(e.target.value)}
                min="1"
                step="1"
                className={`w-36 ${inputClass}`}
              />
              <button
                type="button"
                data-testid={`dev-key-save-${apiKey.id}`}
                onClick={() => void save()}
                disabled={saving}
                className="px-2 py-1 rounded text-[10px] font-semibold uppercase tracking-wider text-white disabled:opacity-50"
                style={{ backgroundColor: '#753991' }}
              >
                {saving ? t('dev.saving') : t('dev.save')}
              </button>
              <span className="text-[10px] text-terminal-muted">{t('dev.editHint')}</span>
              {editError && (
                <span
                  data-testid={`dev-key-edit-error-${apiKey.id}`}
                  className="text-[10px] text-terminal-down"
                >
                  {editError}
                </span>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Create form + one-time secret
// ---------------------------------------------------------------------------
function CreateKeyBlock({ onCreated }: { onCreated: () => Promise<unknown> }) {
  const t = useT();
  const [label, setLabel] = useState('');
  const [tickersInput, setTickersInput] = useState('');
  const [maxQtyInput, setMaxQtyInput] = useState('');
  const [capInput, setCapInput] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The ONE-TIME plaintext — component state only, gone on refresh (P3 §8).
  const [secret, setSecret] = useState<string | null>(null);
  const [copied, setCopied] = useState<'idle' | 'copied' | 'failed'>('idle');

  const create = async () => {
    const trimmed = label.trim();
    if (trimmed.length < 1 || trimmed.length > 40) {
      setError(t('dev.errLabel'));
      return;
    }
    const constraints = buildConstraints(tickersInput, maxQtyInput, capInput);
    if (!constraints.ok) {
      setError(t(constraints.errorKey));
      return;
    }
    setPending(true);
    setError(null);
    try {
      const body: Record<string, unknown> = { label: trimmed };
      if (constraints.allowed_tickers != null) body.allowed_tickers = constraints.allowed_tickers;
      if (constraints.max_order_qty != null) body.max_order_qty = constraints.max_order_qty;
      if (constraints.daily_trade_cap != null) body.daily_trade_cap = constraints.daily_trade_cap;
      const res = await fetch(KEYS_KEY, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await errorFrom(res, t('dev.createFailed')));
      const data: ApiKeyCreateResponse = await res.json();
      setSecret(data.key);
      setCopied('idle');
      setLabel('');
      setTickersInput('');
      setMaxQtyInput('');
      setCapInput('');
      await onCreated();
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : t('dev.createFailed'));
    } finally {
      setPending(false);
    }
  };

  const copy = async () => {
    if (secret == null) return;
    setCopied((await copyText(secret)) ? 'copied' : 'failed');
  };

  return (
    <div className="p-2 flex flex-col gap-2">
      <div className="flex items-center gap-2 flex-wrap">
        <input
          type="text"
          data-testid="dev-key-label"
          aria-label={t('dev.labelAria')}
          placeholder={t('dev.labelPlaceholder')}
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          maxLength={40}
          className={`w-44 ${inputClass}`}
        />
        <input
          type="text"
          data-testid="dev-key-tickers"
          aria-label={t('dev.tickersAria')}
          placeholder={t('dev.tickersPlaceholder')}
          value={tickersInput}
          onChange={(e) => setTickersInput(e.target.value.toUpperCase())}
          className={`w-64 ${inputClass}`}
        />
        <input
          type="number"
          data-testid="dev-key-max-qty"
          aria-label={t('dev.maxQtyAria')}
          placeholder={t('dev.maxQtyPlaceholder')}
          value={maxQtyInput}
          onChange={(e) => setMaxQtyInput(e.target.value)}
          min="0"
          step="any"
          className={`w-36 ${inputClass}`}
        />
        <input
          type="number"
          data-testid="dev-key-daily-cap"
          aria-label={t('dev.dailyCapAria')}
          placeholder={t('dev.dailyCapPlaceholder')}
          value={capInput}
          onChange={(e) => setCapInput(e.target.value)}
          min="1"
          step="1"
          className={`w-36 ${inputClass}`}
        />
        <button
          type="button"
          data-testid="dev-key-create"
          onClick={() => void create()}
          disabled={pending}
          className="px-3 py-1 rounded text-[10px] font-semibold uppercase tracking-wider text-white disabled:opacity-50"
          style={{ backgroundColor: '#753991' }}
        >
          {pending ? t('dev.creating') : t('dev.create')}
        </button>
      </div>
      {error && (
        <p data-testid="dev-key-create-error" className="text-xs text-terminal-down">
          {error}
        </p>
      )}
      {secret != null && (
        <div className="border border-terminal-amber/60 rounded p-2 flex flex-col gap-1.5 bg-terminal-bg">
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-terminal-amber">
              {t('dev.secretTitle')}
            </span>
            <button
              type="button"
              data-testid="dev-key-secret-dismiss"
              onClick={() => setSecret(null)}
              className="text-[10px] font-semibold uppercase tracking-wider text-terminal-muted hover:text-terminal-text"
            >
              {t('dev.dismiss')}
            </button>
          </div>
          <div className="flex items-center gap-2">
            <code
              data-testid="dev-key-secret"
              className="flex-1 min-w-0 break-all text-xs text-terminal-text tabular-nums"
            >
              {secret}
            </code>
            <button
              type="button"
              data-testid="dev-key-copy"
              onClick={() => void copy()}
              className="px-2 py-1 rounded text-[10px] font-semibold uppercase tracking-wider border border-terminal-border text-terminal-muted hover:text-terminal-text hover:border-terminal-blue shrink-0"
            >
              {copied === 'copied'
                ? t('dev.copied')
                : copied === 'failed'
                  ? t('dev.copyFailed')
                  : t('dev.copy')}
            </button>
          </div>
          <p className="text-[10px] text-terminal-amber leading-snug">{t('dev.secretWarning')}</p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Audit ledger — key dropdown + paged table
// ---------------------------------------------------------------------------
function AuditBlock({ keys }: { keys: ApiKeyInfo[] }) {
  const t = useT();
  const [selectedId, setSelectedId] = useState('');
  // Derived during render (no effect): fall back to the first key when nothing
  // is selected yet or when the selected key was just revoked.
  const keyId = keys.some((k) => k.id === selectedId) ? selectedId : (keys[0]?.id ?? '');

  return (
    <div data-testid="dev-audit" className="p-2 flex flex-col min-h-0">
      <select
        data-testid="dev-audit-select"
        aria-label={t('dev.auditKeyAria')}
        value={keyId}
        onChange={(e) => setSelectedId(e.target.value)}
        className={`mb-2 self-start ${inputClass}`}
      >
        {keys.length === 0 && <option value="">{t('dev.auditSelectKey')}</option>}
        {keys.map((k) => (
          <option key={k.id} value={k.id}>
            {k.label} ({k.prefix}…)
          </option>
        ))}
      </select>

      {keyId === '' ? (
        <p className="text-xs text-terminal-muted">{t('dev.auditEmpty')}</p>
      ) : (
        // key= remounts the table per API key, resetting the pagination state.
        <AuditTable key={keyId} keyId={keyId} />
      )}
    </div>
  );
}

function AuditTable({ keyId }: { keyId: string }) {
  const t = useT();
  const profile = useMarketProfile();
  const auditKey = `/api/keys/${keyId}/audit?limit=${AUDIT_PAGE_SIZE}`;
  const { data } = useSWR<ApiAuditResponse>(auditKey, fetcher);

  // Older pages accumulate locally (EventArchive pattern) — fresh per key
  // because AuditBlock remounts this component via key={keyId}.
  const [older, setOlder] = useState<ApiAuditEntry[]>([]);
  const [olderHasMore, setOlderHasMore] = useState<boolean | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  const firstPage = data?.entries ?? [];

  // Once "load more" is in use, the merged list is an accumulated ledger —
  // when a revalidated first page slides forward (new audit rows arrive),
  // entries that fell out of the newest window would silently vanish between
  // the first page and `older`. Fold them into `older` instead (EventArchive
  // pattern): merge by id, dedupe, newest first. With no pagination in play,
  // `older` stays empty and the pure newest-window semantics are unchanged.
  const prevFirstPageRef = useRef<ApiAuditEntry[]>([]);
  useEffect(() => {
    const prev = prevFirstPageRef.current;
    const current = data?.entries ?? [];
    prevFirstPageRef.current = current;
    if (prev.length === 0) return;
    const currentIds = new Set(current.map((e) => e.id));
    const slidOut = prev.filter((e) => !currentIds.has(e.id));
    if (slidOut.length === 0) return;
    setOlder((prevOlder) => {
      if (prevOlder.length === 0) return prevOlder;
      const olderIds = new Set(prevOlder.map((e) => e.id));
      const additions = slidOut.filter((e) => !olderIds.has(e.id));
      if (additions.length === 0) return prevOlder;
      return [...prevOlder, ...additions].sort((a, b) =>
        a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0
      );
    });
  }, [data]);

  const seen = new Set(firstPage.map((e) => e.id));
  const entries = [...firstPage, ...older.filter((e) => !seen.has(e.id))];
  const hasMore = olderHasMore ?? data?.has_more ?? false;

  const loadMore = async () => {
    if (loadingMore || entries.length === 0) return;
    setLoadingMore(true);
    try {
      // Cursor = oldest created_at across the merged list, so paging
      // continues past everything already shown even after first-page/older
      // merges (same cursor rule as EventArchive).
      const before = entries.reduce(
        (min, e) => (e.created_at < min ? e.created_at : min),
        entries[0].created_at
      );
      const page: ApiAuditResponse = await fetcher(
        `${auditKey}&before=${encodeURIComponent(before)}`
      );
      setOlder((prev) => [...prev, ...page.entries]);
      setOlderHasMore(page.has_more);
    } catch {
      // Keep the button enabled — the user can retry.
    } finally {
      setLoadingMore(false);
    }
  };

  return (
    <>
      {!data ? (
        <p className="text-xs text-terminal-muted">{t('dev.auditLoading')}</p>
      ) : entries.length === 0 ? (
        <p className="text-xs text-terminal-muted">{t('dev.auditEmpty')}</p>
      ) : (
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr className="text-terminal-muted border-b border-terminal-border">
              <th className="text-left py-1 pl-1 font-semibold">{t('dev.auditColTime')}</th>
              <th className="text-left py-1 font-semibold">{t('dev.auditColRequest')}</th>
              <th className="text-left py-1 font-semibold">{t('dev.auditColResult')}</th>
              <th className="text-left py-1 pr-1 font-semibold">{t('dev.auditColDigest')}</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr
                key={entry.id}
                data-testid={`dev-audit-row-${entry.id}`}
                className="border-b border-terminal-border/40"
              >
                <td className="py-1 pl-1 tabular-nums text-terminal-muted whitespace-nowrap">
                  {formatWhen(entry.created_at, profile.locale, '—')}
                </td>
                <td className="py-1 font-mono text-terminal-text">
                  <span className="font-semibold">{entry.method}</span> {entry.endpoint}
                </td>
                <td className="py-1 whitespace-nowrap">
                  <span
                    data-testid={`dev-audit-result-${entry.id}`}
                    className={`text-[9px] font-semibold px-1 rounded border uppercase tracking-wide ${resultBadgeClass(
                      entry.result
                    )}`}
                  >
                    {entry.result}
                  </span>
                  {entry.status_code != null && (
                    <span className="ml-1 text-[10px] text-terminal-muted tabular-nums">
                      {entry.status_code}
                    </span>
                  )}
                </td>
                <td className="py-1 pr-1 text-terminal-muted font-mono break-all">
                  {entry.payload_digest ?? '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {hasMore && entries.length > 0 && (
        <button
          type="button"
          data-testid="dev-audit-more"
          onClick={() => void loadMore()}
          disabled={loadingMore}
          className="mt-2 self-start text-[10px] font-semibold uppercase tracking-wider text-terminal-muted hover:text-terminal-accent disabled:opacity-50 transition-colors"
        >
          {loadingMore ? t('dev.loadingMore') : t('dev.loadMore')}
        </button>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Quickstart — snippets + Swagger + bot pointer
// ---------------------------------------------------------------------------
// SSR-safe origin: the static-export prerender uses the localhost default
// (server snapshot); in the browser the snapshot is the live location.origin.
const emptySubscribe = () => () => {};
function useOrigin(): string {
  return useSyncExternalStore(
    emptySubscribe,
    () => window.location?.origin ?? DEFAULT_ORIGIN,
    () => DEFAULT_ORIGIN
  );
}

function QuickstartBlock() {
  const t = useT();
  const origin = useOrigin();

  const snippetClass =
    'text-[10px] leading-relaxed text-terminal-text bg-terminal-bg border border-terminal-border rounded p-2 overflow-x-auto whitespace-pre';

  return (
    <div data-testid="dev-quickstart" className="p-2 flex flex-col gap-2 text-xs">
      <p className="text-terminal-muted leading-relaxed">{t('dev.quickstartIntro')}</p>
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-wider text-terminal-muted mb-1">
          {t('dev.curlTitle')}
        </p>
        <pre data-testid="dev-quickstart-curl" className={snippetClass}>
          {curlSnippet(origin)}
        </pre>
      </div>
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-wider text-terminal-muted mb-1">
          {t('dev.pythonTitle')}
        </p>
        <pre data-testid="dev-quickstart-python" className={snippetClass}>
          {pythonSnippet(origin)}
        </pre>
      </div>
      <a
        href="/api/docs"
        target="_blank"
        rel="noreferrer"
        data-testid="dev-swagger-link"
        className="text-terminal-blue hover:underline"
      >
        {t('dev.swaggerLink')}
      </a>
      <p className="text-terminal-muted leading-relaxed">{t('dev.botHint')}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function DevelopersPage() {
  const t = useT();
  const profile = useMarketProfile();
  const { data, mutate } = useSWR<ApiKeysResponse>(KEYS_KEY, fetcher);
  const keys = data?.keys;

  const [armedRevoke, setArmedRevoke] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  const freeze = async (key: ApiKeyInfo) => {
    setListError(null);
    try {
      const res = await fetch(`${KEYS_KEY}/${key.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ frozen: !key.frozen }),
      });
      if (!res.ok) throw new Error(await errorFrom(res, t('dev.updateFailed')));
      await mutate();
    } catch (e) {
      setListError(e instanceof Error && e.message ? e.message : t('dev.updateFailed'));
    }
  };

  const revoke = async (key: ApiKeyInfo) => {
    if (armedRevoke !== key.id) {
      setArmedRevoke(key.id);
      return;
    }
    setArmedRevoke(null);
    setListError(null);
    try {
      const res = await fetch(`${KEYS_KEY}/${key.id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(await errorFrom(res, t('dev.revokeFailed')));
      await mutate();
    } catch (e) {
      setListError(e instanceof Error && e.message ? e.message : t('dev.revokeFailed'));
    }
  };

  const saveConstraints = async (
    key: ApiKeyInfo,
    payload: ConstraintsPayload
  ): Promise<boolean> => {
    try {
      // Explicit nulls — the API clears a constraint only when the field is
      // present and null (P3 §6), so all three always ship.
      const res = await fetch(`${KEYS_KEY}/${key.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) return false;
      await mutate();
      return true;
    } catch {
      return false;
    }
  };

  return (
    <AppShell>
      <div className="flex gap-4 h-full min-h-0">
        {/* Left: key list + create */}
        <div className="flex-[3] min-w-0 flex flex-col gap-4 min-h-0">
          <section className={`${sectionClass} flex-1`}>
            <h2 className={sectionTitleClass}>{t('dev.keysTitle')}</h2>
            <div data-testid="dev-keys" className="flex-1 min-h-0 overflow-auto p-2">
              {listError && (
                <p data-testid="dev-keys-error" className="mb-2 text-xs text-terminal-down">
                  {listError}
                </p>
              )}
              {keys == null ? (
                <p className="text-xs text-terminal-muted">{t('dev.keysLoading')}</p>
              ) : keys.length === 0 ? (
                <p className="text-xs text-terminal-muted">{t('dev.keysEmpty')}</p>
              ) : (
                <table className="w-full text-xs border-collapse">
                  <thead>
                    <tr className="text-terminal-muted border-b border-terminal-border">
                      <th className="text-left py-1 pl-1 font-semibold">{t('dev.colLabel')}</th>
                      <th className="text-left py-1 font-semibold">{t('dev.colPrefix')}</th>
                      <th className="text-left py-1 font-semibold">{t('dev.colCreated')}</th>
                      <th className="text-left py-1 font-semibold">{t('dev.colLastUsed')}</th>
                      <th className="text-left py-1 font-semibold">
                        {t('dev.colConstraints')}
                      </th>
                      <th className="text-left py-1 font-semibold">{t('dev.colStatus')}</th>
                      <th className="text-right py-1 pr-1 font-semibold">
                        {t('dev.colActions')}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {keys.map((key) => (
                      <KeyRow
                        key={key.id}
                        apiKey={key}
                        locale={profile.locale}
                        armedRevoke={armedRevoke === key.id}
                        onFreeze={freeze}
                        onRevoke={revoke}
                        onSave={saveConstraints}
                      />
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </section>

          <section className={`${sectionClass} shrink-0`}>
            <h2 className={sectionTitleClass}>{t('dev.createTitle')}</h2>
            <CreateKeyBlock onCreated={() => mutate()} />
          </section>
        </div>

        {/* Right: audit + quickstart */}
        <div className="flex-[2] min-w-0 flex flex-col gap-4 min-h-0">
          <section className={`${sectionClass} flex-1 max-h-[55%]`}>
            <h2 className={sectionTitleClass}>{t('dev.auditTitle')}</h2>
            <div className="flex-1 min-h-0 overflow-auto">
              <AuditBlock keys={keys ?? []} />
            </div>
          </section>

          <section className={`${sectionClass} flex-1`}>
            <h2 className={sectionTitleClass}>{t('dev.quickstartTitle')}</h2>
            <div className="flex-1 min-h-0 overflow-auto">
              <QuickstartBlock />
            </div>
          </section>
        </div>
      </div>
    </AppShell>
  );
}
