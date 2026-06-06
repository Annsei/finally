/**
 * index.test.tsx — FE-03 dark terminal theme assertion
 * Test 5 (FE-03): Rendering the index page produces a root element whose
 * className contains `bg-terminal-bg` (dark terminal theme applied at page root).
 */
import React from 'react';
import { render } from '@testing-library/react';

jest.mock('@/hooks/usePriceStream', () => ({
  usePriceStream: jest.fn(),
}));

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn().mockReturnValue({ data: undefined }),
}));

jest.mock('@/components/SparklineChart', () => ({
  __esModule: true,
  default: () => <div data-testid="sparkline-stub" />,
}));

import Dashboard from '@/pages/index';

describe('Dashboard index page', () => {
  it('Test 5 (FE-03): root element className contains bg-terminal-bg', () => {
    const { container } = render(<Dashboard />);
    const root = container.firstChild as HTMLElement;
    expect(root.className).toContain('bg-terminal-bg');
  });
});
