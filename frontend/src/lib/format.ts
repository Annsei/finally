/**
 * format.ts — shared numeric display helpers
 *
 * Share quantities are floats (fractional shares supported) and accumulate
 * IEEE-754 residue across trades (0.1 + 0.1 + 0.1 → 0.30000000000000004).
 * Bound display precision to 4 decimals and trim trailing zeros.
 */
export function formatQuantity(n: number | undefined | null): string {
  if (n === undefined || n === null || !Number.isFinite(n)) return '—';
  return String(parseFloat(n.toFixed(4)));
}

// ---------------------------------------------------------------------------
// Market-aware helpers (FinAlly-CN, CN-3 §4). All ADDITIVE — formatQuantity
// above is untouched so the US share display stays byte-identical.
// ---------------------------------------------------------------------------

/**
 * Currency amount: symbol + locale grouping, 2 decimals. Undefined/NaN → '—'.
 * US: formatMoney(10000, {currency_symbol:'$', locale:'en-US'}) === '$10,000.00'.
 */
export function formatMoney(
  n: number | undefined | null,
  opts: { currency_symbol: string; locale: string }
): string {
  if (n === undefined || n === null || !Number.isFinite(n)) return '—';
  return `${opts.currency_symbol}${n.toLocaleString(opts.locale, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

/**
 * Large counts. zh-CN collapses into 万 (1e4) / 亿 (1e8) with one decimal;
 * every other locale keeps the current grouped display (unchanged behaviour).
 */
export function formatLargeCount(n: number | undefined | null, locale: string): string {
  if (n === undefined || n === null || !Number.isFinite(n)) return '—';
  const zh = typeof locale === 'string' && locale.toLowerCase().startsWith('zh');
  if (zh) {
    const abs = Math.abs(n);
    if (abs >= 1e8) return `${trimTrailingZero((n / 1e8).toFixed(2))}亿`;
    if (abs >= 1e4) return `${trimTrailingZero((n / 1e4).toFixed(2))}万`;
    return String(Math.round(n));
  }
  // en / default — same grouped integer display used across the app today.
  return Math.round(n).toLocaleString(locale);
}

function trimTrailingZero(s: string): string {
  return s.replace(/\.?0+$/, '');
}

/**
 * Share quantity for display. On lot markets (lot_size > 1) integer lots read
 * as「N手」, with any odd-lot remainder appended as「(零股 M)」. US (lot_size 1)
 * reuses formatQuantity, so nothing changes on the US market.
 */
export function formatShares(
  n: number | undefined | null,
  profile: { lot_size: number }
): string {
  if (n === undefined || n === null || !Number.isFinite(n)) return '—';
  const lot = profile?.lot_size ?? 1;
  if (lot <= 1) return formatQuantity(n);
  const lots = Math.floor(n / lot);
  const odd = parseFloat((n - lots * lot).toFixed(4));
  return odd > 0 ? `${lots}手 (零股${formatQuantity(odd)})` : `${lots}手`;
}
