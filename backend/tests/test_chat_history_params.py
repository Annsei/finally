"""Tests for the optional ``kind``/``limit`` params on GET /api/chat/ (P1 §3.6).

Covers kind filtering (all four kinds), invalid-kind 400s, limit sizing and
clamping (1..200), non-integer limit 400s, combined kind+limit, and — the P1
hard gate — a regression asserting the default (no params) response is
byte-identical to the pre-P1 shape: the 20 most recent messages of EVERY
kind, ascending by created_at.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.db.connection import get_conn


def _db_file(tmp_path) -> str:
    """The chat_client fixture's DB path (same tmp_path instance per test)."""
    return str(tmp_path / "test.db")


def _insert_message(
    db_file: str,
    content: str,
    kind: str,
    created_at: str,
    role: str = "assistant",
    actions: dict | None = None,
) -> None:
    conn = get_conn(db_file)
    try:
        conn.execute(
            "INSERT INTO chat_messages (id, user_id, role, content, actions, kind, created_at) "
            "VALUES (?, 'default', ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                role,
                content,
                json.dumps(actions) if actions is not None else None,
                kind,
                created_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _stamp(i: int) -> str:
    """Strictly increasing ISO created_at values."""
    return f"2026-01-01T{i // 3600:02d}:{(i // 60) % 60:02d}:{i % 60:02d}+00:00"


@pytest.mark.asyncio
class TestChatKindFilter:
    """GET /api/chat/?kind=... semantics."""

    async def test_kind_filters_to_single_kind(self, chat_client, tmp_path):
        db_file = _db_file(tmp_path)
        _insert_message(db_file, "hello", "chat", _stamp(1), role="user")
        _insert_message(db_file, "brief one", "brief", _stamp(2))
        _insert_message(db_file, "review one", "review", _stamp(3))
        _insert_message(db_file, "rule fired", "rule", _stamp(4))
        _insert_message(db_file, "review two", "review", _stamp(5))

        response = await chat_client.get("/api/chat/?kind=review")
        assert response.status_code == 200
        messages = response.json()["messages"]
        assert [m["content"] for m in messages] == ["review one", "review two"]
        assert all(m["kind"] == "review" for m in messages)

    async def test_each_valid_kind_is_accepted(self, chat_client, tmp_path):
        db_file = _db_file(tmp_path)
        for i, kind in enumerate(("chat", "brief", "review", "rule")):
            _insert_message(db_file, f"msg {kind}", kind, _stamp(i))

        for kind in ("chat", "brief", "review", "rule"):
            response = await chat_client.get(f"/api/chat/?kind={kind}")
            assert response.status_code == 200
            messages = response.json()["messages"]
            assert len(messages) == 1
            assert messages[0]["kind"] == kind

    async def test_invalid_kind_returns_400(self, chat_client):
        for bad in ("bogus", "CHAT", "reviews", ""):
            response = await chat_client.get(f"/api/chat/?kind={bad}")
            assert response.status_code == 400
            assert "error" in response.json()


@pytest.mark.asyncio
class TestChatLimitParam:
    """GET /api/chat/?limit=... semantics."""

    async def test_limit_returns_most_recent_n_ascending(self, chat_client, tmp_path):
        db_file = _db_file(tmp_path)
        for i in range(30):
            _insert_message(db_file, f"msg {i}", "chat", _stamp(i))

        response = await chat_client.get("/api/chat/?limit=10")
        assert response.status_code == 200
        messages = response.json()["messages"]
        assert [m["content"] for m in messages] == [f"msg {i}" for i in range(20, 30)]

    async def test_limit_above_default_returns_more_than_20(self, chat_client, tmp_path):
        db_file = _db_file(tmp_path)
        for i in range(25):
            _insert_message(db_file, f"msg {i}", "chat", _stamp(i))

        response = await chat_client.get("/api/chat/?limit=100")
        assert len(response.json()["messages"]) == 25

    async def test_limit_clamped_to_1_and_200(self, chat_client, tmp_path):
        db_file = _db_file(tmp_path)
        for i in range(205):
            _insert_message(db_file, f"msg {i}", "chat", _stamp(i))

        high = await chat_client.get("/api/chat/?limit=99999")
        assert high.status_code == 200
        messages = high.json()["messages"]
        assert len(messages) == 200
        assert messages[0]["content"] == "msg 5"  # 200 newest of 205

        for low in ("0", "-5"):
            response = await chat_client.get(f"/api/chat/?limit={low}")
            assert response.status_code == 200
            assert len(response.json()["messages"]) == 1

    async def test_non_integer_limit_returns_400(self, chat_client):
        for bad in ("abc", "2.5", ""):
            response = await chat_client.get(f"/api/chat/?limit={bad}")
            assert response.status_code == 400
            assert "error" in response.json()

    async def test_kind_and_limit_combined(self, chat_client, tmp_path):
        db_file = _db_file(tmp_path)
        for i in range(10):
            kind = "brief" if i % 2 == 0 else "chat"
            _insert_message(db_file, f"msg {i}", kind, _stamp(i))

        response = await chat_client.get("/api/chat/?kind=brief&limit=3")
        messages = response.json()["messages"]
        assert [m["content"] for m in messages] == ["msg 4", "msg 6", "msg 8"]
        assert all(m["kind"] == "brief" for m in messages)


@pytest.mark.asyncio
class TestChatDefaultRegression:
    """P1 hard gate: default GET /api/chat/ is byte-identical."""

    async def test_default_response_exactly_pre_p1_shape(self, chat_client, tmp_path):
        db_file = _db_file(tmp_path)
        # 25 messages of mixed kinds — default must return the 20 newest of
        # EVERY kind (no filter), ascending, with parsed actions.
        kinds = ("chat", "brief", "review", "rule")
        for i in range(25):
            actions = {"trades": [{"ticker": "AAPL"}]} if i == 24 else None
            _insert_message(
                db_file,
                f"msg {i}",
                kinds[i % 4],
                _stamp(i),
                role="user" if i % 5 == 0 else "assistant",
                actions=actions,
            )

        response = await chat_client.get("/api/chat/")
        assert response.status_code == 200
        body = response.json()

        expected_messages = [
            {
                "role": "user" if i % 5 == 0 else "assistant",
                "content": f"msg {i}",
                "actions": {"trades": [{"ticker": "AAPL"}]} if i == 24 else None,
                "kind": kinds[i % 4],
                "created_at": _stamp(i),
            }
            for i in range(5, 25)  # the 20 newest, ascending
        ]
        assert body == {"messages": expected_messages}
        # Serialized key ORDER is part of the byte contract.
        for message in body["messages"]:
            assert list(message.keys()) == [
                "role", "content", "actions", "kind", "created_at",
            ]
