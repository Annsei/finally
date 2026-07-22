/**
 * arenaCompHelpers.test.ts — pure competition helpers (D2 §5).
 *
 * Direct coverage of the exported helpers in ArenaCompetitions.tsx:
 * form validation (name 1..40, hours integer 1..168), tolerant response
 * shaping (list / created / board / status), local-countdown math
 * (compRemainingMs by status, clamped at 0) and H:MM:SS formatting up to the
 * 168-hour contract maximum, plus the never-throwing clipboard wrapper.
 */
import {
  boardRows,
  compRemainingMs,
  competitionsList,
  copyCompText,
  createdCompetition,
  detailStatus,
  formatCompCountdown,
  validateCompHours,
  validateCompName,
} from '@/components/ArenaCompetitions';
import type { CompetitionSummary } from '@/types/market';

const comp = (over: Partial<CompetitionSummary> = {}): CompetitionSummary => ({
  id: 'c1',
  name: 'Friday Sprint',
  code: 'ABC234',
  status: 'running',
  member_count: 2,
  starts_at: '2026-07-12T00:00:00Z',
  ends_at: '2026-07-12T01:00:00Z',
  ...over,
});

describe('create-form validation (contract §3/§5)', () => {
  it('validateCompName trims and accepts 1..40 characters', () => {
    expect(validateCompName('  Friday Sprint  ')).toEqual({ ok: true, name: 'Friday Sprint' });
    expect(validateCompName('x'.repeat(40))).toEqual({ ok: true, name: 'x'.repeat(40) });
  });

  it.each(['', '   ', 'x'.repeat(41)])('validateCompName rejects %j', (raw) => {
    expect(validateCompName(raw).ok).toBe(false);
  });

  it('validateCompHours accepts whole hours 1..168 (trimmed)', () => {
    expect(validateCompHours('1')).toEqual({ ok: true, hours: 1 });
    expect(validateCompHours(' 24 ')).toEqual({ ok: true, hours: 24 });
    expect(validateCompHours('168')).toEqual({ ok: true, hours: 168 });
  });

  it.each(['0', '169', '1.5', '-3', 'abc', '', '24h'])(
    'validateCompHours rejects %j',
    (raw) => {
      expect(validateCompHours(raw).ok).toBe(false);
    }
  );
});

describe('tolerant response shaping', () => {
  it('competitionsList accepts {competitions: [...]} or a bare array and rejects garbage', () => {
    const list = [comp()];
    expect(competitionsList({ competitions: list })).toEqual(list);
    expect(competitionsList(list)).toEqual(list);
    expect(competitionsList({ nope: list })).toEqual([]);
    expect(competitionsList(null)).toEqual([]);
    expect(competitionsList('x')).toEqual([]);
  });

  it('createdCompetition unwraps {competition} (contract 201 shape) with a flat fallback', () => {
    const created = comp();
    expect(createdCompetition({ competition: created })).toEqual(created);
    expect(createdCompetition(created)).toEqual(created);
    // no code → not a usable create payload
    expect(createdCompetition({ competition: { id: 'c1' } })).toBeNull();
    expect(createdCompetition(null)).toBeNull();
    expect(createdCompetition('x')).toBeNull();
  });

  it('boardRows reads the board at the root or under competition; garbage → []', () => {
    const rows = [
      { user_id: 'u1', name: 'Ada', baseline_value: 10000, value: 10500, return_pct: 5, rank: 1 },
    ];
    expect(boardRows({ board: rows })).toEqual(rows);
    expect(boardRows({ competition: { board: rows } })).toEqual(rows);
    expect(boardRows({ board: 'x' })).toEqual([]);
    expect(boardRows(undefined)).toEqual([]);
    expect(boardRows(42)).toEqual([]);
  });

  it('detailStatus reads status at the root or under competition; garbage → null', () => {
    expect(detailStatus({ status: 'ended' })).toBe('ended');
    expect(detailStatus({ competition: { status: 'running' } })).toBe('running');
    expect(detailStatus({})).toBeNull();
    expect(detailStatus(null)).toBeNull();
  });
});

describe('local countdown math', () => {
  const now = Date.parse('2026-07-12T00:00:00Z');

  it('running → milliseconds until ends_at, clamped at 0 once past', () => {
    expect(compRemainingMs(comp(), now)).toBe(3_600_000);
    expect(compRemainingMs(comp({ ends_at: '2026-07-11T23:00:00Z' }), now)).toBe(0);
  });

  it('upcoming → counts down to starts_at; ended → always 0', () => {
    expect(
      compRemainingMs(comp({ status: 'upcoming', starts_at: '2026-07-12T00:01:30Z' }), now)
    ).toBe(90_000);
    expect(compRemainingMs(comp({ status: 'ended' }), now)).toBe(0);
  });

  it('unparsable timestamps → 0 (never NaN in the UI)', () => {
    expect(compRemainingMs(comp({ ends_at: 'not-a-date' }), now)).toBe(0);
  });

  it('formatCompCountdown renders H:MM:SS with unpadded hours up to 168', () => {
    expect(formatCompCountdown(0)).toBe('0:00:00');
    expect(formatCompCountdown(90_000)).toBe('0:01:30');
    expect(formatCompCountdown(3_661_000)).toBe('1:01:01');
    expect(formatCompCountdown(168 * 3_600_000)).toBe('168:00:00');
  });

  it('formatCompCountdown clamps negative and non-finite input to 0:00:00', () => {
    expect(formatCompCountdown(-5_000)).toBe('0:00:00');
    expect(formatCompCountdown(NaN)).toBe('0:00:00');
  });
});

describe('copyCompText', () => {
  const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');

  afterEach(() => {
    if (originalClipboard) {
      Object.defineProperty(navigator, 'clipboard', originalClipboard);
    } else {
      delete (navigator as unknown as Record<string, unknown>).clipboard;
    }
  });

  it('resolves true via navigator.clipboard and false when the API refuses or is missing', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    await expect(copyCompText('ABC234')).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith('ABC234');

    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: jest.fn().mockRejectedValue(new Error('denied')) },
      configurable: true,
    });
    await expect(copyCompText('ABC234')).resolves.toBe(false);

    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
    await expect(copyCompText('ABC234')).resolves.toBe(false);
  });
});
