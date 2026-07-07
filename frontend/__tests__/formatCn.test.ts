/**
 * formatCn.test.ts (FinAlly-CN, CN-3 §6)
 *
 * The market-aware format helpers are additive: formatQuantity is untouched and
 * the US paths of formatMoney/formatShares reproduce the previous output.
 */
import { formatMoney, formatLargeCount, formatShares, formatQuantity } from '@/lib/format';

const US = { lot_size: 1 };
const CN = { lot_size: 100 };

describe('formatMoney', () => {
  it('US: symbol + grouping, 2 decimals — identical to the previous display', () => {
    expect(formatMoney(10000, { currency_symbol: '$', locale: 'en-US' })).toBe('$10,000.00');
    expect(formatMoney(12345.67, { currency_symbol: '$', locale: 'en-US' })).toBe('$12,345.67');
  });

  it('CN: ¥ symbol', () => {
    expect(formatMoney(1700, { currency_symbol: '¥', locale: 'zh-CN' })).toBe('¥1,700.00');
  });

  it('undefined/NaN → —', () => {
    expect(formatMoney(undefined, { currency_symbol: '$', locale: 'en-US' })).toBe('—');
    expect(formatMoney(NaN, { currency_symbol: '$', locale: 'en-US' })).toBe('—');
  });
});

describe('formatLargeCount', () => {
  it('en keeps grouped integers (unchanged volume display)', () => {
    expect(formatLargeCount(35000, 'en-US')).toBe('35,000');
  });

  it('zh collapses into 万', () => {
    expect(formatLargeCount(35000, 'zh-CN')).toBe('3.5万');
    expect(formatLargeCount(10000, 'zh-CN')).toBe('1万');
  });

  it('zh collapses into 亿', () => {
    expect(formatLargeCount(250000000, 'zh-CN')).toBe('2.5亿');
  });

  it('undefined → —', () => {
    expect(formatLargeCount(undefined, 'zh-CN')).toBe('—');
  });
});

describe('formatShares', () => {
  it('US (lot 1) reuses formatQuantity exactly', () => {
    expect(formatShares(52.6315, US)).toBe(formatQuantity(52.6315));
    expect(formatShares(10, US)).toBe('10');
  });

  it('CN (lot 100) shows whole 手', () => {
    expect(formatShares(300, CN)).toBe('3手');
  });

  it('CN with an odd-lot remainder appends 零股', () => {
    expect(formatShares(350, CN)).toBe('3手 (零股50)');
  });

  it('undefined → —', () => {
    expect(formatShares(undefined, CN)).toBe('—');
  });
});
