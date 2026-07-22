/**
 * i18nStrategyKeys.test.ts — P2 §9 dictionary skeleton.
 *
 * Script-style assertions that the new strategy.* / runs.* namespaces exist,
 * are keyset-aligned between en and zh, and carry the contract-mandated
 * entries (six template name/desc pairs, condition field copy, status chips,
 * the soft deploy gate warning, and the pause-semantics hint).
 */
import { DICTIONARIES, translate } from '@/lib/i18n';

const en = DICTIONARIES.en;
const zh = DICTIONARIES.zh;

const keysIn = (dict: Readonly<Record<string, string>>, prefix: string) =>
  Object.keys(dict)
    .filter((k) => k.startsWith(prefix))
    .sort();

describe('i18n P2 keyset alignment (en ↔ zh)', () => {
  it.each(['strategy.', 'runs.'])('namespace %s is non-empty and keyset-identical', (ns) => {
    const enKeys = keysIn(en, ns);
    const zhKeys = keysIn(zh, ns);
    expect(enKeys.length).toBeGreaterThan(0);
    expect(zhKeys).toEqual(enKeys);
  });

  it('every strategy./runs. value is a non-empty string in both dictionaries', () => {
    for (const ns of ['strategy.', 'runs.']) {
      for (const key of keysIn(en, ns)) {
        expect(en[key].length).toBeGreaterThan(0);
        expect(zh[key].length).toBeGreaterThan(0);
      }
    }
  });

  it('nav, chat-kind, and strategy badge keys exist in both dictionaries', () => {
    const keys = [
      'nav.strategies',
      'nav.runs',
      'chat.kind.strategy',
      'badge.strategyCreated',
      'badge.strategyDeployed',
      'badge.strategyPaused',
      'badge.strategyFailed',
      'badge.strategyBacktest',
    ];
    for (const key of keys) {
      expect(en[key]).toBeDefined();
      expect(zh[key]).toBeDefined();
    }
    expect(translate('en', 'nav.strategies')).toBe('Strategies');
    expect(translate('zh', 'nav.strategies')).toBe('策略');
    expect(translate('en', 'nav.runs')).toBe('Runs');
    expect(translate('zh', 'nav.runs')).toBe('回测库');
  });

  it('all six templates carry name + desc in both dictionaries', () => {
    const templates = [
      'dip_buyer',
      'momentum_breakout',
      'ma_golden_cross',
      'grid_lite',
      'rsi_rebound',
      'trend_rider',
    ];
    for (const key of templates) {
      for (const leaf of ['name', 'desc']) {
        const full = `strategy.template.${key}.${leaf}`;
        expect(en[full]).toBeDefined();
        expect(zh[full]).toBeDefined();
        // zh copy is actually translated, not an en echo
        expect(zh[full]).not.toBe(en[full]);
      }
    }
  });

  it('condition copy covers every whitelisted field (dropdown name + sentence template)', () => {
    const fields = [
      'price',
      'day_change_pct',
      'ma',
      'ma_cross',
      'ema_cross',
      'rsi',
      'window_high',
      'window_low',
      'pullback_from_high_pct',
    ];
    for (const field of fields) {
      expect(en[`strategy.cond.field.${field}`]).toBeDefined();
      expect(zh[`strategy.cond.field.${field}`]).toBeDefined();
      // cross fields split into golden/death variants; the rest are single templates
      if (field === 'ma_cross' || field === 'ema_cross') {
        expect(en[`strategy.cond.${field}.above`]).toBeDefined();
        expect(en[`strategy.cond.${field}.below`]).toBeDefined();
      } else {
        expect(en[`strategy.cond.${field}`]).toBeDefined();
      }
    }
    // group joiners + op words
    for (const key of ['strategy.cond.all', 'strategy.cond.any', 'strategy.cond.above', 'strategy.cond.below']) {
      expect(en[key]).toBeDefined();
      expect(zh[key]).toBeDefined();
    }
  });

  it('status chips cover the full lifecycle in both dictionaries', () => {
    for (const status of ['draft', 'live', 'paused', 'archived']) {
      expect(en[`strategy.status.${status}`]).toBeDefined();
      expect(zh[`strategy.status.${status}`]).toBeDefined();
      expect(zh[`strategy.status.${status}`]).not.toBe(en[`strategy.status.${status}`]);
    }
  });

  it('soft deploy gate warning and pause-semantics hint exist and are translated', () => {
    for (const key of ['strategy.deployNoRunsWarning', 'strategy.pauseHint']) {
      expect(en[key]).toBeDefined();
      expect(zh[key]).toBeDefined();
      expect(zh[key]).not.toBe(en[key]);
    }
    // the pause hint must state the frozen-position semantics (P2 §3)
    expect(en['strategy.pauseHint']).toMatch(/open position/i);
    expect(zh['strategy.pauseHint']).toContain('持仓');
  });

  it('condition sentence templates interpolate params', () => {
    expect(translate('en', 'strategy.cond.rsi', { period: 14, op: '≤', value: 30 })).toBe(
      'RSI(14) ≤ 30'
    );
    expect(translate('zh', 'strategy.cond.window_high', { minutes: 60 })).toBe('突破 60 分钟新高');
    expect(
      translate('en', 'strategy.cond.ma_cross.above', { fast: 5, slow: 20 })
    ).toBe('SMA(5)/SMA(20) golden cross');
  });
});
