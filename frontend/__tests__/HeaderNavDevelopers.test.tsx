/**
 * HeaderNavDevelopers.test.tsx — P3 §8 nav addition.
 *
 * Test 1: nav-developers renders with the English label and href
 * Test 2: router on /developers marks the Developers item active (only)
 * Test 3: trailing-slash pathname ("/developers/") normalizes and matches
 * Test 4: zh-CN profile renders the Chinese label (开发者)
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

describe('Header navigation — developers (P3)', () => {
  beforeEach(() => {
    usePriceStore.setState({ connectionStatus: 'disconnected', prices: {} });
    (useSWR as jest.Mock).mockReturnValue({ data: undefined });
    mockUseRouter.mockReturnValue(null);
  });

  it('Test 1: renders nav-developers with label and href', () => {
    render(<Header />);
    const developers = screen.getByTestId('nav-developers');
    expect(developers.textContent).toBe('Developers');
    expect(developers.getAttribute('href')).toBe('/developers');
    // The existing six items are untouched
    for (const id of [
      'nav-desk',
      'nav-market',
      'nav-strategies',
      'nav-runs',
      'nav-journal',
      'nav-arena',
    ]) {
      expect(screen.getByTestId(id)).toBeInTheDocument();
    }
  });

  it('Test 2: with router.pathname "/developers", the Developers item is active', () => {
    mockUseRouter.mockReturnValue({ pathname: '/developers' } as never);
    render(<Header />);
    expect(screen.getByTestId('nav-developers').getAttribute('data-active')).toBe('true');
    expect(screen.getByTestId('nav-developers').className).toContain('text-terminal-accent');
    for (const id of [
      'nav-desk',
      'nav-market',
      'nav-strategies',
      'nav-runs',
      'nav-journal',
      'nav-arena',
    ]) {
      expect(screen.getByTestId(id).getAttribute('data-active')).toBe('false');
    }
  });

  it('Test 3: trailing-slash pathname ("/developers/") normalizes and still matches', () => {
    mockUseRouter.mockReturnValue({ pathname: '/developers/' } as never);
    render(<Header />);
    expect(screen.getByTestId('nav-developers').getAttribute('data-active')).toBe('true');
    expect(screen.getByTestId('nav-desk').getAttribute('data-active')).toBe('false');
  });

  it('Test 4: a zh-CN profile renders the Chinese nav label', () => {
    (useSWR as jest.Mock).mockReturnValue({ data: { locale: 'zh-CN' } });
    render(<Header />);
    expect(screen.getByTestId('nav-developers').textContent).toBe('开发者');
  });
});
