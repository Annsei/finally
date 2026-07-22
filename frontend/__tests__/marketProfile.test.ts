/**
 * marketProfile.test.ts (FinAlly-CN, CN-3 §6)
 *
 * The runtime profile must default to US whenever the endpoint hasn't resolved
 * (or returns something unexpected), so every US-market behaviour is preserved.
 */
import { applyMarketAttr, resolveProfile, directionColors, US_PROFILE } from '@/lib/marketProfile';
import { langFromLocale } from '@/lib/i18n';

describe('resolveProfile', () => {
  it('undefined (loading/failed) resolves to the US defaults', () => {
    const p = resolveProfile(undefined);
    expect(p).toEqual(US_PROFILE);
    expect(p.market).toBe('us');
    expect(p.currency_symbol).toBe('$');
    expect(p.locale).toBe('en-US');
    expect(p.lot_size).toBe(1);
    expect(p.up_is_red).toBe(false);
    expect(p.names).toEqual({});
    expect(p.price_limit_pct).toEqual({});
    expect(p.seed_cash).toBe(10000);
  });

  it('parses a CN profile payload from GET /api/market/profile', () => {
    const p = resolveProfile({
      market: 'cn',
      currency_symbol: '¥',
      locale: 'zh-CN',
      lot_size: 100,
      t_plus: 1,
      up_is_red: true,
      seed_cash: 100000,
      midday_break: true,
      names: { '600519': '贵州茅台' },
      price_limit_pct: { '600519': 10 },
    });
    expect(p.market).toBe('cn');
    expect(p.currency_symbol).toBe('¥');
    expect(p.locale).toBe('zh-CN');
    expect(p.lot_size).toBe(100);
    expect(p.up_is_red).toBe(true);
    expect(p.names['600519']).toBe('贵州茅台');
    expect(p.price_limit_pct['600519']).toBe(10);
  });

  it('ignores foreign objects (e.g. a portfolio payload) and stays on US defaults', () => {
    // A shared SWR mock can hand this hook a portfolio payload; none of its
    // fields collide with profile fields, so the safe US defaults hold.
    const p = resolveProfile({ cash: 10000, total_value: 12345, positions: [] } as never);
    expect(p.up_is_red).toBe(false);
    expect(p.lot_size).toBe(1);
    expect(p.currency_symbol).toBe('$');
    expect(p.locale).toBe('en-US');
  });
});

describe('directionColors', () => {
  it('US (up_is_red false) → green up / red down', () => {
    expect(directionColors(false)).toEqual({ up: '#22c55e', down: '#ef4444' });
  });

  it('CN (up_is_red true) → red up / green down', () => {
    expect(directionColors(true)).toEqual({ up: '#ef4444', down: '#22c55e' });
  });
});

describe('langFromLocale', () => {
  it('maps zh-CN → zh and everything else → en', () => {
    expect(langFromLocale('zh-CN')).toBe('zh');
    expect(langFromLocale('en-US')).toBe('en');
    expect(langFromLocale(undefined)).toBe('en');
    expect(langFromLocale(resolveProfile(undefined).locale)).toBe('en');
  });
});

describe('applyMarketAttr', () => {
  it('stamps both the market theme and the document language', () => {
    applyMarketAttr('cn', 'zh-CN');
    expect(document.documentElement.getAttribute('data-market')).toBe('cn');
    expect(document.documentElement.lang).toBe('zh-CN');

    applyMarketAttr('us', 'en-US');
    expect(document.documentElement.lang).toBe('en');
  });
});
