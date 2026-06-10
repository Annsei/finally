import { test, expect } from '@playwright/test';

/**
 * SSE resilience (PLAN.md §12): disconnect and verify reconnection.
 *
 * Implementation note: context.setOffline() is deliberately NOT used here.
 * Chromium's offline emulation only blocks NEW requests — an already
 * established SSE stream keeps delivering events (verified empirically), so
 * the app correctly keeps showing "connected" and the test would hang.
 * Instead the stream endpoint is aborted at the route layer before the app
 * loads: the initial EventSource connect fails with a network error, the
 * indicator must leave "connected", and lifting the block must let the
 * client reconnect on its own (EventSource built-in retry, backstopped by
 * the usePriceStream staleness watchdog) without a page reload.
 *
 * Silent zombie-connection detection (open socket, no data) is covered by
 * the frontend unit test for the staleness watchdog.
 */
const STREAM = '**/api/stream/prices';

test.describe('SSE resilience', () => {
  test('indicator reflects stream failure and recovers without reload', async ({
    page,
  }) => {
    // Sever the price stream before the app loads.
    await page.route(STREAM, (route) => route.abort('internetdisconnected'));
    await page.goto('/');

    // The connection indicator must reflect the failure.
    const indicator = page.getByTestId('connection-status');
    await expect(indicator).toHaveAttribute(
      'data-state',
      /^(reconnecting|disconnected)$/,
      { timeout: 30_000 }
    );

    // Restore the network path — the client must reconnect by itself.
    await page.unroute(STREAM);
    await expect(indicator).toHaveAttribute('data-state', 'connected', {
      timeout: 60_000,
    });
  });
});
