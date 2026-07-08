/**
 * P2 fix pin — rowsToGroup param fallback/clamping (builder-payload invariant).
 *
 * A cleared param input used to slip through the `Number.isFinite` fallback
 * (Number('') === 0 is finite) and submit params like {period: 0}, which the
 * backend rejects with 400 "param must be between 2 and 120" — violating the
 * invariant that the condition builder can never produce a payload the
 * backend's FIELD_SPECS validation rejects. rowsToGroup now falls back to the
 * registry default on empty/garbage input, rounds typed values to integers,
 * and clamps them into [min, max].
 */
import { rowsToGroup, FIELD_SPECS } from '@/pages/strategies';

describe('rowsToGroup param fallback and clamping (P2 §8 invariant)', () => {
  it('falls back to the registry default when a param input is cleared', () => {
    // Number('') === 0 must NOT survive as params.period = 0
    expect(
      rowsToGroup('all', [{ field: 'rsi', op: 'below', value: '30', params: { period: '' } }])
    ).toEqual({ all: [{ field: 'rsi', op: 'below', value: 30, params: { period: 14 } }] });

    // whitespace-only behaves like empty
    expect(
      rowsToGroup('all', [{ field: 'ma', op: 'above', value: '0', params: { period: '   ' } }])
    ).toEqual({ all: [{ field: 'ma', op: 'above', value: 0, params: { period: 20 } }] });
  });

  it('falls back to the registry default for missing or non-numeric params', () => {
    // key absent from row state entirely
    expect(
      rowsToGroup('all', [{ field: 'window_high', op: 'above', value: '', params: {} }])
    ).toEqual({ all: [{ field: 'window_high', op: 'above', params: { minutes: 60 } }] });

    // garbage text
    expect(
      rowsToGroup('any', [
        { field: 'ma_cross', op: 'above', value: '', params: { fast: 'abc', slow: '20' } },
      ])
    ).toEqual({ any: [{ field: 'ma_cross', op: 'above', params: { fast: 5, slow: 20 } }] });
  });

  it('clamps out-of-range params into the registry [min, max] bounds', () => {
    expect(
      rowsToGroup('all', [
        { field: 'window_low', op: 'below', value: '', params: { minutes: '9999' } },
      ])
    ).toEqual({ all: [{ field: 'window_low', op: 'below', params: { minutes: 240 } }] });

    expect(
      rowsToGroup('all', [{ field: 'rsi', op: 'below', value: '30', params: { period: '1' } }])
    ).toEqual({ all: [{ field: 'rsi', op: 'below', value: 30, params: { period: 2 } }] });

    expect(
      rowsToGroup('all', [{ field: 'rsi', op: 'below', value: '30', params: { period: '0' } }])
    ).toEqual({ all: [{ field: 'rsi', op: 'below', value: 30, params: { period: 2 } }] });
  });

  it('rounds decimal params to integers (backend params are int-only)', () => {
    expect(
      rowsToGroup('all', [{ field: 'ma', op: 'above', value: '0', params: { period: '12.4' } }])
    ).toEqual({ all: [{ field: 'ma', op: 'above', value: 0, params: { period: 12 } }] });
  });

  it('keeps well-formed in-range params byte-identical (no behavior drift)', () => {
    expect(
      rowsToGroup('all', [
        { field: 'rsi', op: 'below', value: '30', params: { period: '14' } },
        { field: 'ma_cross', op: 'above', value: '', params: { fast: '5', slow: '20' } },
      ])
    ).toEqual({
      all: [
        { field: 'rsi', op: 'below', value: 30, params: { period: 14 } },
        { field: 'ma_cross', op: 'above', params: { fast: 5, slow: 20 } },
      ],
    });
  });

  it('every registry default is inside its own [min, max] bounds', () => {
    for (const spec of Object.values(FIELD_SPECS)) {
      for (const p of spec.params) {
        expect(p.def).toBeGreaterThanOrEqual(p.min);
        expect(p.def).toBeLessThanOrEqual(p.max);
      }
    }
  });
});
