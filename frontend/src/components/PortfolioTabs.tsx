/**
 * PortfolioTabs.tsx — Positions | Orders tab strip (FRONTEND_REALISM.md §1.3)
 *
 * Positions stays the default tab so the existing E2E data-testid contract
 * (positions-table, position-row-<TICKER>) holds on page load.
 */
import { useState } from 'react';
import PositionsTable from './PositionsTable';
import OrdersTable from './OrdersTable';

type Tab = 'positions' | 'orders';

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
        <button
          type="button"
          data-testid="tab-positions"
          className={tabClass('positions')}
          onClick={() => setTab('positions')}
        >
          Positions
        </button>
        <button
          type="button"
          data-testid="tab-orders"
          className={tabClass('orders')}
          onClick={() => setTab('orders')}
        >
          Orders
        </button>
      </div>
      {tab === 'positions' ? <PositionsTable /> : <OrdersTable />}
    </div>
  );
}
