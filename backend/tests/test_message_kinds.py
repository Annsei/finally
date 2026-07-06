"""Tests for chat message kinds (M2 Wave 2, Task A).

Covers:
- Migration: pre-M2 chat_messages tables (no ``kind`` column) gain
  ``kind TEXT NOT NULL DEFAULT 'chat'`` on init_db; existing rows read back
  kind='chat'; the migration is idempotent.
- GET /api/chat/ history items include the ``kind`` field for every kind.
- Chat context isolation: the conversation history sent to the LLM contains
  only kind='chat' rows — brief/rule/review rows never leak into the prompt.
"""

from __future__ import annotations

import json
import os
import sqlite3
from types import SimpleNamespace

import pytest

from app.db.connection import get_conn, init_db

# Timestamps that sort before any row written during the test run.
_ANCIENT_TS = "2000-01-01T00:00:0{i}+00:00"


def _make_pre_m2_chat_db(db_file: str) -> None:
    """Create a database whose chat_messages table predates the kind column."""
    conn = sqlite3.connect(db_file)
    try:
        conn.execute(
            """
            CREATE TABLE chat_messages (
                id         TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL DEFAULT 'default',
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                actions    TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO chat_messages (id, user_id, role, content, actions, created_at) "
            "VALUES ('old-user', 'default', 'user', 'hello there', NULL, "
            "'2026-01-01T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO chat_messages (id, user_id, role, content, actions, created_at) "
            "VALUES ('old-asst', 'default', 'assistant', 'hi!', ?, "
            "'2026-01-01T00:00:01+00:00')",
            (json.dumps({"trades": [], "watchlist_changes": []}),),
        )
        conn.commit()
    finally:
        conn.close()


def _chat_columns(db_file: str) -> dict[str, sqlite3.Row]:
    conn = get_conn(db_file)
    try:
        rows = conn.execute("PRAGMA table_info(chat_messages)").fetchall()
        return {row["name"]: row for row in rows}
    finally:
        conn.close()


class TestKindColumnMigration:
    """Pre-M2 database volumes upgrade in place on init_db (Task A)."""

    def test_old_schema_gains_kind_column(self, tmp_path):
        db_file = str(tmp_path / "old.db")
        _make_pre_m2_chat_db(db_file)

        # Sanity: old schema really lacks the kind column
        assert "kind" not in _chat_columns(db_file)

        init_db(db_file)  # what app startup does

        cols = _chat_columns(db_file)
        assert "kind" in cols, "chat_messages.kind missing after migration"
        assert cols["kind"]["notnull"] == 1

        # Existing rows read back as ordinary conversation turns
        conn = get_conn(db_file)
        try:
            rows = conn.execute(
                "SELECT id, kind FROM chat_messages ORDER BY created_at ASC"
            ).fetchall()
        finally:
            conn.close()
        assert [row["id"] for row in rows] == ["old-user", "old-asst"]
        assert all(row["kind"] == "chat" for row in rows)

    def test_migration_is_idempotent(self, tmp_path):
        db_file = str(tmp_path / "old.db")
        _make_pre_m2_chat_db(db_file)
        for _ in range(3):
            init_db(db_file)  # must never raise or duplicate columns

        assert list(_chat_columns(db_file)).count("kind") == 1
        conn = get_conn(db_file)
        try:
            count = conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
        finally:
            conn.close()
        assert count == 2


def _insert_noise_rows(db_file: str) -> dict[str, str]:
    """Insert one assistant row of each non-chat kind; returns {kind: content}."""
    noise = {
        "brief": "BRIEF NOISE: AAPL surged",
        "rule": "RULE NOISE: rule fired",
        "review": "REVIEW NOISE: daily review",
    }
    conn = get_conn(db_file)
    try:
        for i, (kind, content) in enumerate(noise.items()):
            conn.execute(
                "INSERT INTO chat_messages "
                "(id, user_id, role, content, actions, kind, created_at) "
                "VALUES (?, 'default', 'assistant', ?, NULL, ?, ?)",
                (f"noise-{kind}", content, kind, _ANCIENT_TS.format(i=i)),
            )
        conn.commit()
    finally:
        conn.close()
    return noise


def _capturing_completion_factory(captured: dict):
    """litellm.completion stand-in that records kwargs and returns valid JSON."""

    def fake_completion(*args, **kwargs):
        captured.update(kwargs)
        content = json.dumps({"message": "ok", "trades": [], "watchlist_changes": []})
        message = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    return fake_completion


@pytest.mark.asyncio
class TestChatContextIsolation:
    """Brief/rule/review rows never leak into the chat LLM's history window."""

    async def test_llm_history_contains_only_chat_kind_rows(self, chat_client, monkeypatch):
        import litellm

        # Seed an ordinary conversation turn (mock path writes kind='chat')
        resp = await chat_client.post("/api/chat/", json={"message": "first message"})
        assert resp.status_code == 200
        mock_reply = resp.json()["message"]

        noise = _insert_noise_rows(os.environ["DB_PATH"])

        captured: dict = {}
        monkeypatch.setenv("LLM_MOCK", "false")
        monkeypatch.setattr(litellm, "completion", _capturing_completion_factory(captured))

        resp = await chat_client.post("/api/chat/", json={"message": "second message"})
        assert resp.status_code == 200

        # messages[0] is the system prompt; the rest is history + the new turn
        history_contents = [m["content"] for m in captured["messages"][1:]]
        assert "first message" in history_contents
        assert mock_reply in history_contents
        assert history_contents[-1] == "second message"
        for content in noise.values():
            assert content not in history_contents

    async def test_get_history_returns_all_kinds_with_kind_field(self, chat_client):
        resp = await chat_client.post("/api/chat/", json={"message": "hello"})
        assert resp.status_code == 200

        _insert_noise_rows(os.environ["DB_PATH"])

        messages = (await chat_client.get("/api/chat/")).json()["messages"]
        assert all("kind" in m for m in messages)
        assert {m["kind"] for m in messages} == {"chat", "brief", "rule", "review"}

        # Ordinary conversation turns are kind='chat' for user AND assistant
        chat_rows = [m for m in messages if m["kind"] == "chat"]
        assert {m["role"] for m in chat_rows} == {"user", "assistant"}
