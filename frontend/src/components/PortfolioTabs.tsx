/**
 * PortfolioTabs.tsx — Positions | Orders | Fills tab strip
 * (FRONTEND_REALISM.md §1.3/§3.2)
 *
 * Positions stays the default tab so the existing E2E data-testid contract
 * (positions-table, position-row-<TICKER>) holds on page load. Orders shows
 * resting limit orders (cancellable); Fills is the executed-trade blotter.
 */
import { useState } from 'react';
import PositionsTable from './PositionsTable';
import OpenOrdersTable from './OpenOrdersTable';
import OrdersTable from './OrdersTable';
import RulesTable from './RulesTable';

type Tab = 'positions' | 'orders' | 'fills' | 'rules';

const TABS: { key: Tab; label: string; testid: string }[] = [
  { key: 'positions', label: 'Positions', testid: 'tab-positions' },
  { key: 'orders', label: 'Orders', testid: 'tab-orders' },
  { key: 'fills', label: 'Fills', testid: 'tab-fills' },
  { key: 'rules', label: 'Rules', testid: 'tab-rules' },
];

export default function PortfolioTabs() {
  const [tab, setTab] = useState<Tab>('positions');

  const tabClass = (t: Tab) =>
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
    </div>
  );
}
