import ChatPanel from '@/components/ChatPanel';

interface Props {
  open: boolean;
  onToggle: () => void;
  onNewTrade?: () => void;
}

/**
 * Shared responsive shell for the AI chat.
 *
 * - 2xl / 1600px: 320px dock in the normal three-column flow.
 * - xl / 1280px: 288px compact dock in the normal flow.
 * - md / 768px: right-side overlay, so the desk keeps a usable chart width.
 * - narrow: bottom drawer; its collapsed toggle remains reachable.
 */
export default function ResponsiveChatDock({ open, onToggle, onNewTrade }: Props) {
  const responsiveClass = open
    ? 'fixed inset-x-2 bottom-2 h-[min(75dvh,42rem)] shadow-2xl md:inset-x-auto md:right-4 md:bottom-12 md:w-96 md:h-[min(70dvh,44rem)] xl:static xl:h-auto xl:w-72 2xl:w-80 xl:shadow-none xl:shrink-0'
    : 'fixed right-2 bottom-12 h-11 w-10 shadow-lg md:right-4 xl:static xl:h-auto xl:w-8 xl:shadow-none xl:shrink-0';

  return (
    <aside
      data-testid="responsive-chat-dock"
      aria-label="FinAlly AI chat"
      className={`z-40 overflow-hidden transition-all duration-300 border border-terminal-border bg-terminal-bg xl:border-y-0 xl:border-r-0 ${responsiveClass}`}
    >
      <ChatPanel open={open} onToggle={onToggle} onNewTrade={onNewTrade} />
    </aside>
  );
}
