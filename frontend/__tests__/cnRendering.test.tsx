/**
 * cnRendering.test.tsx (FinAlly-CN, CN-3 §6)
 *
 * End-to-end-ish component checks under a mocked A-share profile:
 *  - ChatPanel renders Chinese UI + Chinese action badges.
 *  - Header's connection dot keeps its bg-terminal-up class (the colour is
 *    pinned in CSS by data-state, so the class name never changes) while the
 *    header labels localise to Chinese.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import useSWR from 'swr';
import { usePriceStore } from '@/stores/priceStore';
import type { ChatMessage } from '@/types/market';

jest.mock('swr', () => ({ __esModule: true, default: jest.fn() }));
jest.mock('@/lib/reload', () => ({ __esModule: true, hardReload: jest.fn() }));

import ChatPanel from '@/components/ChatPanel';
import Header from '@/components/Header';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

const cnProfile = {
  market: 'cn',
  currency_symbol: '¥',
  locale: 'zh-CN',
  lot_size: 100,
  t_plus: 1,
  up_is_red: true,
  seed_cash: 100000,
  midday_break: true,
  names: {},
  price_limit_pct: {},
};

describe('ChatPanel — A-share market renders Chinese', () => {
  it('localises the input, send button, empty state and trade badge', () => {
    const msg: ChatMessage = {
      role: 'assistant',
      content: '已为你买入。',
      actions: {
        trades: [{ status: 'executed', ticker: '600519', side: 'buy', quantity: 100, price: 1700 }],
        watchlist_changes: [],
      },
      created_at: '2026-07-07T00:00:00Z',
    };
    mockUseSWR.mockImplementation(((key: string) => {
      if (key === '/api/market/profile') return { data: cnProfile } as never;
      if (key === '/api/chat/') return { data: { messages: [msg] }, mutate: jest.fn() } as never;
      return { data: undefined, mutate: jest.fn() } as never;
    }) as never);

    render(<ChatPanel open onToggle={jest.fn()} />);

    expect(screen.getByRole('button', { name: '发送' })).toBeTruthy();
    expect(screen.getByPlaceholderText('向 FinAlly 咨询你的组合…')).toBeTruthy();
    // Trade badge localises the verb and uses ¥ + 手 formatting: "买入 1手 600519 @ ¥1700.00"
    expect(screen.getByText(/买入 1手 600519 @ ¥1700\.00/)).toBeTruthy();
  });
});

describe('Header — connection dot stays pinned under CN', () => {
  it('keeps the bg-terminal-up class while labels localise to Chinese', () => {
    usePriceStore.setState({ connectionStatus: 'connected', prices: {} });
    mockUseSWR.mockImplementation(((key: string) => {
      if (key === '/api/market/profile') return { data: cnProfile } as never;
      return { data: undefined } as never;
    }) as never);

    render(<Header />);

    // Class name is unchanged (contract): the CSS attribute selector pins the
    // colour green, so the utility class staying bg-terminal-up is correct.
    const dot = screen.getByTestId('connection-status');
    expect(dot.getAttribute('data-state')).toBe('connected');
    expect(dot.className).toContain('bg-terminal-up');

    // Header labels are Chinese on the A-share market.
    expect(screen.getByText('可用')).toBeTruthy();
    expect(screen.getByText('总资产')).toBeTruthy();
  });
});
