/**
 * AppShell.tsx — shared chrome for the new pages (P1 §2):
 * /market, /symbol, /journal, /arena.
 *
 * Header + NewsTicker on top, the page's main content in the middle, the
 * SAME ChatPanel component docked on the right (open/collapse state lives in
 * uiStore so it survives navigation), StatusBar at the bottom. The root uses
 * the trade desk's viewport-lock pattern: the page itself never scrolls —
 * panels scroll internally.
 *
 * The trade desk (/) does NOT use AppShell — index.tsx keeps its own JSX
 * (index.test.tsx contract: Dashboard renders its chrome itself).
 */
import type { ReactNode } from 'react';
import { useSWRConfig } from 'swr';
import Header from '@/components/Header';
import NewsTicker from '@/components/NewsTicker';
import StatusBar from '@/components/StatusBar';
import ChatPanel from '@/components/ChatPanel';
import { useUiStore } from '@/stores/uiStore';

// Same key set as index.tsx's refreshAfterTrade + mutateWatchlist — AI actions
// can touch the portfolio, blotter, open orders, rules, and the watchlist.
export const TRADE_REVALIDATE_KEYS = [
  '/api/portfolio/',
  '/api/portfolio/trades',
  '/api/portfolio/orders?status=open',
  '/api/rules',
  '/api/watchlist/',
] as const;

export default function AppShell({ children }: { children: ReactNode }) {
  const chatOpen = useUiStore((s) => s.chatOpen);
  const setChatOpen = useUiStore((s) => s.setChatOpen);
  const { mutate } = useSWRConfig();

  const refreshAfterTrade = () => {
    for (const key of TRADE_REVALIDATE_KEYS) {
      void mutate(key);
    }
  };

  return (
    <div className="h-screen overflow-hidden flex flex-col bg-terminal-bg text-terminal-text font-mono">
      <Header />
      <NewsTicker />
      <div className="flex gap-4 p-4 flex-1 min-h-0 overflow-hidden">
        {/* Main content — the page decides its own internal layout/scrolling.
            A <div>, not <main>: _app.tsx already wraps every page in the
            single top-level <main>, and landmarks must not nest. */}
        <div className="flex-1 min-h-0 min-w-0 overflow-auto">{children}</div>

        {/* Chat dock — fixed width, collapsible, same component as the desk */}
        <div
          className={`shrink-0 overflow-hidden transition-all duration-300 border-l border-terminal-border ${
            chatOpen ? 'w-80' : 'w-8'
          }`}
        >
          <ChatPanel
            open={chatOpen}
            onToggle={() => setChatOpen(!chatOpen)}
            onNewTrade={refreshAfterTrade}
          />
        </div>
      </div>
      <StatusBar />
    </div>
  );
}
