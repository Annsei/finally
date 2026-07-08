import { useState, useRef, useEffect } from 'react';
import Link from 'next/link';
import useSWR from 'swr';
import { fetcher } from '@/lib/fetcher';
import { formatShares } from '@/lib/format';
import { useMarketProfile } from '@/lib/marketProfile';
import { useUiStore } from '@/stores/uiStore';
import { useT, type TFunction } from '@/lib/i18n';
import type {
  ChatHistoryResponse,
  ChatPostResponse,
  TradeOutcome,
  WatchlistOutcome,
  ChatOrderOutcome,
  ChatRuleOutcome,
  ChatBacktestOutcome,
  StrategyOutcome,
} from '@/types/market';

// Shared props threaded into every action badge so the badges stay pure (no
// hooks) — `t` translates, `sym` is the market currency symbol ($ / ¥), `lot`
// is the market lot size (1 on US → formatShares falls back to formatQuantity).
interface BadgeCtx {
  t: TFunction;
  sym: string;
  lot: number;
}

interface Props {
  open: boolean;
  onToggle: () => void;
  onNewTrade?: () => void;
}

// ---------------------------------------------------------------------------
// Action badge components — text built only from structured fields (T-4-04)
// ---------------------------------------------------------------------------
function TradeBadge({ trade, t, sym, lot }: { trade: TradeOutcome } & BadgeCtx) {
  // Failed outcomes carry only {status, ticker, error} — no side/quantity/price
  if (trade.status === 'failed') {
    return (
      <span
        data-testid="trade-badge-failed"
        className="inline-block px-2 py-0.5 rounded text-xs mr-1 mt-1 bg-terminal-surface"
        style={{ border: '1px solid var(--color-down)', color: 'var(--color-down)' }}
      >
        {t('badge.tradeFailed', { ticker: trade.ticker, error: trade.error ?? t('badge.rejected') })}
      </span>
    );
  }

  const isBuy = trade.side?.toLowerCase() === 'buy';
  // Buy keeps the accent yellow; sell uses the (flippable) down colour.
  const borderColor = isBuy ? '#ecad0a' : 'var(--color-down)';
  const textColor = isBuy ? '#ecad0a' : 'var(--color-down)';
  const price = `${sym}${trade.price?.toFixed(2) ?? '—'}`;
  const label = t(isBuy ? 'fill.bought' : 'fill.sold', {
    qty: formatShares(trade.quantity, { lot_size: lot }),
    ticker: trade.ticker,
    price,
  });

  return (
    <span
      className="inline-block px-2 py-0.5 rounded text-xs mr-1 mt-1 bg-terminal-surface"
      style={{ border: `1px solid ${borderColor}`, color: textColor }}
    >
      {label}
    </span>
  );
}

// AI-placed order outcomes (M2.1): open (resting) blue, filled yellow/red, failed red
function OrderBadge({ order, t, sym, lot }: { order: ChatOrderOutcome } & BadgeCtx) {
  if (order.status === 'failed') {
    return (
      <span
        data-testid="order-badge-failed"
        className="inline-block px-2 py-0.5 rounded text-xs mr-1 mt-1 bg-terminal-surface"
        style={{ border: '1px solid var(--color-down)', color: 'var(--color-down)' }}
      >
        {t('badge.orderFailed', { ticker: order.ticker, error: order.error ?? t('badge.rejected') })}
      </span>
    );
  }

  const isBuy = order.side?.toLowerCase() === 'buy';
  const verb = isBuy ? t('tradebar.buy') : t('tradebar.sell');
  if (order.status === 'filled' && order.fill_price != null) {
    return (
      <span
        className="inline-block px-2 py-0.5 rounded text-xs mr-1 mt-1 bg-terminal-surface"
        style={{ border: '1px solid #ecad0a', color: '#ecad0a' }}
      >
        {t(isBuy ? 'fill.bought' : 'fill.sold', {
          qty: formatShares(order.quantity, { lot_size: lot }),
          ticker: order.ticker,
          price: `${sym}${order.fill_price.toFixed(2)}`,
        })}
      </span>
    );
  }

  const parts: string[] = [];
  if (order.stop_price != null) parts.push(`${t('badge.stopWord')} ${sym}${order.stop_price.toFixed(2)}`);
  if (order.limit_price != null) parts.push(`${isBuy ? '≤' : '≥'}${sym}${order.limit_price.toFixed(2)}`);
  return (
    <span
      data-testid="order-badge-placed"
      className="inline-block px-2 py-0.5 rounded text-xs mr-1 mt-1 bg-terminal-surface"
      style={{ border: '1px solid #209dd7', color: '#209dd7' }}
    >
      {t('badge.orderPlaced', {
        verb,
        qty: formatShares(order.quantity, { lot_size: lot }),
        ticker: order.ticker,
        detail: parts.join(' / '),
      })}
    </span>
  );
}

// AI-created standing rules (M2.2)
function RuleBadge({ outcome, t }: { outcome: ChatRuleOutcome } & BadgeCtx) {
  if (outcome.status === 'failed' || !outcome.rule) {
    return (
      <span
        data-testid="rule-badge-failed"
        className="inline-block px-2 py-0.5 rounded text-xs mr-1 mt-1 bg-terminal-surface"
        style={{ border: '1px solid var(--color-down)', color: 'var(--color-down)' }}
      >
        {t('badge.ruleFailed', {
          ticker: outcome.ticker ?? '',
          error: outcome.error ?? t('badge.rejected'),
        }).trim()}
      </span>
    );
  }
  return (
    <span
      data-testid="rule-badge-created"
      className="inline-block px-2 py-0.5 rounded text-xs mr-1 mt-1 bg-terminal-surface"
      style={{ border: '1px solid #753991', color: '#b07cc6' }}
    >
      {t('badge.ruleArmed', { desc: outcome.rule.description })}
    </span>
  );
}

// AI-run backtests (M5) — compact stats line; full curves live in the Backtest tab
function BacktestBadge({ outcome, t }: { outcome: ChatBacktestOutcome } & BadgeCtx) {
  if (outcome.status === 'failed' || !outcome.stats) {
    return (
      <span
        data-testid="backtest-badge-failed"
        className="inline-block px-2 py-0.5 rounded text-xs mr-1 mt-1 bg-terminal-surface"
        style={{ border: '1px solid var(--color-down)', color: 'var(--color-down)' }}
      >
        {t('badge.backtestFailed', {
          ticker: outcome.ticker,
          error: outcome.error ?? t('badge.rejected'),
        })}
      </span>
    );
  }
  const s = outcome.stats;
  const sign = s.total_return_pct >= 0 ? '+' : '';
  const bhSign = s.buy_hold_return_pct >= 0 ? '+' : '';
  const winPart = s.win_rate != null ? ` · ${t('badge.win')} ${Math.round(s.win_rate * 100)}%` : '';
  return (
    <span
      data-testid="backtest-badge-completed"
      className="inline-block px-2 py-0.5 rounded text-xs mr-1 mt-1 bg-terminal-surface"
      style={{ border: '1px solid #209dd7', color: '#209dd7' }}
    >
      {t('badge.backtest', {
        ticker: outcome.ticker,
        ret: `${sign}${s.total_return_pct.toFixed(1)}%`,
        bh: `${bhSign}${s.buy_hold_return_pct.toFixed(1)}%`,
        rt: s.round_trips,
        win: winPart,
      })}
    </span>
  );
}

// AI strategy actions (P2 §7) — mirrors BacktestBadge: status-driven variants
// for create/deploy/pause plus a compact-stats badge for strategy backtests
// (the full run is persisted to the Run Library; the badge cites it).
function StrategyBadge({ outcome, t }: { outcome: StrategyOutcome } & BadgeCtx) {
  const name = outcome.name ?? outcome.ticker ?? '';
  if (outcome.status === 'failed') {
    return (
      <span
        data-testid="strategy-badge-failed"
        className="inline-block px-2 py-0.5 rounded text-xs mr-1 mt-1 bg-terminal-surface"
        style={{ border: '1px solid var(--color-down)', color: 'var(--color-down)' }}
      >
        {t('badge.strategyFailed', { name, error: outcome.error ?? t('badge.rejected') })}
      </span>
    );
  }
  if (outcome.status === 'completed' && outcome.stats) {
    const s = outcome.stats;
    const sign = s.total_return_pct >= 0 ? '+' : '';
    const bhSign = s.buy_hold_return_pct >= 0 ? '+' : '';
    const winPart = s.win_rate != null ? ` · ${t('badge.win')} ${Math.round(s.win_rate * 100)}%` : '';
    // 'saved to Runs' tail: when the outcome carries the persisted run's id,
    // it deep-links to the /run detail page (same badge colour, underline on
    // hover per the desk's inline-link convention).
    const savedTail = t('badge.strategyBacktestSaved');
    return (
      <span
        data-testid="strategy-badge-backtest"
        className="inline-block px-2 py-0.5 rounded text-xs mr-1 mt-1 bg-terminal-surface"
        style={{ border: '1px solid #209dd7', color: '#209dd7' }}
      >
        {t('badge.strategyBacktest', {
          name,
          ret: `${sign}${s.total_return_pct.toFixed(1)}%`,
          bh: `${bhSign}${s.buy_hold_return_pct.toFixed(1)}%`,
          rt: s.round_trips,
          win: winPart,
        })}
        {' · '}
        {outcome.run_id ? (
          <Link
            href={{ pathname: '/run', query: { id: outcome.run_id } }}
            className="hover:underline"
            style={{ color: 'inherit' }}
          >
            {savedTail}
          </Link>
        ) : (
          savedTail
        )}
      </span>
    );
  }
  if (outcome.status === 'paused') {
    return (
      <span
        data-testid="strategy-badge-paused"
        className="inline-block px-2 py-0.5 rounded text-xs mr-1 mt-1 bg-terminal-surface"
        style={{ border: '1px solid #8b949e', color: '#8b949e' }}
      >
        {t('badge.strategyPaused', { name })}
      </span>
    );
  }
  const isDeploy = outcome.status === 'deployed';
  return (
    <span
      data-testid={isDeploy ? 'strategy-badge-deployed' : 'strategy-badge-created'}
      className="inline-block px-2 py-0.5 rounded text-xs mr-1 mt-1 bg-terminal-surface"
      style={{ border: '1px solid #753991', color: '#b07cc6' }}
    >
      {t(isDeploy ? 'badge.strategyDeployed' : 'badge.strategyCreated', {
        name,
        ticker: outcome.ticker ?? '',
      })}
    </span>
  );
}

// Agent-initiated message styling by kind (M2.3/2.4): briefs, reviews, rules.
// Border colour by kind; the label is translated at render time. Exported so
// the /journal review archive reuses the same kind semantics (P1 §6).
// P2 §1 adds kind='strategy' (engine entry/exit notes) in the purple family.
export const KIND_BORDER: Record<string, string> = {
  brief: '#209dd7',
  review: '#ecad0a',
  rule: '#b07cc6',
  strategy: '#753991',
};

// Briefs arrive continuously and were drowning the conversation — clamp each
// to two lines so the feed stays scannable; clicking toggles the full text.
function BriefContent({ content, t }: { content: string; t: TFunction }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <button
      type="button"
      data-testid="brief-content"
      data-expanded={expanded}
      title={expanded ? t('chat.collapse') : t('chat.showFull')}
      onClick={() => setExpanded((v) => !v)}
      className="block w-full text-left text-xs leading-snug text-terminal-text"
      style={
        expanded
          ? undefined
          : {
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
            }
      }
    >
      {content}
    </button>
  );
}

function WatchlistBadge({ change, t }: { change: WatchlistOutcome } & BadgeCtx) {
  // Failed outcomes carry only {status, ticker, error} — no action
  if (change.status === 'failed') {
    return (
      <span
        data-testid="watchlist-badge-failed"
        className="inline-block px-2 py-0.5 rounded text-xs mr-1 mt-1 bg-terminal-surface"
        style={{ border: '1px solid var(--color-down)', color: 'var(--color-down)' }}
      >
        {t('badge.watchlistFailed', {
          ticker: change.ticker,
          error: change.error ?? t('badge.rejected'),
        })}
      </span>
    );
  }

  const isAdd = change.action?.toLowerCase() === 'add';
  const label = isAdd
    ? t('badge.added', { ticker: change.ticker })
    : t('badge.removed', { ticker: change.ticker });

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
  const t = useT();
  const profile = useMarketProfile();
  const sym = profile.currency_symbol;
  const lot = profile.lot_size;
  // 10s polling — rule firings and other agent-initiated messages appear
  // without the user having to send anything (M2.2)
  const { data: history, mutate: mutateHistory } = useSWR<ChatHistoryResponse>(
    '/api/chat/',
    fetcher,
    { refreshInterval: 10_000 }
  );

  // Draft lives in uiStore (P1 §2) so half-typed input survives navigating
  // between the desk and the new pages (the panel remounts per page).
  const input = useUiStore((s) => s.chatDraft);
  const setInput = useUiStore((s) => s.setChatDraft);
  const pendingChatMessage = useUiStore((s) => s.pendingChatMessage);
  const setPendingChatMessage = useUiStore((s) => s.setPendingChatMessage);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when messages change, loading state changes, or an error appears
  // Guard for jsdom test environment where scrollIntoView may not be implemented
  useEffect(() => {
    const el = messagesEndRef.current;
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ behavior: 'smooth' });
    }
  }, [history?.messages?.length, loading, error]);

  // Core send path — used by both the input row and the pendingChatMessage
  // effect (P1 §2). Behavior identical to the previous inline handleSubmit.
  const sendMessage = async (trimmed: string) => {
    setLoading(true);
    setError(null);

    try {
      const res = await fetch('/api/chat/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: trimmed }),
      });
      if (!res.ok) {
        // Surface backend error detail when available (e.g. {"error": "..."})
        let detail = '';
        try {
          const body = await res.json();
          detail = body?.error ?? body?.detail ?? '';
        } catch {
          // Non-JSON error body — fall through to generic message
        }
        throw new Error(detail || `Request failed (${res.status})`);
      }
      const data: ChatPostResponse = await res.json();
      await mutateHistory();
      if (
        data.trades?.length ||
        data.watchlist_changes?.length ||
        data.orders?.length ||
        data.rules?.length ||
        data.strategies?.length
      ) {
        onNewTrade?.();
      }
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : t('chat.errGeneric'));
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async () => {
    const trimmed = input.trim();
    if (!trimmed || loading) return;
    setInput('');
    await sendMessage(trimmed);
  };

  // One-shot auto-send (P1 §2): a page sets uiStore.pendingChatMessage (e.g.
  // /symbol's "AI analyze" button); this effect consumes it as a user message
  // and clears it. Default null → zero behavior difference.
  useEffect(() => {
    // StrictMode double-invokes mount effects with the same render's values —
    // read the LIVE store slot so it is consumed exactly once (the first run
    // clears it; the second sees null and bails).
    const pending = useUiStore.getState().pendingChatMessage;
    if (pending === null || loading) return;
    // One-shot consume: always clear the slot first — even a blank message
    // must not occupy it forever. Same consume-and-clear pattern as
    // BacktestPanel's backtestPrefill effect.
    setPendingChatMessage(null);
    const msg = pending.trim();
    if (!msg) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void sendMessage(msg);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingChatMessage, loading]);

  // M2.4: on-demand daily review — stored server-side as a kind='review' message
  const handleReview = async () => {
    if (loading) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/api/chat/review', { method: 'POST' });
      if (!res.ok) {
        let detail = '';
        try {
          const body = await res.json();
          detail = body?.error ?? body?.detail ?? '';
        } catch {
          // Non-JSON error body — fall through to generic message
        }
        throw new Error(detail || `Review failed (${res.status})`);
      }
      await mutateHistory();
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : t('chat.errGeneric'));
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
          {t('chat.title')}
        </span>
        {open && (
          <button
            type="button"
            data-testid="chat-review-button"
            onClick={() => void handleReview()}
            disabled={loading}
            title={t('chat.reviewTitle')}
            className="ml-auto mr-2 text-[10px] font-semibold uppercase tracking-wider text-terminal-muted hover:text-terminal-accent disabled:opacity-50 transition-colors"
          >
            {t('chat.review')}
          </button>
        )}
        <button
          onClick={onToggle}
          className="text-terminal-muted hover:text-terminal-text text-sm leading-none px-1"
          aria-label={t('chat.toggle')}
        >
          {open ? '›' : '‹'}
        </button>
      </div>

      {/* Message list */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-3 min-h-0">
        {messages.length === 0 && !loading ? (
          <p className="text-terminal-muted text-xs leading-relaxed">
            {t('chat.empty')}
          </p>
        ) : (
          messages.map((msg, idx) => {
            const kindBorder =
              msg.role === 'assistant' && msg.kind ? KIND_BORDER[msg.kind] : undefined;
            return (
            <div
              key={idx}
              className={`flex flex-col ${msg.role === 'user' ? 'items-end' : 'items-start'}`}
            >
              {/* Message bubble — content rendered as React text child (T-4-02: no dangerouslySetInnerHTML) */}
              <div
                className="max-w-full px-3 py-2 rounded text-sm leading-relaxed bg-terminal-surface text-terminal-text"
                style={kindBorder ? { borderLeft: `2px solid ${kindBorder}` } : undefined}
              >
                {kindBorder && msg.kind && (
                  <span
                    data-testid={`chat-kind-${msg.kind}`}
                    className="block text-[10px] font-semibold uppercase tracking-wider mb-0.5"
                    style={{ color: kindBorder }}
                  >
                    {t(`chat.kind.${msg.kind}`)}
                  </span>
                )}
                {msg.kind === 'brief' ? <BriefContent content={msg.content} t={t} /> : msg.content}
              </div>

              {/* Action badges — only for assistant messages with actions (T-4-04: structured fields only) */}
              {msg.role === 'assistant' && msg.actions && (
                <div className="flex flex-wrap mt-1 max-w-full">
                  {msg.actions.trades?.map((trade, i) => (
                    <TradeBadge key={`trade-${i}`} trade={trade} t={t} sym={sym} lot={lot} />
                  ))}
                  {msg.actions.orders?.map((order, i) => (
                    <OrderBadge key={`order-${i}`} order={order} t={t} sym={sym} lot={lot} />
                  ))}
                  {msg.actions.rules?.map((rule, i) => (
                    <RuleBadge key={`rule-${i}`} outcome={rule} t={t} sym={sym} lot={lot} />
                  ))}
                  {msg.actions.backtests?.map((bt, i) => (
                    <BacktestBadge key={`bt-${i}`} outcome={bt} t={t} sym={sym} lot={lot} />
                  ))}
                  {msg.actions.strategies?.map((st, i) => (
                    <StrategyBadge key={`strat-${i}`} outcome={st} t={t} sym={sym} lot={lot} />
                  ))}
                  {msg.actions.watchlist_changes?.map((change, i) => (
                    <WatchlistBadge key={`wl-${i}`} change={change} t={t} sym={sym} lot={lot} />
                  ))}
                </div>
              )}
            </div>
            );
          })
        )}

        {/* Loading indicator */}
        {loading && (
          <div className="flex items-start" data-testid="chat-loading">
            <div className="bg-terminal-surface px-3 py-2 rounded text-xs text-terminal-muted">
              {t('chat.thinking')}
            </div>
          </div>
        )}

        {/* Inline error — shown in the history area when POST /api/chat/ fails */}
        {error && !loading && (
          <div className="flex items-start" data-testid="chat-error">
            <div className="bg-terminal-surface border border-terminal-down/60 px-3 py-2 rounded text-xs text-terminal-down leading-relaxed">
              {error}
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
            placeholder={t('chat.placeholder')}
            disabled={loading}
            className="flex-1 bg-terminal-surface border border-terminal-border rounded px-2 py-1 text-xs text-terminal-text placeholder-terminal-muted focus:outline-none focus:border-terminal-blue disabled:opacity-50"
          />
          <button
            onClick={() => void handleSubmit()}
            disabled={loading || !input.trim()}
            className="px-3 py-1 rounded text-xs font-semibold text-white disabled:opacity-50"
            style={{ backgroundColor: '#753991' }}
          >
            {t('chat.send')}
          </button>
        </div>
      </div>
    </div>
  );
}
