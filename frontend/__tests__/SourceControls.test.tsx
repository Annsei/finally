/**
 * SourceControls.test.tsx — D1 §5 data-source primitives.
 *
 * Pure helpers:  runSourceKind (marker normalization: absent / "strategy" /
 *                provider strings / date_range fallback), runDateRange
 *                (shape validation), sourceLabel (i18n vs raw fallthrough)
 * Rendering:     SourceToggle segments + aria-pressed + onChange + disabled,
 *                SourceBadge label / data-source attr / date-range suffix
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import SourceToggle from '@/components/backtest/SourceToggle';
import SourceBadge, {
  runSourceKind,
  runDateRange,
  sourceLabel,
} from '@/components/backtest/SourceBadge';
import { makeT } from '@/lib/i18n';

const tEn = makeT('en');
const tZh = makeT('zh');

describe('runSourceKind (D1 §5 marker normalization)', () => {
  it('defaults to synthetic for pre-D1 payloads (no marker at all)', () => {
    expect(runSourceKind({})).toBe('synthetic');
    expect(runSourceKind(null)).toBe('synthetic');
    expect(runSourceKind(undefined)).toBe('synthetic');
  });

  it('passes explicit provider/source strings through', () => {
    expect(runSourceKind({ source: 'history' })).toBe('history');
    expect(runSourceKind({ source: 'sample' })).toBe('sample');
    expect(runSourceKind({ source: 'yfinance' })).toBe('yfinance');
    expect(runSourceKind({ source: 'akshare' })).toBe('akshare');
    expect(runSourceKind({ source: 'synthetic' })).toBe('synthetic');
  });

  it('skips the strategy-config discriminator and prefers data_source', () => {
    // backtest.py marks strategy-shaped configs with source:"strategy" — that
    // is an engine discriminator, not a data source.
    expect(runSourceKind({ source: 'strategy' })).toBe('synthetic');
    expect(runSourceKind({ source: 'strategy', data_source: 'sample' })).toBe('sample');
    // a strategy run that evaluated daily bars still reads as history
    expect(
      runSourceKind({ source: 'strategy', date_range: { from: '2026-01-02', to: '2026-07-01' } })
    ).toBe('history');
  });

  it('ignores non-string / empty markers', () => {
    expect(runSourceKind({ source: 42 })).toBe('synthetic');
    expect(runSourceKind({ source: '' })).toBe('synthetic');
  });
});

describe('runDateRange', () => {
  it('returns validated {from, to} and null otherwise', () => {
    const range = { from: '2026-01-02', to: '2026-07-01' };
    expect(runDateRange({ date_range: range })).toEqual(range);
    expect(runDateRange({})).toBeNull();
    expect(runDateRange({ date_range: null })).toBeNull();
    expect(runDateRange({ date_range: { from: 1, to: 2 } })).toBeNull();
    expect(runDateRange(undefined)).toBeNull();
  });
});

describe('sourceLabel', () => {
  it('maps the known kinds through i18n in both languages', () => {
    expect(sourceLabel(tEn, 'synthetic')).toBe('Simulated');
    expect(sourceLabel(tEn, 'history')).toBe('History');
    expect(sourceLabel(tEn, 'sample')).toBe('Sample');
    expect(sourceLabel(tEn, 'yfinance')).toBe('yfinance');
    expect(sourceLabel(tEn, 'akshare')).toBe('AKShare');
    expect(sourceLabel(tZh, 'synthetic')).toBe('模拟');
    expect(sourceLabel(tZh, 'sample')).toBe('样本');
  });

  it('renders unknown markers verbatim instead of a raw i18n key', () => {
    expect(sourceLabel(tEn, 'massive')).toBe('massive');
  });
});

describe('SourceToggle', () => {
  it('renders both segments under the group testid with aria-pressed state', () => {
    render(<SourceToggle testid="backtest-source" value="synthetic" onChange={jest.fn()} t={tEn} />);
    expect(screen.getByTestId('backtest-source')).toBeInTheDocument();
    const synthetic = screen.getByTestId('backtest-source-synthetic');
    const history = screen.getByTestId('backtest-source-history');
    expect(synthetic.textContent).toBe('Simulated');
    expect(history.textContent).toBe('History');
    expect(synthetic.getAttribute('aria-pressed')).toBe('true');
    expect(history.getAttribute('aria-pressed')).toBe('false');
  });

  it('clicking a segment reports the selection; disabled blocks it', () => {
    const onChange = jest.fn();
    const { rerender } = render(
      <SourceToggle testid="strategy-bt-source" value="synthetic" onChange={onChange} t={tEn} />
    );
    fireEvent.click(screen.getByTestId('strategy-bt-source-history'));
    expect(onChange).toHaveBeenCalledWith('history');

    onChange.mockClear();
    rerender(
      <SourceToggle
        testid="strategy-bt-source"
        value="synthetic"
        onChange={onChange}
        disabled
        t={tEn}
      />
    );
    fireEvent.click(screen.getByTestId('strategy-bt-source-history'));
    expect(onChange).not.toHaveBeenCalled();
  });

  it('renders the zh copy on the cn market language', () => {
    render(<SourceToggle testid="backtest-source" value="history" onChange={jest.fn()} t={tZh} />);
    expect(screen.getByTestId('backtest-source-synthetic').textContent).toBe('模拟');
    expect(screen.getByTestId('backtest-source-history').textContent).toBe('历史');
  });
});

describe('SourceBadge', () => {
  it('renders the i18n label and a data-source attribute', () => {
    render(<SourceBadge testid="backtest-source-badge" source="sample" t={tEn} />);
    const badge = screen.getByTestId('backtest-source-badge');
    expect(badge.textContent).toBe('Sample');
    expect(badge.getAttribute('data-source')).toBe('sample');
  });

  it('appends the evaluated date range when provided', () => {
    render(
      <SourceBadge
        testid="run-source-badge"
        source="history"
        dateRange={{ from: '2026-01-02', to: '2026-07-01' }}
        t={tEn}
      />
    );
    expect(screen.getByTestId('run-source-badge').textContent).toBe(
      'History2026-01-02 → 2026-07-01'
    );
  });

  it('styles synthetic as muted and non-synthetic with the blue accent', () => {
    const { rerender } = render(
      <SourceBadge testid="backtest-source-badge" source="synthetic" t={tEn} />
    );
    expect(screen.getByTestId('backtest-source-badge').className).toContain(
      'text-terminal-muted'
    );
    rerender(<SourceBadge testid="backtest-source-badge" source="history" t={tEn} />);
    expect(screen.getByTestId('backtest-source-badge').className).toContain('text-terminal-blue');
  });
});
