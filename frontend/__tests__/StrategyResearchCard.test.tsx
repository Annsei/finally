/**
 * StrategyResearchCard.test.tsx — chat research comparison card (D4 §3.2/§3.5).
 *
 * Rendering:  header (ticker · days · count), completed candidates in rank
 *             order regardless of payload order, recommended badge iff
 *             recommended_strategy_id matches, failed rows in the down colour,
 *             null-recommendation note, batch-level guard errors
 * Deploy:     PATCH /api/strategies/{id} {"status": "live"} round trip →
 *             research-deployed element + onDeployed + revalidation of BOTH
 *             the status=all key (bound mutate) and the plain
 *             '/api/strategies' key (module-level mutate — AppShell's
 *             STRATEGIES_REVALIDATE_KEY); body.error surfaces inline and
 *             the button survives
 * Derivation: current status read from useSWR('/api/strategies?status=all')
 *             — the ONLY list view that returns archived rows (the default
 *             view hides them server-side) — live → deployed element,
 *             archived → archived chip, ids missing from the all view
 *             disable the button (deleted strategies)
 * i18n:       research.* namespace is keyset-aligned between en and zh
 */
import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import useSWR, { mutate as globalMutate } from 'swr';
import type {
  BacktestStats,
  ResearchCandidateOutcome,
  ResearchOutcome,
  Strategy,
} from '@/types/market';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
  // Named module-level mutate: the card revalidates the default-view key
  // ('/api/strategies') with it after a successful deploy.
  mutate: jest.fn(),
}));

import StrategyResearchCard from '@/components/chat/StrategyResearchCard';
import { DICTIONARIES } from '@/lib/i18n';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;
const mockGlobalMutate = globalMutate as unknown as jest.Mock;

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------
const stats = (over: Partial<BacktestStats> = {}): BacktestStats => ({
  total_return_pct: 12.3,
  buy_hold_return_pct: 8.0,
  max_drawdown_pct: 4.5,
  final_equity: 11230,
  fires: 6,
  round_trips: 6,
  win_rate: 0.67,
  avg_win: 140,
  avg_loss: -80,
  profit_factor: 2.1,
  commission_paid: 0,
  rejections: { insufficient_cash: 0 },
  ...over,
});

const cand = (over: Partial<ResearchCandidateOutcome> = {}): ResearchCandidateOutcome => ({
  status: 'completed',
  name: 'Candidate',
  hypothesis: 'A one-line rationale.',
  strategy_id: 'st-1',
  run_id: 'run-1',
  score: 10.05,
  rank: 1,
  traded: true,
  stats: stats(),
  ...over,
});

const outcome = (over: Partial<ResearchOutcome> = {}): ResearchOutcome => ({
  status: 'completed',
  ticker: 'AAPL',
  days: 120,
  candidates: [],
  recommended_strategy_id: null,
  ...over,
});

const strategyRow = (id: string, status: Strategy['status']): Strategy => ({
  id,
  name: `Strategy ${id}`,
  ticker: 'AAPL',
  status,
  entry: { all: [{ field: 'day_change_pct', op: 'below', value: -3 }] },
  exits: { take_profit_pct: 4, stop_loss_pct: 3 },
  sizing: { mode: 'cash_pct', pct: 20 },
  template: null,
  created_at: '2026-07-07T00:00:00Z',
  deployed_at: null,
  open_qty: 0,
  open_price: null,
  opened_at: null,
  entered_count: 0,
  exited_count: 0,
  last_fired_at: null,
  runs_count: 1,
  realized_pnl: 0,
});

const strategiesMutate = jest.fn();

// listData === null → strategies list still loading (data: undefined).
// The card derives status from the status=all view — the only one that
// returns archived rows (the default view hides them server-side).
function mockSwr(listData: { strategies: Strategy[] } | null) {
  mockUseSWR.mockImplementation(((key: string) => {
    if (key === '/api/strategies?status=all') {
      return { data: listData ?? undefined, mutate: strategiesMutate };
    }
    // '/api/market/profile' etc. — undefined keeps the en/US defaults.
    return { data: undefined, mutate: jest.fn() };
  }) as never);
}

// A completed 3-candidate batch delivered OUT of rank order + one failed row.
const shuffledBatch = outcome({
  candidates: [
    cand({ name: 'RSI Rebound', strategy_id: 'st-2', run_id: 'run-2', rank: 2, score: 8.1 }),
    cand({ name: 'Golden Cross', strategy_id: 'st-1', run_id: 'run-1', rank: 1, score: 10.05 }),
    { status: 'failed', name: 'Broken Idea', error: 'strategy must define at least one exit' },
  ],
  recommended_strategy_id: 'st-1',
});

describe('StrategyResearchCard', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockSwr({ strategies: [strategyRow('st-1', 'draft'), strategyRow('st-2', 'draft')] });
    global.fetch = jest.fn();
  });

  // -------------------------------------------------------------------------
  // Rendering — ranked order, recommended badge, failed row
  // -------------------------------------------------------------------------
  it('renders header and completed candidates in rank order, failed rows last', () => {
    render(<StrategyResearchCard outcome={shuffledBatch} />);

    const card = screen.getByTestId('research-card');
    expect(card.textContent).toContain('AI Research: AAPL');
    expect(card.textContent).toContain('120 trading days');
    expect(card.textContent).toContain('2/3'); // completed / total

    const rows = screen.getAllByTestId('research-candidate');
    expect(rows).toHaveLength(3);
    // Payload order was rank 2, rank 1, failed — render order is rank 1 first.
    expect(rows[0].textContent).toContain('#1');
    expect(rows[0].textContent).toContain('Golden Cross');
    expect(rows[1].textContent).toContain('#2');
    expect(rows[1].textContent).toContain('RSI Rebound');
    expect(rows[2].textContent).toContain('Broken Idea');
  });

  it('shows the compact stats line with signed return and score', () => {
    render(<StrategyResearchCard outcome={shuffledBatch} />);

    const rows = screen.getAllByTestId('research-candidate');
    expect(rows[0].textContent).toContain('+12.3%'); // signed()
    expect(rows[0].textContent).toContain('4.5%'); // max drawdown magnitude
    expect(rows[0].textContent).toContain('67%'); // win rate
    expect(rows[0].textContent).toContain('10.05'); // score
    // Direction colour comes from pnlClass
    expect(rows[0].querySelector('.text-terminal-up')?.textContent).toBe('+12.3%');
  });

  it('marks only the matching candidate as recommended', () => {
    render(<StrategyResearchCard outcome={shuffledBatch} />);

    const badges = screen.getAllByTestId('research-recommended');
    expect(badges).toHaveLength(1);
    const rows = screen.getAllByTestId('research-candidate');
    expect(rows[0].contains(badges[0])).toBe(true); // rank-1 Golden Cross row
    expect(screen.queryByText('No recommendation — no candidate traded in the window.')).toBeNull();
  });

  it('null recommendation renders the muted note and no badge', () => {
    render(
      <StrategyResearchCard
        outcome={outcome({
          candidates: [cand({ name: 'Sleeper', traded: false, stats: stats({ round_trips: 0, win_rate: null }) })],
          recommended_strategy_id: null,
        })}
      />
    );

    expect(screen.queryByTestId('research-recommended')).toBeNull();
    expect(
      screen.getByText('No recommendation — no candidate traded in the window.')
    ).toBeTruthy();
  });

  it('failed candidates render name + error in the down colour, without deploy controls', () => {
    render(
      <StrategyResearchCard
        outcome={outcome({
          status: 'failed',
          candidates: [{ status: 'failed', name: 'Broken Idea', error: 'unknown ticker ZZZZ' }],
        })}
      />
    );

    const row = screen.getByTestId('research-candidate');
    expect(row.textContent).toContain('Broken Idea');
    expect(row.textContent).toContain('unknown ticker ZZZZ');
    expect(row.querySelectorAll('.text-terminal-down').length).toBeGreaterThan(0);
    expect(screen.queryByTestId('research-deploy')).toBeNull();
    expect(screen.queryByTestId('research-deployed')).toBeNull();
  });

  it('a batch-level guard error renders inside the card', () => {
    render(
      <StrategyResearchCard
        outcome={outcome({
          status: 'failed',
          candidates: [],
          error: 'research requires 2 to 4 candidates',
          recommended_strategy_id: undefined,
        })}
      />
    );

    const card = screen.getByTestId('research-card');
    expect(card.textContent).toContain('research requires 2 to 4 candidates');
    expect(screen.queryAllByTestId('research-candidate')).toHaveLength(0);
  });

  it('links each completed candidate to its run and strategy pages', () => {
    render(<StrategyResearchCard outcome={shuffledBatch} />);

    const links = screen.getAllByRole('link').map((a) => a.getAttribute('href'));
    expect(links).toContain('/run?id=run-1');
    expect(links).toContain('/strategy?id=st-1');
    expect(links).toContain('/run?id=run-2');
    expect(links).toContain('/strategy?id=st-2');
  });

  // -------------------------------------------------------------------------
  // Deploy — PATCH round trip, deployed flip, error path
  // -------------------------------------------------------------------------
  it('deploy PATCHes {"status":"live"}, flips to research-deployed, revalidates and notifies', async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ strategy: strategyRow('st-1', 'live') }),
    });
    const onDeployed = jest.fn();

    render(
      <StrategyResearchCard
        outcome={outcome({ candidates: [cand()], recommended_strategy_id: 'st-1' })}
        onDeployed={onDeployed}
      />
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId('research-deploy'));
    });

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/strategies/st-1',
      expect.objectContaining({
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'live' }),
      })
    );
    await waitFor(() => {
      expect(screen.getByTestId('research-deployed')).toBeTruthy();
    });
    expect(screen.queryByTestId('research-deploy')).toBeNull();
    expect(onDeployed).toHaveBeenCalledTimes(1);
    // Both list views revalidate: the card's own status=all key (bound
    // mutate) and the plain key the strategies page reads (global mutate —
    // AppShell's STRATEGIES_REVALIDATE_KEY).
    expect(strategiesMutate).toHaveBeenCalled();
    expect(mockGlobalMutate).toHaveBeenCalledWith('/api/strategies');
  });

  it('a failed deploy surfaces body.error inline and keeps the button', async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: 'deploying requires at least one exit' }),
    });
    const onDeployed = jest.fn();

    render(
      <StrategyResearchCard outcome={outcome({ candidates: [cand()] })} onDeployed={onDeployed} />
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId('research-deploy'));
    });

    await waitFor(() => {
      expect(screen.getByTestId('research-deploy-error').textContent).toBe(
        'deploying requires at least one exit'
      );
    });
    expect(screen.getByTestId('research-deploy')).not.toBeDisabled();
    expect(screen.queryByTestId('research-deployed')).toBeNull();
    expect(onDeployed).not.toHaveBeenCalled();
  });

  it('a non-JSON error body falls back to the generic status message', async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => {
        throw new Error('not json');
      },
    });

    render(<StrategyResearchCard outcome={outcome({ candidates: [cand()] })} />);

    await act(async () => {
      fireEvent.click(screen.getByTestId('research-deploy'));
    });

    await waitFor(() => {
      expect(screen.getByTestId('research-deploy-error').textContent).toBe('Deploy failed (500)');
    });
  });

  // -------------------------------------------------------------------------
  // Status derivation from GET /api/strategies?status=all (re-opened history)
  // -------------------------------------------------------------------------
  it('a strategy already live in the list renders research-deployed instead of a button', () => {
    mockSwr({ strategies: [strategyRow('st-1', 'live')] });

    render(<StrategyResearchCard outcome={outcome({ candidates: [cand()] })} />);

    expect(screen.getByTestId('research-deployed')).toBeTruthy();
    expect(screen.queryByTestId('research-deploy')).toBeNull();
  });

  it('an archived strategy renders the archived chip instead of a button', () => {
    mockSwr({ strategies: [strategyRow('st-1', 'archived')] });

    render(<StrategyResearchCard outcome={outcome({ candidates: [cand()] })} />);

    expect(screen.getByTestId('research-archived')).toBeTruthy();
    expect(screen.queryByTestId('research-deploy')).toBeNull();
    expect(screen.queryByTestId('research-deployed')).toBeNull();
  });

  it('server-reported archived outranks the local just-deployed flag', async () => {
    // Deploy from the card (sets the local deployedIds flag), then the
    // status=all list reports the id as archived (e.g. archived on
    // /strategies while the dock stayed mounted): the archived chip must
    // win over the stale local flag.
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ strategy: strategyRow('st-1', 'live') }),
    });
    const batch = outcome({ candidates: [cand()] });
    const { rerender } = render(<StrategyResearchCard outcome={batch} />);

    await act(async () => {
      fireEvent.click(screen.getByTestId('research-deploy'));
    });
    await waitFor(() => {
      expect(screen.getByTestId('research-deployed')).toBeTruthy();
    });

    mockSwr({ strategies: [strategyRow('st-1', 'archived')] });
    rerender(<StrategyResearchCard outcome={batch} />);

    expect(screen.getByTestId('research-archived')).toBeTruthy();
    expect(screen.queryByTestId('research-deployed')).toBeNull();
    expect(screen.queryByTestId('research-deploy')).toBeNull();
  });

  it('an id missing from the loaded list (deleted strategy) disables the button', () => {
    mockSwr({ strategies: [strategyRow('st-other', 'draft')] });

    render(<StrategyResearchCard outcome={outcome({ candidates: [cand()] })} />);

    expect(screen.getByTestId('research-deploy')).toBeDisabled();
  });

  it('while the strategies list is still loading the button stays enabled (fresh drafts)', () => {
    mockSwr(null);

    render(<StrategyResearchCard outcome={outcome({ candidates: [cand()] })} />);

    expect(screen.getByTestId('research-deploy')).not.toBeDisabled();
  });
});

// ---------------------------------------------------------------------------
// i18n — the D4 §3.4 research.* namespace is aligned between en and zh
// ---------------------------------------------------------------------------
describe('i18n research.* namespace (D4 §3.4)', () => {
  const keysIn = (dict: Readonly<Record<string, string>>) =>
    Object.keys(dict)
      .filter((k) => k.startsWith('research.'))
      .sort();

  it('en and zh carry the identical, contract-mandated keyset', () => {
    const contractKeys = [
      'research.title',
      'research.days',
      'research.score',
      'research.return',
      'research.drawdown',
      'research.winRate',
      'research.trades',
      'research.deploy',
      'research.deploying',
      'research.deployed',
      'research.archived',
      'research.viewRun',
      'research.viewStrategy',
      'research.recommended',
      'research.failed',
      'research.noRecommendation',
      'research.prefill',
      'research.button',
    ].sort();
    expect(keysIn(DICTIONARIES.en)).toEqual(contractKeys);
    expect(keysIn(DICTIONARIES.zh)).toEqual(contractKeys);
    for (const key of contractKeys) {
      expect(DICTIONARIES.en[key].length).toBeGreaterThan(0);
      expect(DICTIONARIES.zh[key].length).toBeGreaterThan(0);
    }
  });

  it('the prefill keeps the LLM_MOCK trigger tokens in both languages', () => {
    // chat.py mock detection: "research" in lower / "研究" in the message.
    expect(DICTIONARIES.en['research.prefill'].toLowerCase()).toContain('research');
    expect(DICTIONARIES.zh['research.prefill']).toContain('研究');
  });
});
