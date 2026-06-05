---
phase: 2
slug: llm-chat-integration
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-05
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x (already configured in `backend/pyproject.toml`) |
| **Config file** | `backend/pyproject.toml` (`[tool.pytest.ini_options]`) |
| **Quick run command** | `cd backend && uv run --extra dev pytest tests/test_chat.py -x -q` |
| **Full suite command** | `cd backend && uv run --extra dev pytest tests/ -q` |
| **Estimated runtime** | ~10 seconds (mock LLM only, no real API calls) |

---

## Sampling Rate

- **After every task commit:** Run `cd backend && uv run --extra dev pytest tests/test_chat.py -x -q`
- **After every plan wave:** Run `cd backend && uv run --extra dev pytest tests/ -q`
- **Before `/gsd:verify-work`:** Full suite must be green (all 89 Phase 1 tests + new chat tests)
- **Max feedback latency:** ~10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Secure Behavior | Test Type | Automated Command | Status |
|---------|------|------|-------------|-----------------|-----------|-------------------|--------|
| extract-trade-helper | refactor | 0 | CHAT-03 | Trade validation identical to HTTP route | unit | `pytest tests/test_portfolio.py -q` | ⬜ pending |
| extract-watchlist-helper | refactor | 0 | CHAT-04 | Watchlist add/remove idempotent | unit | `pytest tests/test_watchlist.py -q` | ⬜ pending |
| create-chat-route | chat | 1 | CHAT-01, CHAT-02 | POST /api/chat returns structured JSON | unit | `pytest tests/test_chat.py::test_chat_real_response -q` | ⬜ pending |
| mock-mode | chat | 1 | CHAT-06 | LLM_MOCK=true skips OpenRouter entirely | unit | `pytest tests/test_chat.py::test_chat_mock_mode -q` | ⬜ pending |
| auto-exec-trades | chat | 1 | CHAT-03 | Trade outcomes included in response | unit | `pytest tests/test_chat.py::test_chat_mock_executes_trade -q` | ⬜ pending |
| auto-exec-watchlist | chat | 1 | CHAT-04 | Watchlist changes applied | unit | `pytest tests/test_chat.py::test_chat_mock_adds_watchlist -q` | ⬜ pending |
| chat-persistence | chat | 1 | CHAT-05 | Messages + actions stored in chat_messages | unit | `pytest tests/test_chat.py::test_chat_persists_messages -q` | ⬜ pending |
| register-router | main | 2 | CHAT-01 | Route visible in OpenAPI schema | integration | `pytest tests/test_chat.py::test_chat_endpoint_exists -q` | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `backend/tests/test_chat.py` — test stubs for CHAT-01 through CHAT-06 (create in Wave 0 before implementation)
- [ ] `backend/tests/conftest.py` — extend with `chat_client` fixture that sets `LLM_MOCK=true`

*Existing `tests/conftest.py` already has `client` fixture wiring — extend, do not replace.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| LLM response quality | CHAT-01 | Requires live OpenRouter key; subjective | Start app, open chat, ask "What's my portfolio worth?", verify coherent response |
| SSE stream unblocked during chat | CHAT-01 | Concurrency test — hard to assert in unit tests | Open SSE stream, submit chat request, verify price ticks continue arriving during LLM wait |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
