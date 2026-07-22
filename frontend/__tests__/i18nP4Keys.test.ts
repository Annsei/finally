/**
 * i18nP4Keys.test.ts — P4 dictionary coverage (P4 §5).
 *
 * Script-style assertions that the market.sentiment* / market.corr* /
 * journal.cal* / player.* namespaces exist and are keyset-aligned between en
 * and zh, and that the five sentiment band labels carry the contract copy.
 */
import { DICTIONARIES, translate } from '@/lib/i18n';

const en = DICTIONARIES.en;
const zh = DICTIONARIES.zh;

const keysWithPrefix = (dict: Readonly<Record<string, string>>, prefix: string) =>
  Object.keys(dict)
    .filter((k) => k.startsWith(prefix))
    .sort();

describe('i18n P4 keyset alignment (en ↔ zh)', () => {
  it.each(['market.sentiment', 'market.corr', 'journal.cal', 'player.'])(
    'namespace %s is non-empty and keyset-identical',
    (ns) => {
      const enKeys = keysWithPrefix(en, ns);
      expect(enKeys.length).toBeGreaterThan(0);
      expect(keysWithPrefix(zh, ns)).toEqual(enKeys);
    }
  );

  it('the five sentiment band labels render the contract copy in both languages', () => {
    expect(translate('en', 'market.sentimentLabel.frozen')).toBe('Frozen');
    expect(translate('en', 'market.sentimentLabel.hot')).toBe('Hot');
    expect(translate('zh', 'market.sentimentLabel.frozen')).toBe('冰点');
    expect(translate('zh', 'market.sentimentLabel.cool')).toBe('低迷');
    expect(translate('zh', 'market.sentimentLabel.neutral')).toBe('中性');
    expect(translate('zh', 'market.sentimentLabel.active')).toBe('活跃');
    expect(translate('zh', 'market.sentimentLabel.hot')).toBe('沸腾');
  });

  it('player copy interpolates and differs between the languages', () => {
    expect(translate('en', 'player.since', { date: 'Jul 1, 2026' })).toBe('since Jul 1, 2026');
    expect(translate('zh', 'player.since', { date: '2026年7月1日' })).toBe('始于 2026年7月1日');
    expect(translate('zh', 'player.private')).not.toBe(translate('en', 'player.private'));
  });
});
