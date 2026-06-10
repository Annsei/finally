import { expect } from '@playwright/test';
import type { APIRequestContext, APIResponse } from '@playwright/test';

/** Shape of GET /api/portfolio/ responses (backend/app/routes/portfolio.py). */
export interface Position {
  ticker: string;
  quantity: number;
  avg_cost: number;
  current_price: number;
  unrealized_pnl: number;
  pnl_pct: number;
}

export interface Portfolio {
  cash: number;
  total_value: number;
  positions: Position[];
}

/** GET /api/portfolio/ and parse. */
export async function getPortfolio(request: APIRequestContext): Promise<Portfolio> {
  const res = await request.get('/api/portfolio/');
  expect(res.ok()).toBeTruthy();
  return (await res.json()) as Portfolio;
}

/** Execute a market order through the public trade API. */
export function trade(
  request: APIRequestContext,
  ticker: string,
  side: 'buy' | 'sell',
  quantity: number
): Promise<APIResponse> {
  return request.post('/api/portfolio/trade', { data: { ticker, side, quantity } });
}

/** Sell the full current position in `ticker`, if any (test setup/cleanup). */
export async function flattenPosition(
  request: APIRequestContext,
  ticker: string
): Promise<void> {
  const portfolio = await getPortfolio(request);
  const position = portfolio.positions.find((p) => p.ticker === ticker);
  if (position && position.quantity > 0) {
    await trade(request, ticker, 'sell', position.quantity);
  }
}

/** Remove `ticker` from the watchlist (idempotent; missing ticker is fine). */
export async function removeFromWatchlist(
  request: APIRequestContext,
  ticker: string
): Promise<void> {
  await request.delete(`/api/watchlist/${ticker}`);
}
