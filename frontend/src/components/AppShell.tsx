/**
 * AppShell.tsx — shared chrome for the new pages (P1 §2):
 * /market, /symbol, /journal, /arena.
 *
 * Header + NewsTicker on top, the page's main content in the middle, the
 * SAME ChatPanel component docked on the right (open/collapse state lives in
 * uiStore so it survives navigation), StatusBar at the bottom. Desktop uses
 * viewport-locked internal panels; narrow screens return to natural document
 * scrolling and present chat as a closeable drawer.
 *
 * The trade desk (/) does NOT use AppShell — index.tsx keeps its own JSX
 * (index.test.tsx contract: Dashboard renders its chrome itself).
 */
import type { ReactNode } from 'react';
import { useSWRConfig, mutate as swrGlobalMutate } from 'swr';
import Header from '@/components/Header';
import NewsTicker from '@/components/NewsTicker';
import StatusBar from '@/components/StatusBar';
import ResponsiveChatDock from '@/components/ResponsiveChatDock';
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

// P2 §7: AI strategy actions also touch the strategies list. The key set
// above is pinned by AppShell.test.tsx (exactly five useSWRConfig-mutate
// calls), so this key revalidates through SWR's module-level mutate instead —
// the app mounts no custom SWRConfig provider, so both entry points address
// the same default cache and the production effect is identical.
export const STRATEGIES_REVALIDATE_KEY = '/api/strategies';

export default function AppShell({ children }: { children: ReactNode }) {
  const chatOpen = useUiStore((s) => s.chatOpen);
  const setChatOpen = useUiStore((s) => s.setChatOpen);
  const { mutate } = useSWRConfig();

  const refreshAfterTrade = () => {
    for (const key of TRADE_REVALIDATE_KEYS) {
      void mutate(key);
    }
    // Module-level mutate (same default cache — see STRATEGIES_REVALIDATE_KEY
    // note). Guarded: jest suites mock 'swr' without the named export.
    if (typeof swrGlobalMutate === 'function') void swrGlobalMutate(STRATEGIES_REVALIDATE_KEY);
  };

  return (
    <div
      data-testid="app-shell-root"
      className="min-h-screen md:h-screen md:overflow-hidden flex flex-col bg-terminal-bg text-terminal-text font-mono"
    >
      <Header />
      <NewsTicker />
      <div
        data-testid="app-shell-layout"
        className="relative flex flex-col md:flex-row gap-2 lg:gap-4 p-2 lg:p-4 flex-1 min-h-0 md:overflow-hidden"
      >
        {/* Main content — the page decides its own internal layout/scrolling.
            A <div>, not <main>: _app.tsx already wraps every page in the
            single top-level <main>, and landmarks must not nest. */}
        <div className="w-full flex-1 min-h-0 min-w-0 overflow-x-auto md:overflow-auto">
          {children}
        </div>

        {/* At xl this is a normal dock; below xl it becomes a closeable drawer. */}
        <ResponsiveChatDock
          open={chatOpen}
          onToggle={() => setChatOpen(!chatOpen)}
          onNewTrade={refreshAfterTrade}
        />
      </div>
      <StatusBar />
    </div>
  );
}
