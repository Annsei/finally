import { useState, useEffect, useRef } from 'react';
import useSWR, { useSWRConfig } from 'swr';
import { usePriceStream } from '@/hooks/usePriceStream';
import Header from '@/components/Header';
import NewsTicker from '@/components/NewsTicker';
import StatusBar from '@/components/StatusBar';
import WatchlistPanel from '@/components/WatchlistPanel';
import MainChart from '@/components/MainChart';
import PnLChart from '@/components/PnLChart';
import PortfolioHeatmap from '@/components/PortfolioHeatmap';
import PortfolioTabs from '@/components/PortfolioTabs';
import TradeBar from '@/components/TradeBar';
import ChatPanel from '@/components/ChatPanel';
import { fetcher } from '@/lib/fetcher';
import { TICKER_DIRECTORY } from '@/lib/tickers';
import type { WatchlistResponse, PortfolioResponse } from '@/types/market';

export default function Dashboard() {
  // Single SSE connection for the page lifetime — call ONCE at root (Anti-pattern guard T-4-ES)
  usePriceStream();

  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);

  // Chat panel open by default (D-09)
  const [chatOpen, setChatOpen] = useState(true);

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

  // Auto-select first ticker on load (D-03), and re-select when the current
  // selection is removed from the watchlist (manually or via AI) — otherwise
  // MainChart freezes and TradeBar stays pre-filled with an untracked ticker
  useEffect(() => {
    const tickers = watchlistData?.tickers;
    if (!tickers) return;
    const stillWatched = selectedTicker !== null && tickers.some((t) => t.ticker === selectedTicker);
    if (!stillWatched) {
      setSelectedTicker(tickers.length ? tickers[0].ticker : null);
    }
  }, [watchlistData, selectedTicker]);

  // Keyboard shortcuts (FRONTEND_REALISM §3.3) — inactive while typing in a field.
  // "/" focuses the watchlist search, ↑↓ move the selection, B/S press Buy/Sell
  // (TradeBar's own validation still gates execution).
  const tickerListRef = useRef<string[]>([]);
  tickerListRef.current = watchlistData?.tickers?.map((t) => t.ticker) ?? [];
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
        const list = tickerListRef.current;
        if (!list.length) return;
        e.preventDefault();
        setSelectedTicker((current) => {
          const idx = current ? list.indexOf(current) : -1;
          const next =
            e.key === 'ArrowDown'
              ? Math.min(idx + 1, list.length - 1)
              : Math.max(idx - 1, 0);
          return list[next] ?? current;
        });
      } else if (e.key === 'b' || e.key === 'B') {
        document.querySelector<HTMLButtonElement>('[data-testid="trade-buy-button"]')?.click();
      } else if (e.key === 's' || e.key === 'S') {
        document.querySelector<HTMLButtonElement>('[data-testid="trade-sell-button"]')?.click();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  return (
    // h-screen + overflow-hidden locks the terminal to the viewport: the page
    // itself never scrolls — each column (watchlist, center, chat history)
    // scrolls internally instead. min-h-screen let long chat history stretch
    // the whole page, defeating ChatPanel's internal overflow-y-auto.
    <div className="h-screen overflow-hidden bg-terminal-bg text-terminal-text font-mono flex flex-col">
      <Header />
      <NewsTicker />
      <div className="flex gap-4 p-4 flex-1 min-h-0 overflow-hidden">
        {/* Column 1: Watchlist */}
        <WatchlistPanel
          selectedTicker={selectedTicker}
          onSelectTicker={setSelectedTicker}
        />

        {/* Column 2: Center — main chart → [heatmap | P&L] → trade bar → positions (D-02/D-04) */}
        <div className="flex-1 flex flex-col gap-4 overflow-auto">
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
          <div className="flex gap-4">
            <PortfolioHeatmap />
            <PnLChart />
          </div>
          <TradeBar
            selectedTicker={selectedTicker}
            onTradeComplete={refreshAfterTrade}
          />
          <PortfolioTabs />
        </div>

        {/* Column 3: Chat — fixed width, collapsible via toggle (D-09) */}
        <div
          className={`shrink-0 overflow-hidden transition-all duration-300 border-l border-terminal-border ${
            chatOpen ? 'w-80' : 'w-8'
          }`}
        >
          <ChatPanel
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
