import { useState, useEffect } from 'react';
import useSWR from 'swr';
import { usePriceStream } from '@/hooks/usePriceStream';
import Header from '@/components/Header';
import WatchlistPanel from '@/components/WatchlistPanel';
import MainChart from '@/components/MainChart';
import PnLChart from '@/components/PnLChart';
import PortfolioHeatmap from '@/components/PortfolioHeatmap';
import PositionsTable from '@/components/PositionsTable';
import TradeBar from '@/components/TradeBar';
import ChatPanel from '@/components/ChatPanel';
import { fetcher } from '@/lib/fetcher';
import type { WatchlistResponse, PortfolioResponse } from '@/types/market';

export default function Dashboard() {
  // Single SSE connection for the page lifetime — call ONCE at root (Anti-pattern guard T-4-ES)
  usePriceStream();

  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);

  // Chat panel open by default (D-09)
  const [chatOpen, setChatOpen] = useState(true);

  // SWR for portfolio (bound mutate passed to TradeBar + ChatPanel for revalidation)
  const { mutate: mutatePortfolio } = useSWR<PortfolioResponse>('/api/portfolio/', fetcher);

  // SWR for watchlist (needed for auto-select D-03; mutate revalidates after AI watchlist changes)
  const { data: watchlistData, mutate: mutateWatchlist } = useSWR<WatchlistResponse>(
    '/api/watchlist/',
    fetcher
  );

  // Auto-select first ticker on load when none is selected (D-03)
  useEffect(() => {
    if (!selectedTicker && watchlistData?.tickers?.length) {
      setSelectedTicker(watchlistData.tickers[0].ticker);
    }
  }, [watchlistData, selectedTicker]);

  return (
    <div className="min-h-screen bg-terminal-bg text-terminal-text font-mono">
      <Header />
      <div className="flex gap-4 p-4 h-[calc(100vh-52px)] overflow-hidden">
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
              style={{ height: '264px' }}
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
            onTradeComplete={() => mutatePortfolio()}
          />
          <PositionsTable />
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
              // AI actions can affect both portfolio (trades) and watchlist
              // (watchlist_changes) — revalidate both so no panel goes stale
              void mutatePortfolio();
              void mutateWatchlist();
            }}
          />
        </div>
      </div>
    </div>
  );
}
