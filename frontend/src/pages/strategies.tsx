/**
 * strategies.tsx — /strategies strategy center (P2 §8). Exported statically as
 * strategies/index.html (trailingSlash: true).
 *
 * Sections:
 *   template-card-${key}   six fixed templates (GET /api/strategies/templates);
 *                           names/descriptions render via i18n
 *                           strategy.template.{key}.name/.desc; clicking a card
 *                           prefills the builder form
 *   strategy-form           create form: name / ticker (datalist) / template
 *                           select / condition-row builder (all|any, ≤5 rows,
 *                           field-driven op/value/params inputs) / four exits /
 *                           sizing mode toggle → POST /api/strategies
 *   strategy-row-${id}      list rows: name, SymbolLink, status chip
 *                           strategy-status-${id}, realized P&L (formatMoney +
 *                           direction colour), runs_count, lifecycle toggle
 *                           strategy-toggle-${id}, details link → /strategy?id=
 *
 * Pure helpers (exported for jest): FIELD_SPECS, defaultRow, rowsToGroup,
 * groupToRows, validateStrategyForm.
 */
import { useState } from 'react';
import Link from 'next/link';
import useSWR from 'swr';
import AppShell from '@/components/AppShell';
import SymbolLink from '@/components/SymbolLink';
import { fetcher } from '@/lib/fetcher';
import { useMarketProfile } from '@/lib/marketProfile';
import { useT, type TFunction } from '@/lib/i18n';
import { formatMoney } from '@/lib/format';
import { TICKER_DIRECTORY } from '@/lib/tickers';
import type {
  StrategiesResponse,
  Strategy,
  StrategyCondition,
  StrategyConditionGroup,
  StrategyExits,
  StrategySizing,
  StrategyStatus,
  StrategyTemplate,
  StrategyTemplatesResponse,
} from '@/types/market';

// ---------------------------------------------------------------------------
// Condition builder registry — mirrors the backend whitelist (P2 §2). The
// builder constrains inputs to the registry so a well-formed form can never
// produce an unknown field / op / params shape.
// ---------------------------------------------------------------------------
export interface FieldParamSpec {
  name: 'period' | 'fast' | 'slow' | 'minutes';
  def: number;
  min: number;
  max: number;
}

export interface FieldSpec {
  value: 'required' | 'optional' | 'none';
  valueDef: string;
  params: FieldParamSpec[];
  opDef: 'above' | 'below';
  // window_high is a breakout (above-only) and window_low a breakdown
  // (below-only; `above` is a backend 400) — the builder locks their op.
  lockedOp?: 'above' | 'below';
  // Numeric bounds for `value`, mirroring the backend value rules
  // (indicators.FIELD_SPECS): `gt` is an exclusive lower bound
  // (required_positive), `min`/`max` are inclusive (required_0_100).
  valueRange?: { gt?: number; min?: number; max?: number };
}

export const FIELD_SPECS: Record<string, FieldSpec> = {
  price: { value: 'required', valueDef: '', params: [], opDef: 'above', valueRange: { gt: 0 } },
  day_change_pct: { value: 'required', valueDef: '-2', params: [], opDef: 'below' },
  ma: {
    value: 'optional',
    valueDef: '0',
    params: [{ name: 'period', def: 20, min: 2, max: 120 }],
    opDef: 'above',
  },
  ma_cross: {
    value: 'none',
    valueDef: '',
    params: [
      { name: 'fast', def: 5, min: 2, max: 120 },
      { name: 'slow', def: 20, min: 2, max: 120 },
    ],
    opDef: 'above',
  },
  ema_cross: {
    value: 'none',
    valueDef: '',
    params: [
      { name: 'fast', def: 5, min: 2, max: 120 },
      { name: 'slow', def: 20, min: 2, max: 120 },
    ],
    opDef: 'above',
  },
  rsi: {
    value: 'required',
    valueDef: '30',
    params: [{ name: 'period', def: 14, min: 2, max: 50 }],
    opDef: 'below',
    valueRange: { min: 0, max: 100 },
  },
  window_high: {
    value: 'none',
    valueDef: '',
    params: [{ name: 'minutes', def: 60, min: 5, max: 240 }],
    opDef: 'above',
    lockedOp: 'above',
  },
  window_low: {
    value: 'none',
    valueDef: '',
    params: [{ name: 'minutes', def: 60, min: 5, max: 240 }],
    opDef: 'below',
    lockedOp: 'below',
  },
  pullback_from_high_pct: {
    value: 'required',
    valueDef: '2',
    params: [{ name: 'minutes', def: 60, min: 5, max: 240 }],
    opDef: 'above',
    valueRange: { gt: 0 },
  },
};

export const MAX_CONDITIONS = 5;

// max_holding_days bounds — mirrors the backend exits whitelist
// (indicators.validate_exits: an integer 1..120).
export const MAX_HOLDING_DAYS_MIN = 1;
export const MAX_HOLDING_DAYS_MAX = 120;

export interface ConditionRowState {
  field: string;
  op: 'above' | 'below';
  value: string;
  params: Record<string, string>;
}

/** A fresh builder row with the field's registry defaults. */
export function defaultRow(field = 'day_change_pct'): ConditionRowState {
  const spec = FIELD_SPECS[field] ?? FIELD_SPECS.price;
  return {
    field,
    op: spec.lockedOp ?? spec.opDef,
    value: spec.valueDef,
    params: Object.fromEntries(spec.params.map((p) => [p.name, String(p.def)])),
  };
}

/**
 * Effective numeric value of one param input. A cleared input must fall back
 * to the registry default — note Number('') === 0 is finite, so an emptiness
 * check has to come first — and typed values are rounded (backend params are
 * integers) and clamped to [min, max]: the builder must never produce a
 * payload the backend rejects with a 400 (P2 §8). Shared by rowsToGroup and
 * validateStrategyForm so the fast/slow gate sees exactly what is submitted.
 */
function effectiveParam(p: FieldParamSpec, raw: string | undefined): number {
  const rawText = (raw ?? '').trim();
  const parsed = rawText === '' ? Number.NaN : Number(rawText);
  const value = Number.isFinite(parsed) ? Math.round(parsed) : p.def;
  return Math.min(p.max, Math.max(p.min, value));
}

/** Builder rows → the declarative condition-group payload (P2 §2 shape). */
export function rowsToGroup(
  mode: 'all' | 'any',
  rows: ConditionRowState[]
): StrategyConditionGroup {
  const conditions: StrategyCondition[] = rows.map((row) => {
    const spec = FIELD_SPECS[row.field];
    const cond: StrategyCondition = { field: row.field, op: row.op };
    if (spec && spec.value !== 'none' && row.value.trim() !== '') {
      cond.value = Number(row.value);
    }
    if (spec && spec.params.length > 0) {
      cond.params = Object.fromEntries(
        spec.params.map((p) => [p.name, effectiveParam(p, row.params[p.name])])
      );
    }
    return cond;
  });
  return mode === 'all' ? { all: conditions } : { any: conditions };
}

/** Condition-group payload → builder rows (template prefill). */
export function groupToRows(group: StrategyConditionGroup): {
  mode: 'all' | 'any';
  rows: ConditionRowState[];
} {
  const mode: 'all' | 'any' = 'all' in group ? 'all' : 'any';
  const conditions = ('all' in group ? group.all : group.any) ?? [];
  const rows = conditions.map((cond) => {
    const spec = FIELD_SPECS[cond.field] ?? FIELD_SPECS.price;
    return {
      field: cond.field,
      op: spec.lockedOp ?? cond.op,
      value: cond.value != null ? String(cond.value) : spec.valueDef,
      params: Object.fromEntries(
        spec.params.map((p) => [p.name, String(cond.params?.[p.name] ?? p.def)])
      ),
    };
  });
  return { mode, rows: rows.length > 0 ? rows : [defaultRow()] };
}

/** Raw exit-input strings as typed into the form ('' = unset). */
export interface ExitsFormState {
  takeProfit: string;
  stopLoss: string;
  trailing: string;
  maxDays: string;
}

/** Raw sizing-input strings as typed into the form. */
export interface SizingFormState {
  mode: 'fixed_qty' | 'cash_pct';
  qty: string;
  pct: string;
}

/**
 * Client-side form gate — returns the i18n error key, or null when valid.
 * Mirrors the backend whitelist bounds (P2 §2) so a passing form can never
 * draw a 400: per-field value ranges (FIELD_SPECS.valueRange), fast < slow
 * for the cross fields, exits > 0 with max_holding_days an integer 1..120,
 * and sizing fixed_qty > 0 / cash_pct 1..100. Note Number('') === 0, so
 * every bound check trims and rejects emptiness first — a cleared sizing
 * input must not collapse to 0 and slip through.
 */
export function validateStrategyForm(
  name: string,
  ticker: string,
  rows: ConditionRowState[],
  exits?: ExitsFormState,
  sizing?: SizingFormState,
  lotSize = 1
): string | null {
  const trimmedName = name.trim();
  if (trimmedName.length < 1 || trimmedName.length > 40) return 'strategy.errName';
  if (ticker.trim() === '') return 'strategy.errTicker';
  for (const row of rows) {
    const spec = FIELD_SPECS[row.field];
    if (!spec) return 'strategy.errValue';
    const valueText = row.value.trim();
    if (spec.value === 'required' && (valueText === '' || !Number.isFinite(Number(valueText)))) {
      return 'strategy.errValue';
    }
    if (spec.value === 'optional' && valueText !== '' && !Number.isFinite(Number(valueText))) {
      return 'strategy.errValue';
    }
    if (spec.value !== 'none' && valueText !== '' && spec.valueRange) {
      const value = Number(valueText);
      const range = spec.valueRange;
      if (range.gt != null && !(value > range.gt)) return 'strategy.errValueRange';
      if (range.min != null && value < range.min) return 'strategy.errValueRange';
      if (range.max != null && value > range.max) return 'strategy.errValueRange';
    }
    // Cross fields: the submitted (defaulted/clamped) fast must stay < slow —
    // both can sit inside [min, max] and still be an inverted, backend-400 pair.
    const fastSpec = spec.params.find((p) => p.name === 'fast');
    const slowSpec = spec.params.find((p) => p.name === 'slow');
    if (fastSpec && slowSpec) {
      const fast = effectiveParam(fastSpec, row.params[fastSpec.name]);
      const slow = effectiveParam(slowSpec, row.params[slowSpec.name]);
      if (fast >= slow) return 'strategy.errFastSlow';
    }
  }
  if (exits) {
    for (const raw of [exits.takeProfit, exits.stopLoss, exits.trailing]) {
      const text = raw.trim();
      if (text === '') continue; // unset is legal — exits are all optional
      const value = Number(text);
      if (!Number.isFinite(value) || value <= 0) return 'strategy.errExits';
    }
    const daysText = exits.maxDays.trim();
    if (daysText !== '') {
      const days = Number(daysText);
      if (
        !Number.isInteger(days) ||
        days < MAX_HOLDING_DAYS_MIN ||
        days > MAX_HOLDING_DAYS_MAX
      ) {
        return 'strategy.errExits';
      }
    }
  }
  if (sizing) {
    if (sizing.mode === 'fixed_qty') {
      const text = sizing.qty.trim();
      const qty = Number(text);
      if (text === '' || !Number.isFinite(qty) || qty <= 0) return 'strategy.errSizing';
      if (lotSize > 1 && (!Number.isInteger(qty) || qty % lotSize !== 0)) {
        return 'strategy.errWholeLot';
      }
    } else {
      const text = sizing.pct.trim();
      const pct = Number(text);
      if (text === '' || !Number.isFinite(pct) || pct < 1 || pct > 100) {
        return 'strategy.errSizing';
      }
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Small display helpers
// ---------------------------------------------------------------------------
const STATUS_CHIP: Record<StrategyStatus, string> = {
  draft: 'text-terminal-muted border-terminal-border',
  live: 'text-terminal-up border-terminal-up/60',
  paused: 'text-terminal-amber border-terminal-amber/60',
  archived: 'text-terminal-muted border-terminal-border',
};

function StatusChip({ id, status, t }: { id: string; status: StrategyStatus; t: TFunction }) {
  return (
    <span
      data-testid={`strategy-status-${id}`}
      className={`text-[9px] font-semibold px-1 py-0.5 rounded border uppercase tracking-wider ${
        STATUS_CHIP[status] ?? STATUS_CHIP.draft
      }`}
    >
      {t(`strategy.status.${status}`)}
    </span>
  );
}

const inputClass =
  'px-2 py-1.5 text-xs font-mono bg-terminal-bg border border-terminal-border text-terminal-text rounded focus:outline-none focus:border-terminal-blue tabular-nums placeholder:text-terminal-muted disabled:opacity-50';
const labelClass = 'text-xs font-semibold text-terminal-muted uppercase tracking-wider';
const sectionClass =
  'border border-terminal-border rounded bg-terminal-surface/30 flex flex-col min-h-0';
const sectionTitleClass =
  'px-2 py-1.5 text-xs font-semibold text-terminal-muted uppercase tracking-wider border-b border-terminal-border shrink-0';

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function StrategiesPage() {
  const t = useT();
  const profile = useMarketProfile();
  const money = { currency_symbol: profile.currency_symbol, locale: profile.locale };

  const { data: templatesData } = useSWR<StrategyTemplatesResponse>(
    '/api/strategies/templates',
    fetcher
  );
  const templates = templatesData?.templates ?? [];

  const { data: listData, mutate: mutateList } = useSWR<StrategiesResponse>(
    '/api/strategies',
    fetcher,
    { refreshInterval: 5000 }
  );
  const strategies = listData?.strategies ?? [];

  // --- form state ---------------------------------------------------------
  const [name, setName] = useState('');
  const [tickerInput, setTicker] = useState<string | null>(null);
  const ticker = tickerInput ?? Object.keys(profile.names)[0] ?? '';
  const [template, setTemplate] = useState<string>('');
  const [mode, setMode] = useState<'all' | 'any'>('all');
  const [rows, setRows] = useState<ConditionRowState[]>([defaultRow()]);
  const [takeProfit, setTakeProfit] = useState('');
  const [stopLoss, setStopLoss] = useState('');
  const [trailing, setTrailing] = useState('');
  const [maxDays, setMaxDays] = useState('');
  const [sizingMode, setSizingMode] = useState<'fixed_qty' | 'cash_pct'>('fixed_qty');
  // null means "use the active market's board-lot default". Unlike a plain
  // '1' initializer this updates correctly when the CN profile resolves.
  const [sizingQty, setSizingQty] = useState<string | null>(null);
  const [sizingPct, setSizingPct] = useState('20');
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  const applyTemplate = (tpl: StrategyTemplate) => {
    setTemplate(tpl.key);
    setName(t(`strategy.template.${tpl.key}.name`));
    if (tpl.ticker_hint) setTicker(tpl.ticker_hint.toUpperCase());
    const { mode: tplMode, rows: tplRows } = groupToRows(tpl.entry);
    setMode(tplMode);
    setRows(tplRows);
    const exits: StrategyExits = tpl.exits ?? {};
    setTakeProfit(exits.take_profit_pct != null ? String(exits.take_profit_pct) : '');
    setStopLoss(exits.stop_loss_pct != null ? String(exits.stop_loss_pct) : '');
    setTrailing(exits.trailing_stop_pct != null ? String(exits.trailing_stop_pct) : '');
    setMaxDays(exits.max_holding_days != null ? String(exits.max_holding_days) : '');
    const sizing = tpl.sizing;
    if (sizing.mode === 'cash_pct') {
      setSizingMode('cash_pct');
      setSizingPct(String(sizing.pct));
    } else {
      setSizingMode('fixed_qty');
      setSizingQty(String(sizing.qty));
    }
    setFormError(null);
  };

  const updateRow = (index: number, patch: Partial<ConditionRowState>) => {
    setRows((prev) => prev.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  };

  const changeRowField = (index: number, field: string) => {
    setRows((prev) => prev.map((row, i) => (i === index ? defaultRow(field) : row)));
  };

  const addRow = () => {
    setRows((prev) => (prev.length >= MAX_CONDITIONS ? prev : [...prev, defaultRow()]));
  };

  const removeRow = (index: number) => {
    setRows((prev) => (prev.length <= 1 ? prev : prev.filter((_, i) => i !== index)));
  };

  const submit = async () => {
    const effectiveSizingQty = sizingQty ?? String(profile.lot_size);
    const errKey = validateStrategyForm(
      name,
      ticker,
      rows,
      { takeProfit, stopLoss, trailing, maxDays },
      { mode: sizingMode, qty: effectiveSizingQty, pct: sizingPct },
      profile.lot_size
    );
    if (errKey) {
      setFormError(t(errKey));
      return;
    }
    const exits: StrategyExits = {
      take_profit_pct: takeProfit.trim() === '' ? null : Number(takeProfit),
      stop_loss_pct: stopLoss.trim() === '' ? null : Number(stopLoss),
      trailing_stop_pct: trailing.trim() === '' ? null : Number(trailing),
      max_holding_days: maxDays.trim() === '' ? null : Number(maxDays),
    };
    const sizing: StrategySizing =
      sizingMode === 'cash_pct'
        ? { mode: 'cash_pct', pct: Number(sizingPct) }
        : { mode: 'fixed_qty', qty: Number(effectiveSizingQty) };

    setCreating(true);
    setFormError(null);
    try {
      const res = await fetch('/api/strategies', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name.trim(),
          ticker: ticker.trim().toUpperCase(),
          entry: rowsToGroup(mode, rows),
          exits,
          sizing,
          ...(template !== '' ? { template } : {}),
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error ?? `${t('strategy.createFailed')} (${res.status})`);
      }
      setName('');
      await mutateList();
    } catch (e) {
      setFormError(e instanceof Error ? e.message : t('strategy.createFailed'));
    } finally {
      setCreating(false);
    }
  };

  // List lifecycle toggle: draft → live, live → paused, paused → live.
  const toggleStatus = async (strategy: Strategy) => {
    const next: StrategyStatus | null =
      strategy.status === 'draft' || strategy.status === 'paused'
        ? 'live'
        : strategy.status === 'live'
          ? 'paused'
          : null;
    if (!next) return;
    setListError(null);
    try {
      const res = await fetch(`/api/strategies/${strategy.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: next }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error ?? `${res.status}`);
      }
      await mutateList();
    } catch (e) {
      setListError(e instanceof Error ? e.message : String(e));
    }
  };

  const toggleLabel = (status: StrategyStatus) =>
    status === 'live'
      ? t('strategy.pause')
      : status === 'paused'
        ? t('strategy.resume')
        : t('strategy.deploy');

  // Ticker autocomplete: profile names on named markets (cn), else the static
  // US directory (same source as the desk's shared datalist).
  const tickerOptions =
    Object.keys(profile.names).length > 0
      ? Object.entries(profile.names).map(([code, label]) => ({ code, label }))
      : TICKER_DIRECTORY.map((info) => ({ code: info.symbol, label: info.name }));

  const opChoices: ('above' | 'below')[] = ['above', 'below'];

  return (
    <AppShell>
      <div className="flex flex-col gap-3 h-full min-h-0 overflow-auto">
        <h1 className="text-xl font-semibold text-terminal-text tracking-wide shrink-0">
          {t('strategy.title')}
        </h1>

        {/* Templates */}
        <section className={sectionClass}>
          <h2 className={sectionTitleClass}>{t('strategy.templatesTitle')}</h2>
          <div className="p-2 grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-2">
            {templates.map((tpl) => (
              <button
                type="button"
                key={tpl.key}
                data-testid={`template-card-${tpl.key}`}
                onClick={() => applyTemplate(tpl)}
                className="text-left px-2 py-2 rounded border border-terminal-border bg-terminal-bg hover:border-terminal-blue transition-colors"
              >
                <span className="block text-xs font-semibold text-terminal-text">
                  {t(`strategy.template.${tpl.key}.name`)}
                </span>
                <span className="block mt-1 text-[10px] text-terminal-muted leading-tight">
                  {t(`strategy.template.${tpl.key}.desc`)}
                </span>
              </button>
            ))}
          </div>
        </section>

        {/* Builder form */}
        <section className={sectionClass}>
          <h2 className={sectionTitleClass}>{t('strategy.formTitle')}</h2>
          <form
            data-testid="strategy-form"
            className="p-2 flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              void submit();
            }}
          >
            <div className="flex items-end gap-2 flex-wrap">
              <div className="flex flex-col gap-1">
                <label htmlFor="st-name" className={labelClass}>
                  {t('strategy.name')}
                </label>
                <input
                  id="st-name"
                  type="text"
                  maxLength={40}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  disabled={creating}
                  className={`w-44 ${inputClass}`}
                />
              </div>
              <div className="flex flex-col gap-1">
                <label htmlFor="st-ticker" className={labelClass}>
                  {t('strategy.ticker')}
                </label>
                <input
                  id="st-ticker"
                  type="text"
                  list="strategy-ticker-suggestions"
                  value={ticker}
                  onChange={(e) => setTicker(e.target.value.toUpperCase())}
                  disabled={creating}
                  className={`w-24 ${inputClass}`}
                />
              </div>
              <div className="flex flex-col gap-1">
                <label htmlFor="st-template" className={labelClass}>
                  {t('strategy.template')}
                </label>
                <select
                  id="st-template"
                  data-testid="strategy-template-select"
                  value={template}
                  onChange={(e) => {
                    const key = e.target.value;
                    const tpl = templates.find((candidate) => candidate.key === key);
                    if (tpl) applyTemplate(tpl);
                    else setTemplate('');
                  }}
                  disabled={creating}
                  className={inputClass}
                >
                  <option value="">{t('strategy.templateCustom')}</option>
                  {templates.map((tpl) => (
                    <option key={tpl.key} value={tpl.key}>
                      {t(`strategy.template.${tpl.key}.name`)}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* Entry condition builder */}
            <div className="flex flex-col gap-1.5">
              <div className="flex items-center gap-2">
                <span className={labelClass}>{t('strategy.entryTitle')}</span>
                {(['all', 'any'] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    data-testid={`strategy-entry-mode-${m}`}
                    onClick={() => setMode(m)}
                    className={`px-1.5 py-0.5 rounded text-[10px] font-semibold transition-colors ${
                      mode === m
                        ? 'bg-terminal-bg text-terminal-text border border-terminal-blue'
                        : 'text-terminal-muted border border-terminal-border hover:text-terminal-text'
                    }`}
                  >
                    {t(`strategy.cond.${m}`)}
                  </button>
                ))}
              </div>

              {rows.map((row, i) => {
                const spec = FIELD_SPECS[row.field] ?? FIELD_SPECS.price;
                return (
                  <div
                    key={i}
                    data-testid={`condition-row-${i}`}
                    className="flex items-center gap-2 flex-wrap"
                  >
                    <select
                      data-testid={`condition-field-${i}`}
                      aria-label={t('strategy.entryTitle')}
                      value={row.field}
                      onChange={(e) => changeRowField(i, e.target.value)}
                      disabled={creating}
                      className={inputClass}
                    >
                      {Object.keys(FIELD_SPECS).map((field) => (
                        <option key={field} value={field}>
                          {t(`strategy.cond.field.${field}`)}
                        </option>
                      ))}
                    </select>
                    <select
                      data-testid={`condition-op-${i}`}
                      value={row.op}
                      onChange={(e) => updateRow(i, { op: e.target.value as 'above' | 'below' })}
                      disabled={creating || spec.lockedOp != null}
                      className={inputClass}
                    >
                      {opChoices.map((op) => (
                        <option key={op} value={op}>
                          {t(`strategy.cond.${op}`)}
                        </option>
                      ))}
                    </select>
                    {spec.value !== 'none' && (
                      <input
                        type="number"
                        step="any"
                        data-testid={`condition-value-${i}`}
                        value={row.value}
                        onChange={(e) => updateRow(i, { value: e.target.value })}
                        disabled={creating}
                        className={`w-20 ${inputClass}`}
                      />
                    )}
                    {spec.params.map((param) => (
                      <span key={param.name} className="flex items-center gap-1">
                        <span className="text-[10px] text-terminal-muted">{param.name}</span>
                        <input
                          type="number"
                          step="1"
                          min={param.min}
                          max={param.max}
                          data-testid={`condition-param-${param.name}-${i}`}
                          value={row.params[param.name] ?? String(param.def)}
                          onChange={(e) =>
                            updateRow(i, {
                              params: { ...row.params, [param.name]: e.target.value },
                            })
                          }
                          disabled={creating}
                          className={`w-16 ${inputClass}`}
                        />
                      </span>
                    ))}
                    {rows.length > 1 && (
                      <button
                        type="button"
                        data-testid={`condition-remove-${i}`}
                        onClick={() => removeRow(i)}
                        disabled={creating}
                        className="text-[10px] font-semibold text-terminal-muted hover:text-terminal-down uppercase tracking-wider"
                      >
                        {t('strategy.removeCondition')}
                      </button>
                    )}
                  </div>
                );
              })}

              {rows.length < MAX_CONDITIONS && (
                <button
                  type="button"
                  data-testid="condition-add"
                  onClick={addRow}
                  disabled={creating}
                  className="self-start text-[10px] font-semibold text-terminal-blue hover:underline uppercase tracking-wider"
                >
                  {t('strategy.addCondition')}
                </button>
              )}
            </div>

            {/* Exits + sizing */}
            <div className="flex items-end gap-4 flex-wrap">
              <div className="flex items-end gap-2">
                <span className={`${labelClass} pb-1.5`}>{t('strategy.exitsTitle')}</span>
                {(
                  [
                    ['st-tp', 'strategy.exitTp', takeProfit, setTakeProfit],
                    ['st-sl', 'strategy.exitSl', stopLoss, setStopLoss],
                    ['st-trail', 'strategy.exitTrailing', trailing, setTrailing],
                    ['st-maxdays', 'strategy.exitMaxDays', maxDays, setMaxDays],
                  ] as const
                ).map(([id, key, value, setter]) => (
                  <div key={id} className="flex flex-col gap-1">
                    <label htmlFor={id} className={labelClass}>
                      {t(key)}
                    </label>
                    <input
                      id={id}
                      type="number"
                      min="0"
                      step="any"
                      placeholder="—"
                      value={value}
                      onChange={(e) => setter(e.target.value)}
                      disabled={creating}
                      className={`w-16 ${inputClass}`}
                    />
                  </div>
                ))}
              </div>

              <div className="flex items-end gap-2">
                <span className={`${labelClass} pb-1.5`}>{t('strategy.sizingTitle')}</span>
                {(['fixed_qty', 'cash_pct'] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    data-testid={`sizing-mode-${m}`}
                    onClick={() => setSizingMode(m)}
                    className={`px-1.5 py-1.5 rounded text-[10px] font-semibold transition-colors ${
                      sizingMode === m
                        ? 'bg-terminal-bg text-terminal-text border border-terminal-blue'
                        : 'text-terminal-muted border border-terminal-border hover:text-terminal-text'
                    }`}
                  >
                    {t(m === 'fixed_qty' ? 'strategy.sizingFixed' : 'strategy.sizingCashPct')}
                  </button>
                ))}
                {sizingMode === 'fixed_qty' ? (
                  <input
                    type="number"
                    min="0"
                    step="any"
                    data-testid="sizing-qty"
                    aria-label={t('strategy.sizingFixed')}
                    value={sizingQty ?? String(profile.lot_size)}
                    onChange={(e) => setSizingQty(e.target.value)}
                    disabled={creating}
                    className={`w-20 ${inputClass}`}
                  />
                ) : (
                  <input
                    type="number"
                    min="1"
                    max="100"
                    step="1"
                    data-testid="sizing-pct"
                    aria-label={t('strategy.sizingCashPct')}
                    value={sizingPct}
                    onChange={(e) => setSizingPct(e.target.value)}
                    disabled={creating}
                    className={`w-16 ${inputClass}`}
                  />
                )}
              </div>

              <button
                type="submit"
                data-testid="strategy-create"
                disabled={creating}
                className="px-4 py-1.5 text-xs font-semibold rounded min-h-[36px] text-white disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
                style={{ backgroundColor: '#753991' }}
              >
                {creating ? t('strategy.creating') : t('strategy.create')}
              </button>
            </div>

            {formError && (
              <p data-testid="strategy-form-error" className="text-xs text-terminal-down">
                {formError}
              </p>
            )}
          </form>
          <datalist id="strategy-ticker-suggestions">
            {tickerOptions.map((opt) => (
              <option key={opt.code} value={opt.code}>
                {opt.label}
              </option>
            ))}
          </datalist>
        </section>

        {/* Strategy list */}
        <section className={sectionClass}>
          <h2 className={sectionTitleClass}>{t('strategy.listTitle')}</h2>
          <div className="p-2 overflow-auto min-h-0">
            {listError && (
              <p data-testid="strategy-list-error" className="mb-1.5 text-xs text-terminal-down">
                {listError}
              </p>
            )}
            {strategies.length === 0 ? (
              <p className="text-xs text-terminal-muted">{t('strategy.listEmpty')}</p>
            ) : (
              <table className="w-full text-xs border-collapse">
                <thead>
                  <tr className="text-terminal-muted border-b border-terminal-border">
                    <th className="text-left py-1 pl-1 font-semibold">{t('strategy.colName')}</th>
                    <th className="text-left py-1 font-semibold">{t('strategy.colTicker')}</th>
                    <th className="text-left py-1 font-semibold">{t('strategy.colStatus')}</th>
                    <th className="text-right py-1 font-semibold">{t('strategy.colPnl')}</th>
                    <th className="text-right py-1 font-semibold">{t('strategy.colRuns')}</th>
                    <th className="text-right py-1 pr-1 font-semibold">
                      {t('strategy.colActions')}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {strategies.map((strategy) => {
                    const pnl = strategy.realized_pnl;
                    const pnlColor =
                      pnl === 0
                        ? 'text-terminal-muted'
                        : pnl > 0
                          ? 'text-terminal-up'
                          : 'text-terminal-down';
                    return (
                      <tr
                        key={strategy.id}
                        data-testid={`strategy-row-${strategy.id}`}
                        className="border-b border-terminal-border/60"
                      >
                        <td className="py-1 pl-1 font-semibold text-terminal-text">
                          <Link
                            href={{ pathname: '/strategy', query: { id: strategy.id } }}
                            className="hover:underline"
                          >
                            {strategy.name}
                          </Link>
                        </td>
                        <td className="py-1">
                          <SymbolLink code={strategy.ticker} />
                        </td>
                        <td className="py-1">
                          <StatusChip id={strategy.id} status={strategy.status} t={t} />
                        </td>
                        <td className={`text-right py-1 tabular-nums ${pnlColor}`}>
                          {`${pnl >= 0 ? '+' : '-'}${formatMoney(Math.abs(pnl), money)}`}
                        </td>
                        <td className="text-right py-1 tabular-nums text-terminal-muted">
                          {strategy.runs_count}
                        </td>
                        <td className="text-right py-1 pr-1">
                          <span className="inline-flex items-center gap-2">
                            {strategy.status !== 'archived' && (
                              <button
                                type="button"
                                data-testid={`strategy-toggle-${strategy.id}`}
                                onClick={() => void toggleStatus(strategy)}
                                className="text-[10px] font-semibold uppercase tracking-wider text-terminal-blue hover:underline"
                              >
                                {toggleLabel(strategy.status)}
                              </button>
                            )}
                            <Link
                              href={{ pathname: '/strategy', query: { id: strategy.id } }}
                              data-testid={`strategy-details-${strategy.id}`}
                              className="text-[10px] font-semibold uppercase tracking-wider text-terminal-muted hover:text-terminal-text hover:underline"
                            >
                              {t('strategy.details')}
                            </Link>
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </section>
      </div>
    </AppShell>
  );
}
