import { useState, useEffect, useMemo } from 'react';
import useSWR, { useSWRConfig } from 'swr';
import Header from '@/components/Header';
import NewsTicker from '@/components/NewsTicker';
import StatusBar from '@/components/StatusBar';
import WatchlistPanel from '@/components/WatchlistPanel';
import MainChart from '@/components/MainChart';
import PnLChart from '@/components/PnLChart';
import PortfolioHeatmap from '@/components/PortfolioHeatmap';
import PortfolioTabs from '@/components/PortfolioTabs';
import TradeBar from '@/components/TradeBar';
import ResponsiveChatDock from '@/components/ResponsiveChatDock';
import { fetcher } from '@/lib/fetcher';
import { TICKER_DIRECTORY } from '@/lib/tickers';
import { useUiStore } from '@/stores/uiStore';
import type { WatchlistResponse, PortfolioResponse } from '@/types/market';

export default function Dashboard() {
  // SSE connection lives in _app (P1 §2) — do NOT call usePriceStream() here.

  const [preferredTicker, setPreferredTicker] = useState<string | null>(null);

  // Chat panel open by default (D-09); state lives in uiStore (P1 §2) so it
  // survives client-side navigation to /market, /journal, /arena.
  const chatOpen = useUiStore((s) => s.chatOpen);
  const setChatOpen = useUiStore((s) => s.setChatOpen);

  // SWR for portfolio (bound mutate passed to TradeBar + ChatPanel for revalidation)
  const { mutate: mutatePortfolio } = useSWR<PortfolioResponse>('/api/portfolio/', fetcher);

  // Global mutate — refreshes the fills blotter and open orders after any trade
  const { mutate: globalMutate } = useSWRConfig();
  const refreshAfterTrade = () => {
    void mutatePortfolio();
    void globalMutate('/api/portfolio/trades');
    void globalMutate('/api/portfolio/orders?status=open');
    void globalMutate('/api/rules');
  };

  // SWR for watchlist (needed for auto-select D-03; mutate revalidates after AI watchlist changes)
  const { data: watchlistData, mutate: mutateWatchlist } = useSWR<WatchlistResponse>(
    '/api/watchlist/',
    fetcher
  );

  // A missing/removed preference falls back to the first watched symbol. This
  // is derived during render, so watchlist changes do not need a state-sync
  // effect and the chart never keeps an untracked ticker.
  const tickerList = useMemo(
    () => watchlistData?.tickers?.map((item) => item.ticker) ?? [],
    [watchlistData?.tickers]
  );
  const selectedTicker =
    preferredTicker != null && tickerList.includes(preferredTicker)
      ? preferredTicker
      : (tickerList[0] ?? null);

  // Keyboard shortcuts (FRONTEND_REALISM §3.3) — inactive while typing in a field.
  // "/" focuses the watchlist search, ↑↓ move the selection, B/S press Buy/Sell
  // (TradeBar's own validation still gates execution).
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const typing =
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        (target != null && target.isContentEditable);
      if (typing) return;

      if (e.key === '/') {
        e.preventDefault();
        document
          .querySelector<HTMLInputElement>('[data-testid="watchlist-add-input"]')
          ?.focus();
      } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        const list = tickerList;
        if (!list.length) return;
        e.preventDefault();
        setPreferredTicker((current) => {
          const active = current != null && list.includes(current) ? current : (list[0] ?? null);
          const idx = active ? list.indexOf(active) : -1;
          const next =
            e.key === 'ArrowDown'
              ? Math.min(idx + 1, list.length - 1)
              : Math.max(idx - 1, 0);
          return list[next] ?? active;
        });
      } else if (e.key === 'b' || e.key === 'B') {
        document.querySelector<HTMLButtonElement>('[data-testid="trade-buy-button"]')?.click();
      } else if (e.key === 's' || e.key === 'S') {
        document.querySelector<HTMLButtonElement>('[data-testid="trade-sell-button"]')?.click();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [tickerList]);

  return (
    // Desktop panels scroll internally. Below md the document returns to
    // natural vertical scrolling, avoiding clipped fixed-column content.
    <div
      data-testid="dashboard-root"
      className="min-h-screen md:h-screen md:overflow-hidden bg-terminal-bg text-terminal-text font-mono flex flex-col"
    >
      <Header />
      <NewsTicker />
      <div
        data-testid="dashboard-layout"
        className="relative flex flex-col md:flex-row gap-2 lg:gap-4 p-2 lg:p-4 flex-1 min-h-0 md:overflow-hidden"
      >
        {/* Column 1: Watchlist */}
        <WatchlistPanel
          selectedTicker={selectedTicker}
          onSelectTicker={setPreferredTicker}
        />

        {/* Column 2: Center — main chart → [heatmap | P&L] → trade bar → positions (D-02/D-04) */}
        <div
          data-testid="dashboard-main"
          className="w-full min-w-0 flex-1 flex flex-col gap-2 lg:gap-4 overflow-x-auto md:overflow-auto"
        >
          {selectedTicker ? (
            <MainChart ticker={selectedTicker} />
          ) : (
            // Placeholder keeps the layout stable until the watchlist loads and
            // auto-select picks the first ticker (matches MainChart's footprint)
            <div
              data-testid="main-chart-placeholder"
              className="flex items-center justify-center text-terminal-muted text-xs"
              style={{ height: '304px' }}
            >
              Waiting for market data…
            </div>
          )}
          <div className="flex flex-col lg:flex-row gap-2 lg:gap-4">
            <PortfolioHeatmap />
            <PnLChart />
          </div>
          <TradeBar
            selectedTicker={selectedTicker}
            onTradeComplete={refreshAfterTrade}
          />
          <PortfolioTabs />
        </div>

        {/* xl: docked third column; md/sm: accessible closeable overlay drawer. */}
        <ResponsiveChatDock
          open={chatOpen}
          onToggle={() => setChatOpen(!chatOpen)}
          onNewTrade={() => {
            // AI actions can affect portfolio (trades), the blotter, and the
            // watchlist (watchlist_changes) — revalidate all so nothing goes stale
            refreshAfterTrade();
            void mutateWatchlist();
          }}
        />
      </div>
      <StatusBar />

      {/* Shared autocomplete directory — referenced by ticker inputs via list="ticker-suggestions" */}
      <datalist id="ticker-suggestions">
        {TICKER_DIRECTORY.map((t) => (
          <option key={t.symbol} value={t.symbol}>
            {t.name}
          </option>
        ))}
      </datalist>
    </div>
  );
}
