/**
 * HeaderNavStrategies.test.tsx — P2 §8 nav additions.
 *
 * Test 1: nav-strategies / nav-runs render with English labels and hrefs
 * Test 2: router on /strategies marks the Strategies item active (only)
 * Test 3: router on /runs marks the Runs item active (only)
 * Test 4: zh-CN profile renders the Chinese labels (策略 / 回测库)
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { usePriceStore } from '@/stores/priceStore';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn().mockReturnValue({ data: undefined }),
}));
import useSWR from 'swr';

jest.mock('next/compat/router', () => ({
  __esModule: true,
  useRouter: jest.fn(),
}));

import { useRouter } from 'next/compat/router';
const mockUseRouter = useRouter as jest.MockedFunction<typeof useRouter>;

import Header from '@/components/Header';

describe('Header navigation — strategies + runs (P2)', () => {
  beforeEach(() => {
    usePriceStore.setState({ connectionStatus: 'disconnected', prices: {} });
    (useSWR as jest.Mock).mockReturnValue({ data: undefined });
    mockUseRouter.mockReturnValue(null);
  });

  it('Test 1: renders nav-strategies and nav-runs with labels and hrefs', () => {
    render(<Header />);
    const strategies = screen.getByTestId('nav-strategies');
    const runs = screen.getByTestId('nav-runs');
    expect(strategies.textContent).toBe('Strategies');
    expect(runs.textContent).toBe('Runs');
    expect(strategies.getAttribute('href')).toBe('/strategies');
    expect(runs.getAttribute('href')).toBe('/runs');
    // The original four items are untouched
    for (const id of ['nav-desk', 'nav-market', 'nav-journal', 'nav-arena']) {
      expect(screen.getByTestId(id)).toBeInTheDocument();
    }
  });

  it('Test 2: with router.pathname "/strategies", the Strategies item is active', () => {
    mockUseRouter.mockReturnValue({ pathname: '/strategies' } as never);
    render(<Header />);
    expect(screen.getByTestId('nav-strategies').getAttribute('data-active')).toBe('true');
    expect(screen.getByTestId('nav-strategies').className).toContain('text-terminal-accent');
    for (const id of ['nav-desk', 'nav-market', 'nav-runs', 'nav-journal', 'nav-arena']) {
      expect(screen.getByTestId(id).getAttribute('data-active')).toBe('false');
    }
  });

  it('Test 3: with router.pathname "/runs/", normalization marks the Runs item active', () => {
    mockUseRouter.mockReturnValue({ pathname: '/runs/' } as never);
    render(<Header />);
    expect(screen.getByTestId('nav-runs').getAttribute('data-active')).toBe('true');
    expect(screen.getByTestId('nav-strategies').getAttribute('data-active')).toBe('false');
  });

  it('Test 4: a zh-CN profile renders the Chinese nav labels', () => {
    (useSWR as jest.Mock).mockReturnValue({ data: { locale: 'zh-CN' } });
    render(<Header />);
    expect(screen.getByTestId('nav-strategies').textContent).toBe('策略');
    expect(screen.getByTestId('nav-runs').textContent).toBe('回测库');
  });
});
