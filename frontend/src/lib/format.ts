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
