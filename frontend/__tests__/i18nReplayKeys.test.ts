/**
 * i18nReplayKeys.test.ts — D3 replay dictionary coverage (D3 §3).
 *
 * The replay.* namespace must be keyset-aligned between en and zh, cover
 * every key the indicators render, and the zh copy must carry the contract
 * strings verbatim: badge「回放 {date} · {i}/{n}」and finished
 * 「回放已结束（价格冻结）」.
 */
import { DICTIONARIES, translate } from '@/lib/i18n';

const en = DICTIONARIES.en;
const zh = DICTIONARIES.zh;

const keysWithPrefix = (dict: Readonly<Record<string, string>>, prefix: string) =>
  Object.keys(dict)
    .filter((k) => k.startsWith(prefix))
    .sort();

describe('i18n replay keyset (en ↔ zh, D3 §3)', () => {
  it('the replay.* namespace is non-empty, keyset-identical, and covers the rendered keys', () => {
    const enKeys = keysWithPrefix(en, 'replay.');
    expect(enKeys.length).toBeGreaterThan(0);
    expect(keysWithPrefix(zh, 'replay.')).toEqual(enKeys);

    for (const key of [
      'replay.badge',
      'replay.title',
      'replay.window',
      'replay.day',
      'replay.progressAria',
      'replay.loop',
      'replay.once',
      'replay.finished',
    ]) {
      expect(en[key]).toBeDefined();
      expect(zh[key]).toBeDefined();
    }
  });

  it('zh carries the contract copy verbatim (badge + finished)', () => {
    expect(translate('zh', 'replay.badge', { date: '2020-03-16', i: 3, n: 20 })).toBe(
      '回放 2020-03-16 · 3/20'
    );
    expect(translate('zh', 'replay.finished')).toBe('回放已结束（价格冻结）');
  });

  it('en interpolates and differs from zh for translated keys', () => {
    expect(translate('en', 'replay.badge', { date: '2020-03-16', i: 3, n: 20 })).toBe(
      'Replay 2020-03-16 · 3/20'
    );
    expect(translate('en', 'replay.finished')).toBe('Replay finished (prices frozen)');
    expect(translate('en', 'replay.window', { from: '2020-03-02', to: '2020-03-27' })).toBe(
      '2020-03-02 → 2020-03-27'
    );
    for (const key of ['replay.badge', 'replay.title', 'replay.day', 'replay.finished']) {
      expect(translate('zh', key)).not.toBe(translate('en', key));
    }
  });
});
