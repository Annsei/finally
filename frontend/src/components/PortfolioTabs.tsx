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
import { useMarketProfile } from '@/lib/marketProfile';
import { useT } from '@/lib/i18n';

const TABS: { key: PortfolioTab; labelKey: string; testid: string }[] = [
  { key: 'positions', labelKey: 'tabs.positions', testid: 'tab-positions' },
  { key: 'orders', labelKey: 'tabs.orders', testid: 'tab-orders' },
  { key: 'fills', labelKey: 'tabs.fills', testid: 'tab-fills' },
  { key: 'rules', labelKey: 'tabs.rules', testid: 'tab-rules' },
  { key: 'backtest', labelKey: 'tabs.backtest', testid: 'tab-backtest' },
  { key: 'analytics', labelKey: 'tabs.analytics', testid: 'tab-analytics' },
  { key: 'board', labelKey: 'tabs.board', testid: 'tab-board' },
];

export default function PortfolioTabs() {
  const t = useT();
  const profile = useMarketProfile();
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
      <div className="flex overflow-x-auto whitespace-nowrap border-b border-terminal-border">
        {TABS.map((tab_) => (
          <button
            key={tab_.key}
            type="button"
            data-testid={tab_.testid}
            className={tabClass(tab_.key)}
            onClick={() => setTab(tab_.key)}
          >
            {t(tab_.labelKey)}
          </button>
        ))}
      </div>
      {tab === 'positions' && <PositionsTable />}
      {tab === 'orders' && <OpenOrdersTable />}
      {tab === 'fills' && <OrdersTable />}
      {tab === 'rules' && <RulesTable />}
      {tab === 'backtest' && <BacktestPanel key={profile.market} profile={profile} />}
      {tab === 'analytics' && <AnalyticsPanel />}
      {tab === 'board' && <Leaderboard />}
    </div>
  );
}
