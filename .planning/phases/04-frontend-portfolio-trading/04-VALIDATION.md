---
phase: 4
slug: frontend-portfolio-trading
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-07
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework (Frontend)** | Jest 30 + Testing Library React 16 |
| **Config file (Frontend)** | `frontend/jest.config.js` |
| **Quick run command (Frontend)** | `npm test -- --testPathPattern=<file> --watchAll=false` |
| **Full suite command (Frontend)** | `npm test -- --watchAll=false` |
| **Framework (Backend)** | pytest-asyncio |
| **Config file (Backend)** | `backend/pyproject.toml` |
| **Quick run command (Backend)** | `uv run --extra dev pytest tests/test_chat.py -v` |
| **Full suite command (Backend)** | `uv run --extra dev pytest -v` |
| **Estimated frontend runtime** | ~30 seconds |
| **Estimated backend runtime** | ~20 seconds |

---

## Sampling Rate

- **After every task commit:** Run `npm test -- --testPathPattern=<component> --watchAll=false`
- **After every plan wave:** Run `npm test -- --watchAll=false` (full frontend) + `uv run --extra dev pytest -v` (backend)
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** ~30 seconds (frontend), ~20 seconds (backend)

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 04-01-01 | 01 | 1 | FE-14 (backend) | — | GET /api/chat returns messages array, never raw SQL | integration | `uv run --extra dev pytest tests/test_chat.py::TestChat::test_get_chat_history -v` | ❌ W0 | ⬜ pending |
| 04-02-01 | 02 | 1 | FE-09 | — | createChart called once; canvas rendered | unit | `npm test -- --testPathPattern=MainChart --watchAll=false` | ❌ W0 | ⬜ pending |
| 04-02-02 | 02 | 1 | FE-09 | — | setData([]) called on ticker change | unit | `npm test -- --testPathPattern=MainChart --watchAll=false` | ❌ W0 | ⬜ pending |
| 04-03-01 | 03 | 1 | FE-10 | — | Tiles render with width% per position weight | unit | `npm test -- --testPathPattern=PortfolioHeatmap --watchAll=false` | ❌ W0 | ⬜ pending |
| 04-03-02 | 03 | 1 | FE-10 | — | Empty state renders "No positions yet" text | unit | `npm test -- --testPathPattern=PortfolioHeatmap --watchAll=false` | ❌ W0 | ⬜ pending |
| 04-04-01 | 04 | 1 | FE-11 | — | createChart called; setData called with snapshots | unit | `npm test -- --testPathPattern=PnLChart --watchAll=false` | ❌ W0 | ⬜ pending |
| 04-05-01 | 05 | 1 | FE-12 | — | All 6 columns render with mock position data | unit | `npm test -- --testPathPattern=PositionsTable --watchAll=false` | ❌ W0 | ⬜ pending |
| 04-05-02 | 05 | 1 | FE-12 | — | Current-price cell applies flash class on price change | unit | `npm test -- --testPathPattern=PositionsTable --watchAll=false` | ❌ W0 | ⬜ pending |
| 04-06-01 | 06 | 1 | FE-13 | T-4-01: injection via ticker | ticker trimmed + uppercased before submit; quantity validated isFinite && > 0 | unit | `npm test -- --testPathPattern=TradeBar --watchAll=false` | ❌ W0 | ⬜ pending |
| 04-06-02 | 06 | 1 | FE-13 | — | POST /api/portfolio/trade called on Buy click | unit | `npm test -- --testPathPattern=TradeBar --watchAll=false` | ❌ W0 | ⬜ pending |
| 04-06-03 | 06 | 1 | FE-13 | — | Inline error message shown on 400 response | unit | `npm test -- --testPathPattern=TradeBar --watchAll=false` | ❌ W0 | ⬜ pending |
| 04-07-01 | 07 | 2 | FE-14 | T-4-02: XSS via chat | message rendered as React text child — no dangerouslySetInnerHTML | unit | `npm test -- --testPathPattern=ChatPanel --watchAll=false` | ❌ W0 | ⬜ pending |
| 04-07-02 | 07 | 2 | FE-14 | — | Loading indicator visible during pending POST | unit | `npm test -- --testPathPattern=ChatPanel --watchAll=false` | ❌ W0 | ⬜ pending |
| 04-07-03 | 07 | 2 | FE-14 | — | History loaded from GET /api/chat on mount | unit | `npm test -- --testPathPattern=ChatPanel --watchAll=false` | ❌ W0 | ⬜ pending |
| 04-07-04 | 07 | 2 | FE-15 | — | Trade action badge renders for each trades[] entry | unit | `npm test -- --testPathPattern=ChatPanel --watchAll=false` | ❌ W0 | ⬜ pending |
| 04-07-05 | 07 | 2 | FE-15 | — | Watchlist badge renders for each watchlist_changes[] entry | unit | `npm test -- --testPathPattern=ChatPanel --watchAll=false` | ❌ W0 | ⬜ pending |
| 04-08-01 | 08 | 2 | FE-09–15 | — | index.tsx renders 3-column layout; no console errors | unit | `npm test -- --testPathPattern=index --watchAll=false` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `frontend/__tests__/MainChart.test.tsx` — stubs for FE-09
- [ ] `frontend/__tests__/PortfolioHeatmap.test.tsx` — stubs for FE-10
- [ ] `frontend/__tests__/PnLChart.test.tsx` — stubs for FE-11
- [ ] `frontend/__tests__/PositionsTable.test.tsx` — stubs for FE-12
- [ ] `frontend/__tests__/TradeBar.test.tsx` — stubs for FE-13
- [ ] `frontend/__tests__/ChatPanel.test.tsx` — stubs for FE-14, FE-15
- [ ] `backend/tests/test_chat.py` — add `test_get_chat_history` test case for GET /api/chat endpoint

*The `lightweight-charts` mock in `frontend/__mocks__/lightweightChartsStub.js` and `jest.config.js` mapper are already configured — new chart tests follow `SparklineChart.test.tsx` mock pattern exactly.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Price flash animation visible in browser | FE-12 | CSS animation not detectable in jsdom | Open app, observe positions table current-price column as prices update; verify brief green/red background flash |
| Chat panel collapse/expand animation | FE-14 | CSS width transition not detectable in jsdom | Click toggle button; verify smooth slide animation in browser |
| Heatmap color intensity scales with P&L% | FE-10 | Visual inspection required | Buy shares at one price, let price move; verify tiles deepen in color as P&L% increases |
| MainChart auto-selects first ticker on load | FE-09 | Browser state + SSE required | Reload page with no ticker selected; verify chart renders for first watchlist ticker |
| P&L chart updates every 30 seconds | FE-11 | Timing-dependent | Monitor browser network tab; confirm GET /api/portfolio/history is called every ~30s |

---

## Threat Model

| ID | Threat | STRIDE | Mitigation | Verified By |
|----|--------|--------|------------|-------------|
| T-4-01 | Ticker input injection | Tampering | Trim + uppercase ticker before submit; backend validates against price cache | TradeBar unit test (04-06-01) |
| T-4-02 | XSS via chat messages | Spoofing | React auto-escapes text — never use `dangerouslySetInnerHTML` in ChatPanel | ChatPanel unit test (04-07-01) |
| T-4-03 | Large/NaN quantity causes server error | Denial of Service | Validate `isFinite(qty) && qty > 0` client-side before POST | TradeBar unit test (04-06-01) |
| T-4-04 | Chat badge displays raw LLM output | Information Disclosure | Badge text constructed from known fields (ticker, qty, price) only | ChatPanel unit test (04-07-04) |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s (frontend), < 20s (backend)
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
