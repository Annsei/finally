/**
 * uiStore.test.ts — P1 §2 uiStore increments.
 *
 * The store is a module-level Zustand singleton, so every test resets it in
 * beforeEach — both to prove the reset pattern and to prevent state leaking
 * between tests (and into other suites' assumptions).
 */
import { useUiStore } from '@/stores/uiStore';

const INITIAL = {
  portfolioTab: 'positions' as const,
  backtestPrefill: null,
  chatOpen: true,
  chatDraft: '',
  pendingChatMessage: null,
};

describe('uiStore — P1 chat state increments', () => {
  beforeEach(() => {
    useUiStore.setState(INITIAL);
  });

  it('Test 1: defaults — chatOpen true, chatDraft empty, pendingChatMessage null', () => {
    const s = useUiStore.getState();
    expect(s.chatOpen).toBe(true);
    expect(s.chatDraft).toBe('');
    expect(s.pendingChatMessage).toBeNull();
  });

  it('Test 2: setChatOpen toggles the docked panel state', () => {
    useUiStore.getState().setChatOpen(false);
    expect(useUiStore.getState().chatOpen).toBe(false);
    useUiStore.getState().setChatOpen(true);
    expect(useUiStore.getState().chatOpen).toBe(true);
  });

  it('Test 3: setChatDraft stores the cross-page input draft', () => {
    useUiStore.getState().setChatDraft('analyze NVDA');
    expect(useUiStore.getState().chatDraft).toBe('analyze NVDA');
    useUiStore.getState().setChatDraft('');
    expect(useUiStore.getState().chatDraft).toBe('');
  });

  it('Test 4: pendingChatMessage is a one-shot handoff — set then clear to null', () => {
    useUiStore.getState().setPendingChatMessage('Analyze AAPL for me');
    expect(useUiStore.getState().pendingChatMessage).toBe('Analyze AAPL for me');
    useUiStore.getState().setPendingChatMessage(null);
    expect(useUiStore.getState().pendingChatMessage).toBeNull();
  });

  it('Test 5: new fields do not disturb the existing M5 tab/prefill wiring', () => {
    useUiStore.getState().setChatOpen(false);
    useUiStore.getState().setChatDraft('draft');
    expect(useUiStore.getState().portfolioTab).toBe('positions');
    expect(useUiStore.getState().backtestPrefill).toBeNull();

    useUiStore.getState().setPortfolioTab('backtest');
    expect(useUiStore.getState().portfolioTab).toBe('backtest');
    // and vice versa: tab switching leaves chat state alone
    expect(useUiStore.getState().chatOpen).toBe(false);
    expect(useUiStore.getState().chatDraft).toBe('draft');
  });

  it('Test 6: beforeEach reset guards against singleton leakage across tests', () => {
    // Anything test 5 mutated must be back at defaults here.
    const s = useUiStore.getState();
    expect(s.portfolioTab).toBe('positions');
    expect(s.chatOpen).toBe(true);
    expect(s.chatDraft).toBe('');
    expect(s.pendingChatMessage).toBeNull();
  });
});
