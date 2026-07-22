import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import useSWR from 'swr';

// next/compat/router returns null when no RouterContext is mounted — mock it
// so the route-change test can swap asPath between renders.
jest.mock('next/compat/router', () => ({
  __esModule: true,
  useRouter: jest.fn().mockReturnValue(null),
}));

import { useRouter } from 'next/compat/router';
import ApiStatusProvider from '@/components/ApiStatusProvider';

const mockUseRouter = useRouter as jest.MockedFunction<typeof useRouter>;

jest.mock('@/lib/i18n', () => ({
  useT: () => (key: string) =>
    ({
      'api.errorTitle': 'Data request failed.',
      'api.retry': 'Retry',
      'api.dismiss': 'Dismiss error',
    })[key] ?? key,
}));

const request = jest.fn<Promise<never>, []>();

function BrokenRead() {
  useSWR('/api/test-failure', request, { dedupingInterval: 0 });
  return <div>content remains available</div>;
}

describe('ApiStatusProvider', () => {
  beforeEach(() => {
    // Default: bare mount, no RouterContext (compat router → null)
    mockUseRouter.mockReturnValue(null);
  });

  it('surfaces a REST read failure and retries its SWR key', async () => {
    request.mockRejectedValue(new Error('network offline'));
    render(
      <ApiStatusProvider>
        <BrokenRead />
      </ApiStatusProvider>
    );

    const banner = await screen.findByTestId('api-error-banner');
    expect(banner).toHaveAttribute('role', 'alert');
    expect(banner.textContent).toContain('network offline');
    expect(screen.getByText('content remains available')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Retry'));
    await waitFor(() => expect(request.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it('clears the failure banner on route change', async () => {
    const failing = jest.fn().mockRejectedValue(new Error('stale page error'));
    function BrokenRouteRead() {
      useSWR('/api/test-failure-route', failing, { dedupingInterval: 0 });
      return <div>page body</div>;
    }

    mockUseRouter.mockReturnValue({ asPath: '/' } as never);
    const { rerender } = render(
      <ApiStatusProvider>
        <BrokenRouteRead />
      </ApiStatusProvider>
    );

    const banner = await screen.findByTestId('api-error-banner');
    expect(banner.textContent).toContain('stale page error');

    // Simulate a pages-router navigation: asPath changes and the tree re-renders.
    mockUseRouter.mockReturnValue({ asPath: '/market' } as never);
    rerender(
      <ApiStatusProvider>
        <BrokenRouteRead />
      </ApiStatusProvider>
    );

    await waitFor(() => expect(screen.queryByTestId('api-error-banner')).toBeNull());
  });
});
