/**
 * HeaderNav.test.tsx — P1 §2 Header global navigation.
 *
 * Test 1: nav testids render with i18n labels; no RouterContext → no crash
 * Test 2: without a router, the desk ('/') item is active
 * Test 3: with a mounted router, the matching item is active (accent classes)
 * Test 4: trailing-slash pathnames normalize before comparison
 * Test 5: hrefs point at the four routes
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { usePriceStore } from '@/stores/priceStore';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn().mockReturnValue({ data: undefined }),
}));

// next/compat/router returns null when no RouterContext is mounted — mock it
// so individual tests can also simulate a mounted router with a pathname.
jest.mock('next/compat/router', () => ({
  __esModule: true,
  useRouter: jest.fn(),
}));

import { useRouter } from 'next/compat/router';
const mockUseRouter = useRouter as jest.MockedFunction<typeof useRouter>;

import Header from '@/components/Header';

describe('Header navigation (P1)', () => {
  beforeEach(() => {
    usePriceStore.setState({ connectionStatus: 'disconnected', prices: {} });
    // Default: bare mount, no router (jest renders <Header/> without context)
    mockUseRouter.mockReturnValue(null);
  });

  it('Test 1: renders all four nav items without a Router and does not crash', () => {
    expect(() => render(<Header />)).not.toThrow();
    expect(screen.getByTestId('nav-desk').textContent).toBe('Desk');
    expect(screen.getByTestId('nav-market').textContent).toBe('Market');
    expect(screen.getByTestId('nav-journal').textContent).toBe('Journal');
    expect(screen.getByTestId('nav-arena').textContent).toBe('Arena');
  });

  it('Test 2: without a router, pathname defaults to "/" — Desk is active', () => {
    render(<Header />);
    expect(screen.getByTestId('nav-desk').getAttribute('data-active')).toBe('true');
    expect(screen.getByTestId('nav-desk').className).toContain('text-terminal-accent');
    expect(screen.getByTestId('nav-desk').className).toContain('border-terminal-accent');
    for (const id of ['nav-market', 'nav-journal', 'nav-arena']) {
      expect(screen.getByTestId(id).getAttribute('data-active')).toBe('false');
      expect(screen.getByTestId(id).className).not.toContain('text-terminal-accent');
    }
  });

  it('Test 3: with router.pathname "/market", the Market item is active', () => {
    mockUseRouter.mockReturnValue({ pathname: '/market' } as never);
    render(<Header />);
    expect(screen.getByTestId('nav-market').getAttribute('data-active')).toBe('true');
    expect(screen.getByTestId('nav-market').className).toContain('text-terminal-accent');
    expect(screen.getByTestId('nav-desk').getAttribute('data-active')).toBe('false');
  });

  it('Test 4: trailing-slash pathname ("/journal/") normalizes and still matches', () => {
    mockUseRouter.mockReturnValue({ pathname: '/journal/' } as never);
    render(<Header />);
    expect(screen.getByTestId('nav-journal').getAttribute('data-active')).toBe('true');
    expect(screen.getByTestId('nav-arena').getAttribute('data-active')).toBe('false');
  });

  it('Test 5: nav items link to /, /market, /journal, /arena', () => {
    render(<Header />);
    expect(screen.getByTestId('nav-desk').getAttribute('href')).toBe('/');
    expect(screen.getByTestId('nav-market').getAttribute('href')).toBe('/market');
    expect(screen.getByTestId('nav-journal').getAttribute('href')).toBe('/journal');
    expect(screen.getByTestId('nav-arena').getAttribute('href')).toBe('/arena');
  });
});
