import { useState } from 'react';
import { usePriceStream } from '@/hooks/usePriceStream';
import Header from '@/components/Header';
import WatchlistPanel from '@/components/WatchlistPanel';

export default function Dashboard() {
  // Single SSE connection for the page lifetime (call ONCE at root — Pitfall 3)
  usePriceStream();

  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);

  return (
    <div className="min-h-screen bg-terminal-bg text-terminal-text font-mono">
      <Header />
      <div className="flex gap-4 p-4">
        <WatchlistPanel
          selectedTicker={selectedTicker}
          onSelectTicker={setSelectedTicker}
        />
        {/* Phase 4: main chart area, portfolio panels, and AI chat go here */}
      </div>
    </div>
  );
}
