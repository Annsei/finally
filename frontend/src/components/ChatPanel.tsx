import { useState, useRef, useEffect } from 'react';
import useSWR from 'swr';
import { fetcher } from '@/lib/fetcher';
import type { ChatHistoryResponse, ChatPostResponse, TradeOutcome, WatchlistOutcome } from '@/types/market';

interface Props {
  open: boolean;
  onToggle: () => void;
  onNewTrade?: () => void;
}

// ---------------------------------------------------------------------------
// Action badge components — text built only from structured fields (T-4-04)
// ---------------------------------------------------------------------------
function TradeBadge({ trade }: { trade: TradeOutcome }) {
  const isBuy = trade.side?.toLowerCase() === 'buy';
  const borderColor = isBuy ? '#ecad0a' : '#ef4444';
  const textColor = isBuy ? '#ecad0a' : '#ef4444';
  const label = isBuy
    ? `Bought ${trade.quantity} ${trade.ticker} @ $${trade.price?.toFixed(2) ?? '—'}`
    : `Sold ${trade.quantity} ${trade.ticker} @ $${trade.price?.toFixed(2) ?? '—'}`;

  return (
    <span
      className="inline-block px-2 py-0.5 rounded text-xs mr-1 mt-1 bg-terminal-surface"
      style={{ border: `1px solid ${borderColor}`, color: textColor }}
    >
      {label}
    </span>
  );
}

function WatchlistBadge({ change }: { change: WatchlistOutcome }) {
  const isAdd = change.action?.toLowerCase() === 'add';
  const label = isAdd ? `Added ${change.ticker}` : `Removed ${change.ticker}`;

  return (
    <span
      className="inline-block px-2 py-0.5 rounded text-xs mr-1 mt-1 bg-terminal-surface"
      style={{ border: '1px solid #8b949e', color: '#8b949e' }}
    >
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// ChatPanel main component
// ---------------------------------------------------------------------------
export default function ChatPanel({ open, onToggle, onNewTrade }: Props) {
  const { data: history, mutate: mutateHistory } = useSWR<ChatHistoryResponse>(
    '/api/chat/',
    fetcher
  );

  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when messages change or loading state changes
  // Guard for jsdom test environment where scrollIntoView may not be implemented
  useEffect(() => {
    const el = messagesEndRef.current;
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ behavior: 'smooth' });
    }
  }, [history?.messages?.length, loading]);

  const handleSubmit = async () => {
    const trimmed = input.trim();
    if (!trimmed || loading) return;

    setLoading(true);
    setInput('');

    try {
      const res = await fetch('/api/chat/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: trimmed }),
      });
      const data: ChatPostResponse = await res.json();
      await mutateHistory();
      if (data.trades?.length || data.watchlist_changes?.length) {
        onNewTrade?.();
      }
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      void handleSubmit();
    }
  };

  const messages = history?.messages ?? [];

  return (
    <div className="flex flex-col h-full bg-terminal-bg border-l border-terminal-border">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-terminal-border shrink-0">
        <span className="text-xs font-semibold text-terminal-muted uppercase tracking-wide">
          FinAlly AI
        </span>
        <button
          onClick={onToggle}
          className="text-terminal-muted hover:text-terminal-text text-sm leading-none px-1"
          aria-label="Toggle chat panel"
        >
          {open ? '›' : '‹'}
        </button>
      </div>

      {/* Message list */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-3 min-h-0">
        {messages.length === 0 && !loading ? (
          <p className="text-terminal-muted text-xs leading-relaxed">
            Ask FinAlly to analyze your portfolio, suggest trades, or manage your watchlist.
          </p>
        ) : (
          messages.map((msg, idx) => (
            <div
              key={idx}
              className={`flex flex-col ${msg.role === 'user' ? 'items-end' : 'items-start'}`}
            >
              {/* Message bubble — content rendered as React text child (T-4-02: no dangerouslySetInnerHTML) */}
              <div
                className={`max-w-full px-3 py-2 rounded text-sm leading-relaxed ${
                  msg.role === 'user'
                    ? 'bg-terminal-surface text-terminal-text'
                    : 'bg-terminal-surface text-terminal-text'
                }`}
              >
                {msg.content}
              </div>

              {/* Action badges — only for assistant messages with actions (T-4-04: structured fields only) */}
              {msg.role === 'assistant' && msg.actions && (
                <div className="flex flex-wrap mt-1 max-w-full">
                  {msg.actions.trades?.map((trade, i) => (
                    <TradeBadge key={`trade-${i}`} trade={trade} />
                  ))}
                  {msg.actions.watchlist_changes?.map((change, i) => (
                    <WatchlistBadge key={`wl-${i}`} change={change} />
                  ))}
                </div>
              )}
            </div>
          ))
        )}

        {/* Loading indicator */}
        {loading && (
          <div className="flex items-start" data-testid="chat-loading">
            <div className="bg-terminal-surface px-3 py-2 rounded text-xs text-terminal-muted">
              Thinking…
            </div>
          </div>
        )}

        {/* Auto-scroll anchor */}
        <div ref={messagesEndRef} />
      </div>

      {/* Input row */}
      <div className="shrink-0 px-3 py-2 border-t border-terminal-border">
        <div className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask FinAlly about your portfolio…"
            disabled={loading}
            className="flex-1 bg-terminal-surface border border-terminal-border rounded px-2 py-1 text-xs text-terminal-text placeholder-terminal-muted focus:outline-none focus:border-terminal-blue disabled:opacity-50"
          />
          <button
            onClick={() => void handleSubmit()}
            disabled={loading || !input.trim()}
            className="px-3 py-1 rounded text-xs font-semibold text-white disabled:opacity-50"
            style={{ backgroundColor: '#753991' }}
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
