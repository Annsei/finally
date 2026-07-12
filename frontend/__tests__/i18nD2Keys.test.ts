/**
 * i18nD2Keys.test.ts — D2 dictionary coverage (D2 §5).
 *
 * The arena.comp* competition namespace and the analytics risk keys
 * (analytics.var / analytics.beta / analytics.riskHint + companions) must be
 * keyset-aligned between en and zh, and the zh risk hint must carry the
 * contract copy「同步历史数据后可用」.
 */
import { DICTIONARIES, translate } from '@/lib/i18n';

const en = DICTIONARIES.en;
const zh = DICTIONARIES.zh;

const keysWithPrefix = (dict: Readonly<Record<string, string>>, prefix: string) =>
  Object.keys(dict)
    .filter((k) => k.startsWith(prefix))
    .sort();

describe('i18n D2 keyset alignment (en ↔ zh)', () => {
  it.each(['arena.comp', 'analytics.'])('namespace %s is non-empty and keyset-identical', (ns) => {
    const enKeys = keysWithPrefix(en, ns);
    expect(enKeys.length).toBeGreaterThan(0);
    expect(keysWithPrefix(zh, ns)).toEqual(enKeys);
  });

  it('covers every key the competitions section renders', () => {
    for (const key of [
      'arena.compTitle',
      'arena.compNamePlaceholder',
      'arena.compNameAria',
      'arena.compHoursAria',
      'arena.compHoursUnit',
      'arena.compCreate',
      'arena.compCreating',
      'arena.compCreateFailed',
      'arena.compErrName',
      'arena.compErrHours',
      'arena.compErrCode',
      'arena.compCodeLabel',
      'arena.compCopy',
      'arena.compCopied',
      'arena.compCopyFailed',
      'arena.compJoinPlaceholder',
      'arena.compJoinAria',
      'arena.compJoin',
      'arena.compJoining',
      'arena.compJoinFailed',
      'arena.compLoading',
      'arena.compListEmpty',
      'arena.compMembers',
      'arena.compFinal',
      'arena.compStatus.upcoming',
      'arena.compStatus.running',
      'arena.compStatus.ended',
      'arena.compColRank',
      'arena.compColTrader',
      'arena.compColValue',
      'arena.compColReturn',
      'arena.compBoardLoading',
      'arena.compBoardEmpty',
    ]) {
      expect(en[key]).toBeDefined();
      expect(zh[key]).toBeDefined();
    }
  });

  it('the zh risk hint carries the contract copy and both risk cards are labeled', () => {
    expect(translate('zh', 'analytics.riskHint')).toContain('同步历史数据后可用');
    expect(translate('en', 'analytics.riskHint')).toContain('syncing historical data');
    expect(en['analytics.var']).toBeDefined();
    expect(en['analytics.beta']).toBeDefined();
    expect(zh['analytics.var']).toBeDefined();
    expect(zh['analytics.beta']).toBeDefined();
  });

  it('competition copy interpolates and differs between the languages', () => {
    expect(translate('en', 'arena.compMembers', { n: 3 })).toBe('3 traders');
    expect(translate('zh', 'arena.compMembers', { n: 3 })).toBe('3 人');
    expect(translate('zh', 'arena.compStatus.running')).toBe('进行中');
    expect(translate('zh', 'arena.compJoin')).not.toBe(translate('en', 'arena.compJoin'));
    // riskWindow badge count
    expect(translate('en', 'analytics.riskWindow', { n: 60 })).toBe('60 bars');
    expect(translate('zh', 'analytics.riskWindow', { n: 60 })).toBe('60 根');
  });
});
