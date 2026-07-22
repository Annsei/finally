/**
 * candles.ts — pure OHLCV aggregation helpers (FRONTEND_REALISM.md §2.2)
 *
 * The backend serves 1-second bars (GET /api/market/history) and the SSE
 * stream delivers ticks. These helpers bucket both into the display
 * timeframe. Kept free of chart/React dependencies so they are unit-testable.
 */
import type { HistoryBar } from '@/types/market';

export type Bar = HistoryBar;

export interface Tick {
  timestamp: number; // Unix seconds (float)
  price: number;
  volume?: number;
}

/** Start of the bucket containing `ts` for the given interval. */
export function bucketStart(ts: number, intervalSec: number): number {
  return Math.floor(ts / intervalSec) * intervalSec;
}

/**
 * Re-bucket finer bars (ascending) into `intervalSec` bars (ascending).
 * Standard OHLCV merge: first open, max high, min low, last close, summed volume.
 */
export function aggregateBars(bars: Bar[], intervalSec: number): Bar[] {
  const out: Bar[] = [];
  for (const b of bars) {
    const time = bucketStart(b.time, intervalSec);
    const last = out[out.length - 1];
    if (last && last.time === time) {
      last.high = Math.max(last.high, b.high);
      last.low = Math.min(last.low, b.low);
      last.close = b.close;
      last.volume += b.volume;
    } else if (!last || time > last.time) {
      out.push({ time, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume });
    }
    // out-of-order input bars are dropped — the backend serves ascending data
  }
  return out;
}

/**
 * Fold a live tick into an ascending bar array IN PLACE.
 * Returns the updated/appended bar, or null when the tick belongs to a bucket
 * older than the newest bar (stale tick — dropped).
 */
export function applyTick(bars: Bar[], tick: Tick, intervalSec: number): Bar | null {
  const time = bucketStart(tick.timestamp, intervalSec);
  const vol = tick.volume ?? 0;
  const last = bars[bars.length - 1];

  if (last && time < last.time) return null;

  if (last && time === last.time) {
    last.high = Math.max(last.high, tick.price);
    last.low = Math.min(last.low, tick.price);
    last.close = tick.price;
    last.volume += vol;
    return last;
  }

  const bar: Bar = {
    time,
    open: tick.price,
    high: tick.price,
    low: tick.price,
    close: tick.price,
    volume: vol,
  };
  bars.push(bar);
  return bar;
}
