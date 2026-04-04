"""Tests for core/db.py."""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from contextforge.core.db import get_db, get_session, get_sessions, upsert_session
from contextforge.models.session import Message, Session


def make_session(session_id: str = "test-123") -> Session:
    now = datetime.now(timezone.utc)
    return Session(
        id=session_id,
        tool="claude_code",
        title="Test session",
        cwd="/tmp/test",
        created_at=now,
        updated_at=now,
        messages=[
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hi there"),
        ],
        status="unknown",
    )


def test_upsert_and_get():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = get_db(Path(tmpdir) / "test.db")
        session = make_session("abc-123")
        upsert_session(db, session)

        row = get_session(db, "abc-123")
        assert row is not None
        assert row["id"] == "abc-123"
        assert row["tool"] == "claude_code"
        assert row["first_message"] == "Hello"


def test_get_sessions_filter_by_tool():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = get_db(Path(tmpdir) / "test.db")
        upsert_session(db, make_session("s1"))
        s2 = make_session("s2")
        s2 = s2.model_copy(update={"tool": "codex"})
        upsert_session(db, s2)

        cc = get_sessions(db, tool="claude_code")
        assert len(cc) == 1
        assert cc[0]["id"] == "s1"


def test_schema_idempotent():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "test.db"
        db1 = get_db(path)
        db2 = get_db(path)  # should not raise
        assert "sessions" in db2.table_names()
