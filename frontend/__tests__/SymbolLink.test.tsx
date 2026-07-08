/**
 * SymbolLink.test.tsx — P1 §2 canonical symbol link.
 *
 * Test 1: renders an anchor to /symbol?c=CODE with testid symbol-link-CODE
 * Test 2: codes are uppercase-normalized (input and testid)
 * Test 3: children override the default label; className merges with
 *         hover:underline
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import SymbolLink from '@/components/SymbolLink';

describe('SymbolLink (P1)', () => {
  it('Test 1: links to the symbol page with the code as query param', () => {
    render(<SymbolLink code="AAPL" />);
    const link = screen.getByTestId('symbol-link-AAPL');
    expect(link.tagName.toLowerCase()).toBe('a');
    expect(link.getAttribute('href')).toContain('/symbol');
    expect(link.getAttribute('href')).toContain('c=AAPL');
    expect(link.textContent).toBe('AAPL');
  });

  it('Test 2: lowercase input is uppercase-normalized everywhere', () => {
    render(<SymbolLink code="nvda" />);
    const link = screen.getByTestId('symbol-link-NVDA');
    expect(link.getAttribute('href')).toContain('c=NVDA');
    expect(link.textContent).toBe('NVDA');
  });

  it('Test 3: custom children and className are honored, hover underline kept', () => {
    render(
      <SymbolLink code="600519" className="font-semibold">
        贵州茅台
      </SymbolLink>
    );
    const link = screen.getByTestId('symbol-link-600519');
    expect(link.textContent).toBe('贵州茅台');
    expect(link.className).toContain('hover:underline');
    expect(link.className).toContain('font-semibold');
    expect(link.getAttribute('href')).toContain('c=600519');
  });
});
