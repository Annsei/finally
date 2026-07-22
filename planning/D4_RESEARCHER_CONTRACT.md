# D4 — AI strategy researcher loop (contract)

Status: complete — pytest 1832 (+38) / jest 626 (+17) / E2E us 42 + cn 19;
five adjudicated deviations in the appendix.  
Baseline: `f933424` (D3 shipped; pytest 1794 / jest 609 / E2E us 41 + cn 18)

One chat request makes the AI act as a strategy researcher: it authors 2–4
candidate declarative strategies for a ticker, the backend backtests every
candidate on stored daily-bar history, ranks them by a documented robustness
score, and the chat renders a comparison card with per-candidate deploy
buttons. Deploy is a separate, explicit user click — never automatic.

Everything composes existing machinery: the P2 strategy DSL + templates, the
D1 history backtest, the Run Library, and the chat action pipeline. **No new
tables, no new columns, no new env vars, no new indicator fields.**

## 1. User story

> 用户: “帮我研究一下 AAPL 的策略,趋势和超跌都试试”
>
> AI 回复一段研究结论 + 一张对比卡:3 个候选策略(名称/假设/收益/回撤/胜率/
> 成交数/稳健分),按排名列出,第 1 名带“推荐”徽章。每个候选已存为 draft
> 策略并挂了一条可点开的历史回测记录。用户点“部署”→ 该策略转 live,开始
> 实时运行。

## 2. Backend contract

### 2.1 New chat action `research`

Added to the chat structured-output schema — **never** to the frozen
`ChatResponse` (pinned by tests/test_chat_models.py). Implementation note:
the field lives on a `ChatResearchTurnResponse` subclass of
`ChatTurnResponse`, because the `ChatTurnResponse` field set itself turned
out to be pinned too — appendix item 5.

```python
class ResearchCandidate(BaseModel):
    name: str                      # short display name, 1..40 after trim
    hypothesis: str | None = None  # one-line rationale, shown in the card
    template: str | None = None    # TEMPLATES_BY_KEY key; supplies missing parts
    entry: dict | None = None      # condition-group DSL (overrides template)
    exits: dict | None = None
    sizing: dict | None = None

class ResearchInstruction(BaseModel):
    ticker: str
    days: int | None = None        # TRADING days; default 120, clamped 20..750
                                   # (reuse existing history-mode semantics)
    candidates: list[ResearchCandidate] = []

# ChatTurnResponse gains:
research: list[ResearchInstruction] = []
```

Template merge rule is identical to the existing strategy-create action
(chat.py:1075-1083): template supplies `entry`/`exits`/`sizing`, explicit
fields override.

### 2.2 Handler — new module `backend/app/research.py`

```python
def run_research_on_conn(
    conn, price_cache, *, ticker, days, candidates, user_id,
    universe=None, profile=None, market="us",
    commission_bps=0.0, starting_cash=10_000.0,
) -> dict:
```

Rules:

- **Does not commit.** The chat turn owns the single commit (existing
  invariant). Dispatched as a new step after the `strategies` loop
  (chat.py:1245), one outcome per instruction, appended to `actions["research"]`
  and the response payload **only when non-empty** (golden byte-identity).
- Batch guard: 2..4 candidates, else the whole instruction fails
  (`{"status": "failed", "ticker", "error"}`). More instructions in the array
  are processed independently (per-action isolation, existing pattern).
- Per candidate, in order; any failure marks THAT candidate
  `{"status": "failed", "name", "error"}` and continues:
  1. template merge; require `has_any_exit(exits)` — research products must
     be deployable (error otherwise);
  2. `normalize_strategy_backtest_config(price_cache, ticker=..., entry=...,
     exits=..., sizing=..., days=days, source="history", universe=universe,
     profile=profile)` (backtest.py:324) — DSL/name/ticker/lot validation;
  3. `attach_history_bars(config, conn, market=market)` (backtest.py:483) —
     insufficient history ⇒ failed candidate;
  4. `run_backtest(config, commission_bps=..., starting_cash=...,
     profile=profile)` (backtest.py:1183);
  5. `create_strategy_on_conn(...)` (strategies.py:255) — **draft**, owned by
     `user_id`;
  6. `insert_backtest_run_on_conn(conn, user_id=..., strategy_id=<new id>,
     label="Research: " + name, result=result)` (backtest_runs.py:85).
- **Ranking (deterministic, documented):**
  `score = round(total_return_pct - 0.5 * max_drawdown_pct, 2)`
  (`max_drawdown_pct` is the engine's non-negative magnitude — verify sign
  against `stats` and adjust so a larger drawdown always lowers the score).
  Sort completed candidates by `(traded desc, score desc, win_rate desc,
  original index asc)` where `traded = round_trips >= 1`. Assign `rank`
  1..n over completed candidates only. `recommended_strategy_id` = rank-1
  candidate **iff it traded**; otherwise `null` (an untraded winner is not a
  recommendation).
- Batch outcome:

```json
{
  "status": "completed",            // "failed" only if zero candidates completed
  "ticker": "AAPL",
  "days": 120,
  "candidates": [
    {"name": "...", "hypothesis": "...", "status": "completed",
     "strategy_id": "...", "run_id": "...", "score": 12.34, "rank": 1,
     "traded": true, "stats": { ...full run_backtest stats dict... }},
    {"name": "...", "status": "failed", "error": "..."}
  ],
  "recommended_strategy_id": "..."  // or null
}
```

- Deploy is NOT part of research. The card deploys via the existing
  `PATCH /api/strategies/{id} {"status": "live"}` (strategies.py:590).
  Constrained Bearer keys already get a blanket 403 on POST /api/chat
  (api_gateway.py:504) — research inherits that; no gateway change.

### 2.3 System prompt

Append a `'research'` bullet to BOTH `SYSTEM_PROMPT` (chat.py:83) and
`SYSTEM_PROMPT_ZH` (chat.py:158), after the `strategies` bullet. Base text
(lane may refine wording, content is contractual):

> - 'research': strategy research requests. Use when the user asks you to
>   research, explore, or compare candidate strategies for a ticker. Each
>   item: {ticker, days?, candidates: [{name, hypothesis?, template?,
>   entry?, exits?, sizing?}]} with 2-4 candidates. Give each candidate a
>   short name and a one-line hypothesis, and vary the approaches (trend
>   following, mean reversion, breakout). Candidates may reference a
>   template with optional overrides or give explicit entry/exits/sizing;
>   every candidate MUST include at least one exit. The platform backtests
>   each candidate on stored daily history, ranks them by robustness score
>   (return minus half the max drawdown), and shows a comparison card with
>   deploy buttons. Do not also emit 'strategies' actions in the same turn;
>   never deploy in the research turn — the user decides from the card.

Do NOT touch the stale "five action arrays" wording (asserted literally by
tests/test_chat_backtest.py:114; precedent: P2/M5 left it as-is).

**Permitted existing-test edit (the ONLY one):**
`tests/test_sentiment_context.py:48-53` — recompute both
`SYSTEM_PROMPT_SHA256` and `SYSTEM_PROMPT_ZH_SHA256` from the new constants
and update the provenance comment to name D4. This mirrors how P2/M5 evolved
the prompt.

### 2.4 LLM_MOCK branch

Insert a research branch **before** the `wants_strategy` check in both the
zh and en mock chains (chat.py:789-889): `wants_research = "research" in
lower or "研究" in body.message`. The four golden messages ("hello there",
"你好…", "backtest…", "帮我回测…") contain neither token — existing goldens
must stay byte-identical.

Mock emits ONE `ResearchInstruction`, `days=120`, 3 template candidates with
`sizing={"mode": "cash_pct", "pct": 20}` (lot-safe on CN):

- EN branch: ticker `AAPL`; candidates
  (`Golden Cross`, ma_golden_cross), (`RSI Rebound`, rsi_rebound),
  (`Momentum Breakout`, momentum_breakout), each with a one-line hypothesis.
- ZH branch: ticker `600519`; the same three templates with Chinese
  names/hypotheses (`均线金叉` / `RSI 超跌反弹` / `动量突破`).

Both tickers ship in the committed sample bars (2023-08-08→2026-06-30), so
mock research works offline on a fresh volume in both markets. History
backtests are RNG-free ⇒ the whole mock research turn is deterministic.

### 2.5 Backend tests

- `tests/test_research.py` (new): unit tests on `run_research_on_conn` —
  candidate-count guard; per-candidate failure isolation (bad DSL / missing
  exit / unknown ticker / insufficient history fail one, not the batch);
  draft + linked run actually persisted (strategy_id on the run row); label
  prefix; ranking determinism incl. tie-breaks; zero-trade demotion and
  null recommendation; score formula (drawdown lowers score); CN profile
  parity (lots via cash_pct, T+1 handled by engine); handler never commits
  (rollback leaves no rows).
- `tests/test_chat_research.py` (new): POST /api/chat with LLM_MOCK research
  message (en + zh) — response carries `research` outcome, 3 ranked
  candidates, drafts owned by cookie user, runs visible in
  GET /api/backtest/runs; `research` key absent from default/backtest mock
  responses; prompt token-presence assertions for the new bullet (both
  languages), following test_chat_strategies.py:393 style.
- Golden: extend `tests/test_chat_mock_golden.py` with
  `chat_mock_research.json` + `chat_mock_research_zh.json` using the same
  volatile-field normalization the file already uses; if ids/timestamps
  cannot be normalized cleanly with the existing machinery, pin exact-value
  assertions on stats/ranking instead (document the choice in the test).
- The four existing chat goldens, the 3 backtest goldens, FIELD_SPECS
  key-set pins, frozen-signature pins: all pass **unmodified**.

## 3. Frontend contract

### 3.1 Types (src/types/market.ts)

`ResearchCandidateOutcome`, `ResearchOutcome` mirroring §2.2;
`ChatMessage.actions` (types:736-743) and `ChatPostResponse` (types:753-761)
gain `research?: ResearchOutcome[]`.

### 3.2 `src/components/chat/StrategyResearchCard.tsx` (new)

Rendered by ChatPanel's actions block (ChatPanel.tsx:535-556) for each
research outcome — a block card, not a badge pill. Designed for the 320px
dock: ranked candidate mini-rows, not a wide table.

- Header: ticker · days · candidate count.
- Per completed candidate (rank order): rank number, name, hypothesis
  (muted, clamped), compact stats line (return / max drawdown / win rate /
  round trips) using `signed`/`pnlClass` from backtest/StatCard, score,
  links to `/run?id=<run_id>` and `/strategy?id=<strategy_id>`, and a
  Deploy button (`#753991` purple, standard inline-fetch error convention).
- Rank-1 recommended candidate carries a badge when
  `recommended_strategy_id` matches; when it is null show a muted
  "no recommendation" note.
- Failed candidates render name + error in the down color.
- Deploy = `PATCH /api/strategies/{id} {"status": "live"}`; on success flip
  local state and call the revalidation callback (AppShell's
  `onNewTrade`-style hook already revalidates `/api/strategies`).
- The card reads `useSWR('/api/strategies?status=all')` to derive current
  status per strategy_id so re-opened history shows "deployed"/"archived"
  instead of a stale Deploy button; ids missing even from the all view
  (deleted) disable the button. The default list view hides archived rows
  server-side (strategies contract §6, pinned), so only status=all can
  resolve the archived display — appendix item 3. A successful deploy
  mutates both this key and the plain '/api/strategies' key (AppShell's
  STRATEGIES_REVALIDATE_KEY) so the strategies page stays fresh.
- Pinned testids: `research-card`, `research-candidate`, `research-deploy`,
  `research-recommended`; after a successful deploy the button is replaced by
  a `research-deployed` element (also used when the strategies list already
  reports the id as live).

### 3.3 Entry point

`strategies.tsx` gains a small "AI research" button next to the create form
header: injects a prefilled research prompt via the `pendingChatMessage`
one-shot channel (uiStore.ts:43) and opens the chat dock.

### 3.4 i18n

Add to BOTH `en` and `zh` dictionaries (keyset parity tests):
`research.title, research.days, research.score, research.return,
research.drawdown, research.winRate, research.trades, research.deploy,
research.deploying, research.deployed, research.archived, research.viewRun,
research.viewStrategy, research.recommended, research.failed,
research.noRecommendation, research.prefill, research.button`.

### 3.5 Frontend tests

`__tests__/StrategyResearchCard.test.tsx` (new): ranked render order,
recommended badge, failed row, deploy PATCH round trip + deployed state +
error path, status derivation from the strategies list. Extend
`__tests__/ChatPanel.test.tsx`: a message whose actions carry `research`
renders the card. Follow the `jest.mock('swr')` + `global.fetch` pattern;
mock `lightweight-charts` only if a chart is imported (prefer no chart in
the card).

## 4. E2E contract (LLM_MOCK)

- `test/specs/research.spec.ts` (US): send "Research momentum strategies
  for AAPL" from `/` → `research-card` visible with 3 `research-candidate`
  rows and one `research-recommended` → click the recommended row's
  `research-deploy` → button flips to deployed → `GET /api/strategies`
  (request fixture) shows exactly one `live` strategy among the three new
  drafts. Use `.last()` selectors (retries append history).
- `test/specs/research-cn.spec.ts` (CN): "帮我研究一下 600519 的策略" →
  the same card flow on 600519 with zh UI labels, except the recommendation
  is deterministically NULL (¥20,000 of cash_pct-20 sizing can never afford
  one ≥¥140k 100-share board lot of 600519 — appendix item 2): the spec
  asserts ZERO `research-recommended` badges (the card renders the muted
  no-recommendation note instead — pinned by jest, §3.5) and deploys the
  rank-1 row draft → live.
- Both specs self-seed their sample daily bars in beforeEach via
  `ensureSampleHistory` (history-helpers.ts; offline `sample` source) —
  AAPL (US) / 600519 (CN) — so they pass standalone under E2E_SPECS subsets
  and fresh volumes (appendix item 4).
- Wire both into the existing US/CN compose spec selection convention
  (test/README.md subsets updated).

## 5. Hard gates

1. Existing tests pass. Among pre-D4 test files, only (a) the §2.3 sha256
   constant update in tests/test_sentiment_context.py and (b) the purely
   ADDITIVE extensions this contract itself mandates — §2.5's research
   goldens in tests/test_chat_mock_golden.py and §3.5's research-card case
   in `__tests__/ChatPanel.test.tsx` — may change; no pre-existing test
   logic, assertion or fixture changes. (Amended: the original "no other
   existing test file changes" wording contradicted §2.5/§3.5 — appendix
   item 1.)
2. All existing golden fixtures byte-identical; new research goldens added,
   not substituted.
3. `FIELD_SPECS`/`D1_`/`D2_`/`ACTIVE_FIELD_SPECS` untouched; frozen
   signatures untouched; `ChatResponse` untouched; no schema.sql change; no
   new env vars.
4. Default behavior identical: responses/persisted actions gain the
   `research` key only when the action fires.
5. Zero real network in all tests (LLM_MOCK + committed sample bars).
6. Backend suite, jest suite, `next build`, then dual-market compose E2E
   (us + cn) all green before commit.

## 6. Out of scope (deferred)

- A research-sessions table / dedicated research page (chat history IS the
  session log).
- Second LLM pass to narrate results (ranking is deterministic; the model's
  per-candidate hypotheses carry the narrative).
- Multi-ticker portfolio research, optimizer, walk-forward splits.
- Auto-deploy, scheduled re-research, CN price-limit modeling inside the
  history engine (existing D1 behavior; documented limitation).

---

## Appendix: adjudicated deviations (fix round 1, 2026-07-13)

Precedent: D2's implementation-deviation ledger. Each item resolves a
contract-internal conflict or records an accepted departure from the letter
of a section; sections above were amended in place with pointers here.

1. **Gate 5.1 carve-out (contract-internal conflict).** §2.5 mandates
   extending `tests/test_chat_mock_golden.py` with the two research goldens
   and §3.5 mandates extending `__tests__/ChatPanel.test.tsx` with the
   research-card case, while gate 5.1 originally said "no other existing
   test file changes". Adjudicated in favour of §2.5/§3.5: gate 5.1 now
   sanctions exactly those purely additive extensions. Verified: the four
   pre-existing chat goldens, all pre-existing golden fixtures and every
   pre-existing test/assertion in both files are byte-unchanged.
2. **CN mock recommendation is null by construction (§2.4 vs §4).** The zh
   mock (600519, `cash_pct` 20 on the CN ¥100,000 seed) can never afford
   one 100-share board lot — ≥ ¥140k anywhere in the committed sample bars
   (the same "a 600519 history run can never fill" fact
   history-cn.spec.ts documents) — so all three CN candidates complete
   with zero round trips and §2.2 pins `recommended_strategy_id` to null
   (pinned by tests/golden/chat_mock_research_zh.json). Kept deliberately
   rather than switching to a cheaper CN sample ticker: the CN E2E
   exercises the zero-trade demotion / null-recommendation / rank-1 deploy
   path end-to-end, which the US spec cannot, while the US spec covers the
   recommended-badge path. §4's CN bullet was corrected accordingly.
3. **§3.2 status source is `/api/strategies?status=all`.** The originally
   specified `useSWR('/api/strategies')` default view excludes archived
   rows server-side (strategies contract §6, pinned), which made the
   mandated "archived" display unreachable in production. The card now
   queries status=all (runs.tsx precedent); a successful deploy mutates
   both that key and the plain '/api/strategies' key (AppShell's
   STRATEGIES_REVALIDATE_KEY). This also makes the E2E retry-isolation
   mechanism real: archived research drafts render the archived chip, not
   a disabled Deploy button.
4. **§4 specs self-seed history.** research.spec.ts / research-cn.spec.ts
   call `ensureSampleHistory` (sample source, zero network, idempotent) in
   beforeEach, so the documented test/README.md smoke subsets pass on a
   fresh volume instead of silently depending on the alphabetically
   earlier history specs having populated `daily_bars`.
5. **§2.1 `research` lives on a `ChatResearchTurnResponse` subclass**
   (backend/app/routes/chat.py:397), not on `ChatTurnResponse` directly:
   tests/test_chat_strategies.py:380 pins `set(ChatTurnResponse.model_fields)`
   and gate 5.1 forbids editing that pre-existing assertion. Same subclass
   pattern P2 used to grow `ChatResponse` into `ChatTurnResponse`; the LLM
   `response_format=` and both mock chains use the subclass, so behavior is
   exactly the contract's. §2.1 was amended with a pointer here.
