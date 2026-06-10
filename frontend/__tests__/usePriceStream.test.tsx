import { renderHook, act } from '@testing-library/react';
import { usePriceStream } from '@/hooks/usePriceStream';
import { usePriceStore } from '@/stores/priceStore';
import type { PriceUpdate } from '@/types/market';

// Manual EventSource mock class
class MockEventSource {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;

  url: string;
  readyState: number;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  close = jest.fn(() => {
    this.readyState = MockEventSource.CLOSED;
  });

  constructor(url: string) {
    this.url = url;
    this.readyState = MockEventSource.CONNECTING;
  }
}

// Capture the last constructed EventSource instance
let mockInstance: MockEventSource | null = null;

// Install global mock before all tests
beforeAll(() => {
  // @ts-ignore
  const MockES = jest.fn().mockImplementation((url: string) => {
    mockInstance = new MockEventSource(url);
    return mockInstance;
  });
  // Attach static constants so the hook can read EventSource.CONNECTING etc.
  MockES.CONNECTING = MockEventSource.CONNECTING;
  MockES.OPEN = MockEventSource.OPEN;
  MockES.CLOSED = MockEventSource.CLOSED;
  // @ts-ignore
  global.EventSource = MockES;
});

afterAll(() => {
  // @ts-ignore
  delete global.EventSource;
});

beforeEach(() => {
  // Reset store state
  usePriceStore.setState({ prices: {}, connectionStatus: 'disconnected' });
  // Reset mock instance
  mockInstance = null;
  // Clear mock calls
  (global.EventSource as jest.Mock).mockClear();
});

const aaplUpdate: PriceUpdate = {
  ticker: 'AAPL',
  price: 192.5,
  previous_price: 191.0,
  timestamp: 1717660000,
  change: 1.5,
  change_percent: 0.79,
  direction: 'up',
};

describe('usePriceStream', () => {
  test('Test 1: on mount, EventSource is constructed exactly once with /api/stream/prices', () => {
    renderHook(() => usePriceStream());
    expect(global.EventSource).toHaveBeenCalledTimes(1);
    expect(global.EventSource).toHaveBeenCalledWith('/api/stream/prices');
  });

  test('Test 2: triggering onopen sets store connectionStatus to connected', () => {
    renderHook(() => usePriceStream());
    expect(mockInstance).not.toBeNull();

    act(() => {
      mockInstance!.onopen!(new Event('open'));
    });

    expect(usePriceStore.getState().connectionStatus).toBe('connected');
  });

  test('Test 3: triggering onmessage with valid JSON calls setPrices so store.prices.AAPL exists', () => {
    renderHook(() => usePriceStream());
    expect(mockInstance).not.toBeNull();

    act(() => {
      const event = new MessageEvent('message', {
        data: JSON.stringify({ AAPL: aaplUpdate }),
      });
      mockInstance!.onmessage!(event);
    });

    expect(usePriceStore.getState().prices.AAPL).toBeDefined();
    expect(usePriceStore.getState().prices.AAPL.price).toBe(192.5);
  });

  test('Test 4: triggering onmessage with malformed JSON does NOT throw and leaves prices unchanged', () => {
    renderHook(() => usePriceStream());
    expect(mockInstance).not.toBeNull();

    expect(() => {
      act(() => {
        const event = new MessageEvent('message', { data: 'not json' });
        mockInstance!.onmessage!(event);
      });
    }).not.toThrow();

    // Prices remain unchanged (empty)
    expect(usePriceStore.getState().prices).toEqual({});
  });

  test('Test 5a: onerror with readyState CONNECTING sets status reconnecting', () => {
    renderHook(() => usePriceStream());
    expect(mockInstance).not.toBeNull();

    act(() => {
      mockInstance!.readyState = MockEventSource.CONNECTING;
      mockInstance!.onerror!(new Event('error'));
    });

    expect(usePriceStore.getState().connectionStatus).toBe('reconnecting');
  });

  test('Test 5b: onerror with readyState CLOSED sets status disconnected', () => {
    renderHook(() => usePriceStream());
    expect(mockInstance).not.toBeNull();

    // First set to connected so we can observe the change to disconnected
    act(() => {
      mockInstance!.onopen!(new Event('open'));
    });
    expect(usePriceStore.getState().connectionStatus).toBe('connected');

    act(() => {
      mockInstance!.readyState = MockEventSource.CLOSED;
      mockInstance!.onerror!(new Event('error'));
    });

    expect(usePriceStore.getState().connectionStatus).toBe('disconnected');
  });

  test('Test 7: staleness watchdog recreates a silent OPEN connection and sets reconnecting', () => {
    jest.useFakeTimers();
    try {
      renderHook(() => usePriceStream());
      expect(mockInstance).not.toBeNull();
      const first = mockInstance!;

      act(() => {
        first.readyState = MockEventSource.OPEN;
        first.onopen!(new Event('open'));
      });
      expect(usePriceStore.getState().connectionStatus).toBe('connected');

      // Server pushes every ~500ms; simulate >5s of silence on an OPEN
      // connection (network died without a TCP reset — no error event fires).
      act(() => {
        jest.advanceTimersByTime(8_000);
      });

      expect(usePriceStore.getState().connectionStatus).toBe('reconnecting');
      expect(first.close).toHaveBeenCalled();
      expect(global.EventSource).toHaveBeenCalledTimes(2);

      // The replacement connection succeeding flips the status back.
      act(() => {
        mockInstance!.onopen!(new Event('open'));
      });
      expect(usePriceStore.getState().connectionStatus).toBe('connected');
    } finally {
      jest.useRealTimers();
    }
  });

  test('Test 6: unmounting the hook calls es.close() exactly once', () => {
    const { unmount } = renderHook(() => usePriceStream());
    expect(mockInstance).not.toBeNull();

    const closeMock = mockInstance!.close;
    expect(closeMock).not.toHaveBeenCalled();

    act(() => {
      unmount();
    });

    expect(closeMock).toHaveBeenCalledTimes(1);
  });
});
