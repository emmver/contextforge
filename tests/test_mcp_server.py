"""Tests for the ContextForge MCP server tools."""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from contextforge.core.db import get_db, upsert_session
from contextforge.models.session import Message, Session


def _make_db(tmpdir: str):
    return get_db(Path(tmpdir) / "test.db")


def _seed_session(db, session_id="sess-001", tool="claude_code", token_count=1234):
    now = datetime.now(timezone.utc)
    upsert_session(db, Session(
        id=session_id,
        tool=tool,
        title="Test session",
        cwd="/home/user/project",
        created_at=now,
        updated_at=now,
        token_count=token_count,
    ))


def _mock_messages():
    return [
        Message(role="user", content="Write a hello world function."),
        Message(role="assistant", content="Here is a hello world function:\ndef hello():\n    return 'Hello, World!'"),
        Message(role="user", content="Add a docstring."),
        Message(role="assistant", content="def hello():\n    \"\"\"Return a greeting string.\"\"\"\n    return 'Hello, World!'"),
    ]


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------

def test_list_sessions_returns_all(tmp_path):
    db = get_db(tmp_path / "test.db")
    _seed_session(db, "s1", "claude_code")
    _seed_session(db, "s2", "codex")

    with patch("contextforge.mcp_server._db", return_value=db):
        from contextforge.mcp_server import list_sessions
        result = list_sessions()

    assert len(result) == 2
    ids = {r["id"] for r in result}
    assert ids == {"s1", "s2"}


def test_list_sessions_filters_by_tool(tmp_path):
    db = get_db(tmp_path / "test.db")
    _seed_session(db, "s1", "claude_code")
    _seed_session(db, "s2", "codex")

    with patch("contextforge.mcp_server._db", return_value=db):
        from contextforge.mcp_server import list_sessions
        result = list_sessions(tool="codex")

    assert len(result) == 1
    assert result[0]["id"] == "s2"


def test_list_sessions_respects_limit(tmp_path):
    db = get_db(tmp_path / "test.db")
    for i in range(10):
        _seed_session(db, f"s{i:03d}", "claude_code", token_count=i * 100)

    with patch("contextforge.mcp_server._db", return_value=db):
        from contextforge.mcp_server import list_sessions
        result = list_sessions(limit=3)

    assert len(result) == 3


def test_list_sessions_caps_limit_at_200(tmp_path):
    db = get_db(tmp_path / "test.db")
    _seed_session(db, "s1")

    with patch("contextforge.mcp_server._db", return_value=db):
        from contextforge.mcp_server import list_sessions
        # limit=500 should be capped at 200 internally
        result = list_sessions(limit=500)

    assert len(result) == 1


# ---------------------------------------------------------------------------
# get_session
# ---------------------------------------------------------------------------

def test_get_session_returns_dict(tmp_path):
    db = get_db(tmp_path / "test.db")
    _seed_session(db, "abc-123")

    with patch("contextforge.mcp_server._db", return_value=db):
        from contextforge.mcp_server import get_session
        result = get_session("abc-123")

    assert result is not None
    assert result["id"] == "abc-123"
    assert result["tool"] == "claude_code"


def test_get_session_returns_none_for_missing(tmp_path):
    db = get_db(tmp_path / "test.db")

    with patch("contextforge.mcp_server._db", return_value=db):
        from contextforge.mcp_server import get_session
        result = get_session("does-not-exist")

    assert result is None


# ---------------------------------------------------------------------------
# get_session_tokens
# ---------------------------------------------------------------------------

def test_get_session_tokens_structure(tmp_path):
    db = get_db(tmp_path / "test.db")
    _seed_session(db, "tok-1")

    mock_adapter = MagicMock()
    mock_adapter.load_messages.return_value = _mock_messages()

    with patch("contextforge.mcp_server._db", return_value=db), \
         patch("contextforge.core.token_analyzer.get_adapter", return_value=mock_adapter):
        from contextforge.mcp_server import get_session_tokens
        result = get_session_tokens("tok-1")

    assert result is not None
    assert result["session_id"] == "tok-1"
    assert result["turn_count"] == 4
    assert result["total_tokens"] > 0
    assert result["user_tokens"] + result["assistant_tokens"] == result["total_tokens"]
    assert len(result["turns"]) == 4
    assert "heaviest_turn" in result


def test_get_session_tokens_top_filter(tmp_path):
    db = get_db(tmp_path / "test.db")
    _seed_session(db, "tok-2")

    mock_adapter = MagicMock()
    mock_adapter.load_messages.return_value = _mock_messages()

    with patch("contextforge.mcp_server._db", return_value=db), \
         patch("contextforge.core.token_analyzer.get_adapter", return_value=mock_adapter):
        from contextforge.mcp_server import get_session_tokens
        result = get_session_tokens("tok-2", top=2)

    assert result is not None
    assert len(result["turns"]) == 2
    # top-2 heaviest should still be sorted by turn number
    turn_nums = [t["turn"] for t in result["turns"]]
    assert turn_nums == sorted(turn_nums)


def test_get_session_tokens_returns_none_for_missing(tmp_path):
    db = get_db(tmp_path / "test.db")

    mock_adapter = MagicMock()
    with patch("contextforge.mcp_server._db", return_value=db), \
         patch("contextforge.core.token_analyzer.get_adapter", return_value=mock_adapter):
        from contextforge.mcp_server import get_session_tokens
        result = get_session_tokens("no-such-session")

    assert result is None


# ---------------------------------------------------------------------------
# get_token_analytics
# ---------------------------------------------------------------------------

def test_get_token_analytics_structure(tmp_path):
    db = get_db(tmp_path / "test.db")
    _seed_session(db, "a1", "claude_code", 500)
    _seed_session(db, "a2", "codex", 300)

    with patch("contextforge.mcp_server._db", return_value=db):
        from contextforge.mcp_server import get_token_analytics
        result = get_token_analytics(window="30d")

    assert result["window"] == "30d"
    assert result["total_sessions"] == 2
    assert result["total_tokens"] == 800
    assert "claude_code" in result["session_count_by_tool"]
    assert "codex" in result["session_count_by_tool"]
    assert "date_range" in result


def test_get_token_analytics_empty_db(tmp_path):
    db = get_db(tmp_path / "test.db")

    with patch("contextforge.mcp_server._db", return_value=db):
        from contextforge.mcp_server import get_token_analytics
        result = get_token_analytics()

    assert result["total_sessions"] == 0
    assert result["total_tokens"] == 0
    assert result["active_tools"] == []


# ---------------------------------------------------------------------------
# get_activity_timeline
# ---------------------------------------------------------------------------

def test_get_activity_timeline_returns_buckets(tmp_path):
    db = get_db(tmp_path / "test.db")
    _seed_session(db, "t1")

    with patch("contextforge.mcp_server._db", return_value=db):
        from contextforge.mcp_server import get_activity_timeline
        result = get_activity_timeline(window="7d")

    assert isinstance(result, list)
    assert len(result) == 7  # 7 daily buckets for "7d"
    assert all("label" in b and "sessions" in b and "tokens" in b for b in result)


# ---------------------------------------------------------------------------
# get_top_projects
# ---------------------------------------------------------------------------

def test_get_top_projects_structure(tmp_path):
    db = get_db(tmp_path / "test.db")
    _seed_session(db, "p1")

    with patch("contextforge.mcp_server._db", return_value=db):
        from contextforge.mcp_server import get_top_projects
        result = get_top_projects(top_n=3)

    assert isinstance(result, list)
    assert all("project" in p and "session_count" in p for p in result)


# ---------------------------------------------------------------------------
# compare_sessions
# ---------------------------------------------------------------------------

def test_compare_sessions_sorted_by_tokens(tmp_path):
    db = get_db(tmp_path / "test.db")
    _seed_session(db, "c1", token_count=100)
    _seed_session(db, "c2", token_count=200)

    short_msgs = [Message(role="user", content="Hi")]
    long_msgs = _mock_messages()

    def fake_load(session_id):
        return long_msgs if session_id == "c2" else short_msgs

    mock_adapter = MagicMock()
    mock_adapter.load_messages.side_effect = fake_load

    with patch("contextforge.mcp_server._db", return_value=db), \
         patch("contextforge.core.token_analyzer.get_adapter", return_value=mock_adapter):
        from contextforge.mcp_server import compare_sessions
        result = compare_sessions(["c1", "c2"])

    assert result[0]["total_tokens"] >= result[1]["total_tokens"]


def test_compare_sessions_handles_missing_id(tmp_path):
    db = get_db(tmp_path / "test.db")
    _seed_session(db, "c1")

    mock_adapter = MagicMock()
    mock_adapter.load_messages.return_value = _mock_messages()

    with patch("contextforge.mcp_server._db", return_value=db), \
         patch("contextforge.core.token_analyzer.get_adapter", return_value=mock_adapter):
        from contextforge.mcp_server import compare_sessions
        result = compare_sessions(["c1", "nonexistent"])

    errors = [r for r in result if "error" in r]
    assert len(errors) == 1
    assert errors[0]["session_id"] == "nonexistent"
