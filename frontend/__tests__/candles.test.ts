/**
 * candles.ts unit tests — pure OHLCV aggregation (Batch 2):
 * bucketStart flooring; aggregateBars OHLCV merge math; applyTick
 * same-bucket merge / new-bucket append / stale-tick drop.
 */
import { bucketStart, aggregateBars, applyTick, type Bar } from '@/lib/candles';

const bar = (time: number, o: number, h: number, l: number, c: number, v: number): Bar => ({
  time,
  open: o,
  high: h,
  low: l,
  close: c,
  volume: v,
});

describe('bucketStart', () => {
  it('floors to interval boundaries', () => {
    expect(bucketStart(1717700007.9, 1)).toBe(1717700007);
    expect(bucketStart(1717700007, 5)).toBe(1717700005);
    expect(bucketStart(1717700059, 60)).toBe(1717700040);
    // exact boundary maps to itself
    expect(bucketStart(1717700040, 60)).toBe(1717700040);
  });
});

describe('aggregateBars', () => {
  const oneSecBars = [
    bar(100, 10.0, 10.5, 9.8, 10.2, 100),
    bar(101, 10.2, 10.3, 10.1, 10.1, 50),
    bar(102, 10.1, 10.8, 10.0, 10.7, 25),
    bar(105, 10.7, 10.9, 10.6, 10.8, 75),
  ];

  it('re-buckets 1s bars into 5s bars with correct OHLCV merge', () => {
    const out = aggregateBars(oneSecBars, 5);

    expect(out).toEqual([
      // bucket 100: bars 100-102 — open of first, max high, min low, close of last, summed volume
      bar(100, 10.0, 10.8, 9.8, 10.7, 175),
      // bucket 105: bar 105 alone
      bar(105, 10.7, 10.9, 10.6, 10.8, 75),
    ]);
  });

  it('interval 1 is an identity copy', () => {
    const out = aggregateBars(oneSecBars, 1);
    expect(out).toEqual(oneSecBars);
    expect(out).not.toBe(oneSecBars); // fresh array, no aliasing
  });

  it('empty input → empty output', () => {
    expect(aggregateBars([], 60)).toEqual([]);
  });

  it('drops out-of-order input bars instead of corrupting the series', () => {
    const out = aggregateBars(
      [bar(105, 1, 1, 1, 1, 1), bar(100, 2, 2, 2, 2, 2)],
      5
    );
    expect(out).toEqual([bar(105, 1, 1, 1, 1, 1)]);
  });
});

describe('applyTick', () => {
  it('appends a new bar for a new bucket', () => {
    const bars: Bar[] = [];
    const b = applyTick(bars, { timestamp: 100.4, price: 10.0, volume: 30 }, 5);

    expect(b).toEqual(bar(100, 10.0, 10.0, 10.0, 10.0, 30));
    expect(bars).toHaveLength(1);
  });

  it('merges a same-bucket tick into the last bar (high/low/close/volume)', () => {
    const bars: Bar[] = [bar(100, 10.0, 10.0, 10.0, 10.0, 30)];

    applyTick(bars, { timestamp: 102, price: 10.6, volume: 10 }, 5);
    const b = applyTick(bars, { timestamp: 104.9, price: 9.9, volume: 5 }, 5);

    expect(bars).toHaveLength(1);
    expect(b).toEqual(bar(100, 10.0, 10.6, 9.9, 9.9, 45));
  });

  it('starts a fresh bar when the tick crosses a bucket boundary', () => {
    const bars: Bar[] = [bar(100, 10.0, 10.6, 9.9, 10.1, 45)];

    const b = applyTick(bars, { timestamp: 105.1, price: 10.2, volume: 7 }, 5);

    expect(bars).toHaveLength(2);
    expect(b).toEqual(bar(105, 10.2, 10.2, 10.2, 10.2, 7));
  });

  it('drops ticks older than the newest bar and returns null', () => {
    const bars: Bar[] = [bar(105, 10.2, 10.2, 10.2, 10.2, 7)];

    const b = applyTick(bars, { timestamp: 99, price: 1.0, volume: 1 }, 5);

    expect(b).toBeNull();
    expect(bars).toEqual([bar(105, 10.2, 10.2, 10.2, 10.2, 7)]);
  });

  it('missing tick volume counts as 0', () => {
    const bars: Bar[] = [];
    const b = applyTick(bars, { timestamp: 100, price: 10.0 }, 1);
    expect(b?.volume).toBe(0);
  });
});
