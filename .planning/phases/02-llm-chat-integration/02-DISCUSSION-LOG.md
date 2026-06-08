# Phase 2: LLM Chat Integration - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-05
**Phase:** 2-LLM Chat Integration
**Areas discussed:** Portfolio context depth, Conversation history depth, Mock response design

---

## Portfolio Context Depth

| Option | Description | Selected |
|--------|-------------|----------|
| Core only | Cash balance, total value, positions with P&L, watchlist ticker list (no live prices) | ✓ |
| Full with live watchlist prices | Everything above plus current price and daily change% for every watched ticker | |

**User's choice:** Core only
**Notes:** Watchlist prices not included in context; AI can still reason about portfolio composition and suggest trades from position data. Keeps prompts tight.

---

## Conversation History Depth

| Option | Description | Selected |
|--------|-------------|----------|
| Last 20 messages | 10 exchanges — bounded cost, enough context for multi-step conversations | ✓ |
| Last 10 messages | 5 exchanges — tighter context, lower cost, AI forgets more | |
| No limit | Full history — unbounded cost risk in long demo sessions | |

**User's choice:** Last 20 messages (10 exchanges)
**Notes:** Standard cap for a capstone demo. Ordered ascending by created_at for correct chronological context.

---

## Mock Response Design

| Option | Description | Selected |
|--------|-------------|----------|
| Exercises full pipeline | Message + trade (buy 5 AAPL) + watchlist change (add PYPL) — tests the entire auto-execution path | ✓ |
| Message-only response | Static message, no trades or watchlist changes — simpler but misses auto-execution coverage | |

**User's choice:** Exercises full pipeline
**Notes:** Mock constructs the ChatResponse Pydantic object directly (no LLM call), then runs the same auto-execution path as a real response. Ensures E2E tests verify the complete pipeline without OpenRouter.

---

## Claude's Discretion

- Pydantic model structure for ChatRequest and ChatResponse
- `actions` JSON field format in `chat_messages` (record executed outcomes with status)
- LLM call failure handling: HTTP 500 with `{"error": "LLM unavailable"}`, no retry
- `asyncio.to_thread` for blocking `litellm.completion` call
- `reasoning_effort="low"` per cerebras-inference skill

## Deferred Ideas

None — discussion stayed within phase scope.
