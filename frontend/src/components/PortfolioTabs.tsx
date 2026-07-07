/**
 * PortfolioTabs.tsx — Positions | Orders | Fills | Rules | Backtest |
 * Analytics | Board tab strip (FRONTEND_REALISM.md §1.3/§3.2, ROADMAP M5)
 *
 * Positions stays the default tab so the existing E2E data-testid contract
 * (positions-table, position-row-<TICKER>) holds on page load. The active
 * tab lives in uiStore so other components can switch it — RulesTable's
 * "test" button jumps here to the Backtest tab with a prefilled config.
 */
import PositionsTable from './PositionsTable';
import OpenOrdersTable from './OpenOrdersTable';
import OrdersTable from './OrdersTable';
import RulesTable from './RulesTable';
import BacktestPanel from './BacktestPanel';
import AnalyticsPanel from './AnalyticsPanel';
import Leaderboard from './Leaderboard';
import { useUiStore, type PortfolioTab } from '@/stores/uiStore';

const TABS: { key: PortfolioTab; label: string; testid: string }[] = [
  { key: 'positions', label: 'Positions', testid: 'tab-positions' },
  { key: 'orders', label: 'Orders', testid: 'tab-orders' },
  { key: 'fills', label: 'Fills', testid: 'tab-fills' },
  { key: 'rules', label: 'Rules', testid: 'tab-rules' },
  { key: 'backtest', label: 'Backtest', testid: 'tab-backtest' },
  { key: 'analytics', label: 'Analytics', testid: 'tab-analytics' },
  { key: 'board', label: 'Board', testid: 'tab-board' },
];

export default function PortfolioTabs() {
  const tab = useUiStore((s) => s.portfolioTab);
  const setTab = useUiStore((s) => s.setPortfolioTab);

  const tabClass = (t: PortfolioTab) =>
    `px-3 py-1 text-xs font-semibold uppercase tracking-wide border-b-2 transition-colors ${
      tab === t
        ? 'border-terminal-accent text-terminal-text'
        : 'border-transparent text-terminal-muted hover:text-terminal-text'
    }`;

  return (
    <div>
      <div className="flex border-b border-terminal-border">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            data-testid={t.testid}
            className={tabClass(t.key)}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'positions' && <PositionsTable />}
      {tab === 'orders' && <OpenOrdersTable />}
      {tab === 'fills' && <OrdersTable />}
      {tab === 'rules' && <RulesTable />}
      {tab === 'backtest' && <BacktestPanel />}
      {tab === 'analytics' && <AnalyticsPanel />}
      {tab === 'board' && <Leaderboard />}
    </div>
  );
}
