/**
 * i18n.test.tsx (FinAlly-CN, CN-3 §6)
 *
 * The `en` dictionary must reproduce the current hardcoded copy verbatim, and
 * `useT` must default to English until the profile resolves.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { translate } from '@/lib/i18n';

jest.mock('swr', () => ({ __esModule: true, default: jest.fn() }));
import useSWR from 'swr';
import { useT } from '@/lib/i18n';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

describe('translate — en dictionary is byte-identical to the current copy', () => {
  it('static labels and empty states match the previous hardcoded strings', () => {
    expect(translate('en', 'tabs.positions')).toBe('Positions');
    expect(translate('en', 'tradebar.buy')).toBe('Buy');
    expect(translate('en', 'tradebar.sell')).toBe('Sell');
    expect(translate('en', 'chat.send')).toBe('Send');
    expect(translate('en', 'chat.thinking')).toBe('Thinking…');
    expect(translate('en', 'chat.placeholder')).toBe('Ask FinAlly about your portfolio…');
    expect(translate('en', 'positions.empty')).toBe('No positions yet. Use the trade bar to buy shares.');
    expect(translate('en', 'rules.empty')).toBe(
      'No standing rules. Ask FinAlly to create one — e.g. “buy 5 NVDA if it drops 3% today.”'
    );
    expect(translate('en', 'status.sim247')).toBe('SIM 24/7');
    expect(translate('en', 'watchlist.colSymbol')).toBe('Symbol');
  });

  it('interpolates {params}', () => {
    expect(translate('en', 'fill.bought', { qty: '5', ticker: 'AAPL', price: '$190.00' })).toBe(
      'Bought 5 AAPL @ $190.00'
    );
    expect(translate('en', 'tradebar.concentration', { ticker: 'AAPL', pct: 48 })).toBe(
      '⚠ A buy this size would make AAPL ~48% of your portfolio.'
    );
  });

  it('falls back to en (then the raw key) for missing entries', () => {
    expect(translate('zh', 'chat.title')).toBe('FinAlly AI'); // shared value
    expect(translate('en', 'nonexistent.key')).toBe('nonexistent.key');
  });
});

describe('translate — zh dictionary differs from en for translated keys', () => {
  it('renders Chinese for A-share copy', () => {
    expect(translate('zh', 'tradebar.buy')).toBe('买入');
    expect(translate('zh', 'tradebar.sell')).toBe('卖出');
    expect(translate('zh', 'tabs.positions')).toBe('持仓');
    expect(translate('zh', 'header.cash')).toBe('可用');
  });
});

// A tiny consumer of the hook to prove the locale wiring.
function Probe() {
  const t = useT();
  return <span data-testid="probe">{t('tradebar.buy')}</span>;
}

describe('useT default', () => {
  it('undefined profile (loading) → en', () => {
    mockUseSWR.mockReturnValue({ data: undefined } as never);
    render(<Probe />);
    expect(screen.getByTestId('probe').textContent).toBe('Buy');
  });

  it('a zh-CN profile → zh', () => {
    mockUseSWR.mockReturnValue({ data: { locale: 'zh-CN' } } as never);
    render(<Probe />);
    expect(screen.getByTestId('probe').textContent).toBe('买入');
  });
});
