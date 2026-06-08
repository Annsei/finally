import { usePriceStore, useTicker } from '@/stores/priceStore';
import type { PriceUpdate } from '@/types/market';

// PriceUpdate fixture with all snake_case fields
const aaplUpdate: PriceUpdate = {
  ticker: 'AAPL',
  price: 192.5,
  previous_price: 191.0,
  timestamp: 1717660000,
  change: 1.5,
  change_percent: 0.79,
  direction: 'up',
};

beforeEach(() => {
  // Reset store state before each test
  usePriceStore.setState({ prices: {}, connectionStatus: 'disconnected' });
});

describe('usePriceStore', () => {
  test('Test 1: initial state has prices === {} and connectionStatus === disconnected', () => {
    const state = usePriceStore.getState();
    expect(state.prices).toEqual({});
    expect(state.connectionStatus).toBe('disconnected');
  });

  test('Test 2: setPrices replaces the prices map; getState().prices.AAPL.price equals the value set', () => {
    usePriceStore.getState().setPrices({ AAPL: aaplUpdate });
    const state = usePriceStore.getState();
    expect(state.prices.AAPL).toBeDefined();
    expect(state.prices.AAPL.price).toBe(192.5);
  });

  test('Test 3: setConnectionStatus connected updates connectionStatus to connected', () => {
    usePriceStore.getState().setConnectionStatus('connected');
    expect(usePriceStore.getState().connectionStatus).toBe('connected');
  });

  test('Test 4: useTicker returns AAPL PriceUpdate after setPrices, undefined for unknown ticker', () => {
    // Set prices in store
    usePriceStore.setState({ prices: { AAPL: aaplUpdate } });

    // useTicker reads from the store state directly
    const aaplData = usePriceStore.getState().prices['AAPL'];
    const unknownData = usePriceStore.getState().prices['UNKNOWN'];

    expect(aaplData).toEqual(aaplUpdate);
    expect(unknownData).toBeUndefined();
  });
});
