/**
 * marketProfile.ts — runtime market configuration (FinAlly-CN, CN-3 §1)
 *
 * The backend exposes GET /api/market/profile (CN-1). The default `us` market
 * needs NO server round-trip to behave correctly: while the SWR request is in
 * flight (or fails), `useMarketProfile()` returns the US defaults, so every
 * existing US-market behaviour is preserved byte-for-byte.
 *
 * Field names mirror the CN-1 endpoint response exactly. The endpoint also
 * returns stamp/commission fields; they are not consumed by the frontend and
 * are simply ignored by the typed accessor below.
 */
import useSWR from 'swr';
import { fetcher } from '@/lib/fetcher';

export interface MarketProfile {
  market: string; // "us" | "cn"
  currency_symbol: string; // "$" | "¥"
  locale: string; // "en-US" | "zh-CN"
  lot_size: number; // 1 | 100 (整手买入)
  t_plus: number; // 0 | 1 (T+1 锁仓)
  up_is_red: boolean; // false | true (红涨绿跌)
  seed_cash: number; // 10000 | 100000
  midday_break: boolean; // false | true (午间休市)
  names: Record<string, string>; // { code: 名称 } — {} for us
  price_limit_pct: Record<string, number>; // { code: pct } — {} for us
}

// US defaults — the source of truth for "loading" and "failed" states. Keeping
// these here (not derived from the endpoint) guarantees the US market renders
// identically whether or not the profile request ever resolves.
export const US_PROFILE: MarketProfile = {
  market: 'us',
  currency_symbol: '$',
  locale: 'en-US',
  lot_size: 1,
  t_plus: 0,
  up_is_red: false,
  seed_cash: 10000,
  midday_break: false,
  names: {},
  price_limit_pct: {},
};

/**
 * Merge a (partial) server response over the US defaults. Unknown/missing
 * fields fall back to US, so a malformed payload can never flip colours,
 * currency, or lot size away from the safe default.
 */
export function resolveProfile(data: Partial<MarketProfile> | undefined | null): MarketProfile {
  if (!data || typeof data !== 'object') return US_PROFILE;
  return {
    market: typeof data.market === 'string' ? data.market : US_PROFILE.market,
    currency_symbol:
      typeof data.currency_symbol === 'string' ? data.currency_symbol : US_PROFILE.currency_symbol,
    locale: typeof data.locale === 'string' ? data.locale : US_PROFILE.locale,
    lot_size: typeof data.lot_size === 'number' ? data.lot_size : US_PROFILE.lot_size,
    t_plus: typeof data.t_plus === 'number' ? data.t_plus : US_PROFILE.t_plus,
    up_is_red: typeof data.up_is_red === 'boolean' ? data.up_is_red : US_PROFILE.up_is_red,
    seed_cash: typeof data.seed_cash === 'number' ? data.seed_cash : US_PROFILE.seed_cash,
    midday_break:
      typeof data.midday_break === 'boolean' ? data.midday_break : US_PROFILE.midday_break,
    names: data.names && typeof data.names === 'object' ? data.names : US_PROFILE.names,
    price_limit_pct:
      data.price_limit_pct && typeof data.price_limit_pct === 'object'
        ? data.price_limit_pct
        : US_PROFILE.price_limit_pct,
  };
}

/**
 * SWR-backed runtime profile. Returns US defaults until the profile resolves,
 * so components that never mock the profile stay on the US path.
 */
export function useMarketProfile(): MarketProfile {
  const { data } = useSWR<Partial<MarketProfile>>('/api/market/profile', fetcher, {
    revalidateOnFocus: false,
    revalidateOnReconnect: false,
  });
  return resolveProfile(data);
}

/**
 * Stamp the active market onto <html data-market="…"> so the CSS-variable
 * colour flip (globals.css) engages. `us` is the CSS default, so setting it is
 * a harmless no-op visually.
 */
export function applyMarketAttr(market: string): void {
  if (typeof document !== 'undefined' && document.documentElement) {
    document.documentElement.setAttribute('data-market', market);
  }
}

/**
 * Direction hex pair for canvas charts (lightweight-charts can't read CSS
 * variables). US → green up / red down; CN (up_is_red) → the two swap.
 */
export function directionColors(upIsRed: boolean): { up: string; down: string } {
  return upIsRed ? { up: '#ef4444', down: '#22c55e' } : { up: '#22c55e', down: '#ef4444' };
}
