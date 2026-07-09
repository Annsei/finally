/**
 * i18nDevKeys.test.ts — P3 §8 dictionary additions.
 *
 * Script-style assertions that the dev.* namespace exists, is keyset-aligned
 * between en and zh, and carries the contract-mandated copy: the developers
 * nav label, the shown-only-once secret warning, the two-click revoke
 * confirm, and the interpolated constraint-summary templates.
 */
import { DICTIONARIES, translate } from '@/lib/i18n';

const en = DICTIONARIES.en;
const zh = DICTIONARIES.zh;

const keysIn = (dict: Readonly<Record<string, string>>, prefix: string) =>
  Object.keys(dict)
    .filter((k) => k.startsWith(prefix))
    .sort();

describe('i18n P3 dev.* keyset alignment (en ↔ zh)', () => {
  it('namespace dev. is non-empty and keyset-identical', () => {
    const enKeys = keysIn(en, 'dev.');
    const zhKeys = keysIn(zh, 'dev.');
    expect(enKeys.length).toBeGreaterThan(0);
    expect(zhKeys).toEqual(enKeys);
  });

  it('every dev. value is a non-empty string in both dictionaries', () => {
    for (const key of keysIn(en, 'dev.')) {
      expect(en[key].length).toBeGreaterThan(0);
      expect(zh[key].length).toBeGreaterThan(0);
    }
  });

  it('nav.developers exists in both dictionaries with the contract labels', () => {
    expect(translate('en', 'nav.developers')).toBe('Developers');
    expect(translate('zh', 'nav.developers')).toBe('开发者');
  });

  it('one-time secret warning and revoke confirm are present and translated', () => {
    for (const key of ['dev.secretWarning', 'dev.confirmRevoke', 'dev.keysEmpty']) {
      expect(en[key]).toBeDefined();
      expect(zh[key]).toBeDefined();
      expect(zh[key]).not.toBe(en[key]);
    }
    // the warning must state the shown-only-once semantics (P3 §8)
    expect(en['dev.secretWarning']).toMatch(/only once/i);
    expect(zh['dev.secretWarning']).toContain('仅显示一次');
  });

  it('constraint summary templates interpolate params in both languages', () => {
    expect(translate('en', 'dev.constraintTickers', { list: 'AAPL,MSFT' })).toBe(
      'Tickers: AAPL,MSFT'
    );
    expect(translate('en', 'dev.constraintMaxQty', { qty: 10 })).toBe('Max qty 10');
    expect(translate('en', 'dev.constraintDailyCap', { n: 5 })).toBe('5 trades/day');
    expect(translate('zh', 'dev.constraintTickers', { list: 'AAPL' })).toBe('标的: AAPL');
    expect(translate('zh', 'dev.constraintDailyCap', { n: 5 })).toBe('每日 5 笔');
  });
});
