/**
 * i18nHistoryKeys.test.ts — D1 dictionary coverage (D1 §5).
 *
 * Script-style assertions that the history.* namespace and the D1 backtest
 * additions (source switch, trading-days relabel, history validation copy)
 * exist and are keyset-aligned between en and zh, with the contract copy for
 * the switch segments and badge labels.
 */
import { DICTIONARIES, translate } from '@/lib/i18n';

const en = DICTIONARIES.en;
const zh = DICTIONARIES.zh;

const keysWithPrefix = (dict: Readonly<Record<string, string>>, prefix: string) =>
  Object.keys(dict)
    .filter((k) => k.startsWith(prefix))
    .sort();

describe('i18n D1 keyset alignment (en ↔ zh)', () => {
  it.each(['history.', 'backtest.'])('namespace %s is non-empty and keyset-identical', (ns) => {
    const enKeys = keysWithPrefix(en, ns);
    expect(enKeys.length).toBeGreaterThan(0);
    expect(keysWithPrefix(zh, ns)).toEqual(enKeys);
  });

  it('carries every D1 backtest addition in both languages', () => {
    for (const key of [
      'backtest.source',
      'backtest.sourceSynthetic',
      'backtest.sourceHistory',
      'backtest.tradingDays',
      'backtest.errDaysHistory',
      'backtest.helperHistory',
    ]) {
      expect(en[key]).toBeTruthy();
      expect(zh[key]).toBeTruthy();
    }
  });

  it('the source switch and days relabel render the contract copy', () => {
    expect(translate('en', 'backtest.sourceSynthetic')).toBe('Simulated');
    expect(translate('en', 'backtest.sourceHistory')).toBe('History');
    expect(translate('zh', 'backtest.sourceSynthetic')).toBe('模拟');
    expect(translate('zh', 'backtest.sourceHistory')).toBe('历史');
    // 契约 §5：历史态 days 标签变「交易日」
    expect(translate('zh', 'backtest.tradingDays')).toBe('交易日');
    expect(translate('en', 'backtest.tradingDays')).toBe('Trading days');
  });

  it('badge labels cover all four data sources plus the history fallback', () => {
    for (const kind of ['synthetic', 'history', 'sample', 'yfinance', 'akshare']) {
      expect(en[`history.source.${kind}`]).toBeTruthy();
      expect(zh[`history.source.${kind}`]).toBeTruthy();
    }
    expect(translate('zh', 'history.source.synthetic')).toBe('模拟');
    expect(translate('zh', 'history.source.sample')).toBe('样本');
  });

  it('the sync toast interpolates counts in both languages', () => {
    expect(translate('en', 'history.syncDone', { ok: 10, failed: 0 })).toBe(
      'Sync complete: 10 succeeded · 0 failed'
    );
    expect(translate('zh', 'history.syncDone', { ok: 12, failed: 2 })).toBe(
      '同步完成：成功 12 · 失败 2'
    );
  });
});
