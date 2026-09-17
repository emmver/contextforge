"""Tests for the Cursor adapter using synthetic SQLite fixtures."""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from contextforge.adapters.cursor import CursorAdapter, _ms_to_dt, _uri_to_path, _count_tokens


def _make_global_db(path: Path, rows: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE cursorDiskKV (key TEXT UNIQUE, value BLOB)")
    for key, value in rows.items():
        conn.execute(
            "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
            (key, json.dumps(value)),
        )
    conn.commit()
    conn.close()


def test_ms_to_dt():
    dt = _ms_to_dt(1700000000000)
    assert dt is not None
    assert dt.tzinfo is not None


def test_ms_to_dt_none():
    assert _ms_to_dt(None) is None
    assert _ms_to_dt(0) is None


def test_uri_to_path_file_uri():
    assert _uri_to_path("file:///Users/alice/myproject") == "/Users/alice/myproject"


def test_uri_to_path_passthrough():
    assert _uri_to_path("/plain/path") == "/plain/path"


def test_count_tokens():
    n = _count_tokens("hello world")
    assert isinstance(n, int) and n > 0


def test_discover_sessions_with_headers():
    adapter = CursorAdapter()

    with tempfile.TemporaryDirectory() as tmpdir:
        global_db = Path(tmpdir) / "User" / "globalStorage" / "state.vscdb"
        composer_id = "comp-1"
        _make_global_db(
            global_db,
            {
                f"composerData:{composer_id}": {
                    "composerId": composer_id,
                    "name": "Fix the bug",
                    "createdAt": 1700000000000,
                    "lastUpdatedAt": 1700000100000,
                    "fullConversationHeadersOnly": [
                        {"bubbleId": "b1", "type": 1},
                        {"bubbleId": "b2", "type": 2},
                    ],
                },
                f"bubbleId:{composer_id}:b1": {"text": "hi", "type": 1, "createdAt": 1700000000000},
                f"bubbleId:{composer_id}:b2": {"text": "hello", "type": 2, "createdAt": 1700000050000},
            },
        )

        import contextforge.adapters.cursor as mod

        original_global = mod._GLOBAL_DB
        original_ws = mod._WORKSPACE_STORAGE_DIR
        mod._GLOBAL_DB = global_db
        mod._WORKSPACE_STORAGE_DIR = Path(tmpdir) / "User" / "workspaceStorage"
        try:
            sessions = adapter.discover_sessions()
        finally:
            mod._GLOBAL_DB = original_global
            mod._WORKSPACE_STORAGE_DIR = original_ws

    assert len(sessions) == 1
    session = sessions[0]
    assert session.id == composer_id
    assert session.title == "Fix the bug"
    assert session.tool == "cursor"
    assert session.created_at == _ms_to_dt(1700000000000)
    assert session.updated_at == _ms_to_dt(1700000100000)


def test_discover_sessions_title_fallback_to_first_bubble():
    adapter = CursorAdapter()

    with tempfile.TemporaryDirectory() as tmpdir:
        global_db = Path(tmpdir) / "User" / "globalStorage" / "state.vscdb"
        composer_id = "comp-2"
        _make_global_db(
            global_db,
            {
                f"composerData:{composer_id}": {
                    "composerId": composer_id,
                    "createdAt": 1700000000000,
                    "fullConversationHeadersOnly": [
                        {"bubbleId": "b1", "type": 1},
                    ],
                },
                f"bubbleId:{composer_id}:b1": {
                    "text": "Please refactor this function",
                    "type": 1,
                    "createdAt": 1700000000000,
                },
            },
        )

        import contextforge.adapters.cursor as mod

        original_global = mod._GLOBAL_DB
        original_ws = mod._WORKSPACE_STORAGE_DIR
        mod._GLOBAL_DB = global_db
        mod._WORKSPACE_STORAGE_DIR = Path(tmpdir) / "User" / "workspaceStorage"
        try:
            sessions = adapter.discover_sessions()
        finally:
            mod._GLOBAL_DB = original_global
            mod._WORKSPACE_STORAGE_DIR = original_ws

    assert len(sessions) == 1
    assert sessions[0].title == "Please refactor this function"


def test_discover_sessions_resolves_cwd_from_workspace():
    adapter = CursorAdapter()

    with tempfile.TemporaryDirectory() as tmpdir:
        user_dir = Path(tmpdir) / "User"
        global_db = user_dir / "globalStorage" / "state.vscdb"
        composer_id = "comp-3"
        _make_global_db(
            global_db,
            {
                f"composerData:{composer_id}": {
                    "composerId": composer_id,
                    "name": "Session with cwd",
                    "createdAt": 1700000000000,
                    "fullConversationHeadersOnly": [],
                },
            },
        )

        ws_dir = user_dir / "workspaceStorage" / "abc123"
        ws_dir.mkdir(parents=True)
        (ws_dir / "workspace.json").write_text(
            json.dumps({"folder": "file:///home/alice/project"})
        )
        ws_state_db = ws_dir / "state.vscdb"
        conn = sqlite3.connect(str(ws_state_db))
        conn.execute("CREATE TABLE ItemTable (key TEXT UNIQUE, value BLOB)")
        conn.execute(
            "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
            (
                "composer.composerData",
                json.dumps({"allComposers": [{"composerId": composer_id}]}),
            ),
        )
        conn.commit()
        conn.close()

        import contextforge.adapters.cursor as mod

        original_global = mod._GLOBAL_DB
        original_ws = mod._WORKSPACE_STORAGE_DIR
        mod._GLOBAL_DB = global_db
        mod._WORKSPACE_STORAGE_DIR = user_dir / "workspaceStorage"
        try:
            sessions = adapter.discover_sessions()
        finally:
            mod._GLOBAL_DB = original_global
            mod._WORKSPACE_STORAGE_DIR = original_ws

    assert len(sessions) == 1
    assert sessions[0].cwd == "/home/alice/project"


def test_load_messages_with_headers():
    adapter = CursorAdapter()

    with tempfile.TemporaryDirectory() as tmpdir:
        global_db = Path(tmpdir) / "User" / "globalStorage" / "state.vscdb"
        composer_id = "comp-4"
        _make_global_db(
            global_db,
            {
                f"composerData:{composer_id}": {
                    "composerId": composer_id,
                    "fullConversationHeadersOnly": [
                        {"bubbleId": "b1", "type": 1},
                        {"bubbleId": "b2", "type": 2},
                    ],
                },
                f"bubbleId:{composer_id}:b1": {
                    "text": "What does this do?",
                    "type": 1,
                    "createdAt": 1700000000000,
                },
                f"bubbleId:{composer_id}:b2": {
                    "text": "It does X.",
                    "type": 2,
                    "createdAt": 1700000010000,
                },
            },
        )

        import contextforge.adapters.cursor as mod

        original_global = mod._GLOBAL_DB
        mod._GLOBAL_DB = global_db
        try:
            messages = adapter.load_messages(composer_id)
        finally:
            mod._GLOBAL_DB = original_global

    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "What does this do?"
    assert messages[1].role == "assistant"
    assert messages[1].content == "It does X."


def test_load_messages_without_headers_sorts_by_created_at():
    adapter = CursorAdapter()

    with tempfile.TemporaryDirectory() as tmpdir:
        global_db = Path(tmpdir) / "User" / "globalStorage" / "state.vscdb"
        composer_id = "comp-5"
        _make_global_db(
            global_db,
            {
                f"composerData:{composer_id}": {"composerId": composer_id},
                f"bubbleId:{composer_id}:b2": {
                    "text": "second",
                    "type": 2,
                    "createdAt": 1700000020000,
                },
                f"bubbleId:{composer_id}:b1": {
                    "text": "first",
                    "type": 1,
                    "createdAt": 1700000010000,
                },
            },
        )

        import contextforge.adapters.cursor as mod

        original_global = mod._GLOBAL_DB
        mod._GLOBAL_DB = global_db
        try:
            messages = adapter.load_messages(composer_id)
        finally:
            mod._GLOBAL_DB = original_global

    assert [m.content for m in messages] == ["first", "second"]


def test_load_messages_missing_session_returns_empty():
    adapter = CursorAdapter()

    with tempfile.TemporaryDirectory() as tmpdir:
        global_db = Path(tmpdir) / "User" / "globalStorage" / "state.vscdb"
        _make_global_db(global_db, {})

        import contextforge.adapters.cursor as mod

        original_global = mod._GLOBAL_DB
        mod._GLOBAL_DB = global_db
        try:
            messages = adapter.load_messages("does-not-exist")
        finally:
            mod._GLOBAL_DB = original_global

    assert messages == []


def test_build_inject_command_new_session():
    adapter = CursorAdapter()
    cmd = adapter.build_inject_command("some context", method="system_prompt")
    assert "cursor-agent" in cmd
    assert "-p" in cmd
    assert "some context" in cmd


def test_build_inject_command_resume():
    adapter = CursorAdapter()
    cmd = adapter.build_inject_command("ctx", target_session_id="abc-123", method="resume")
    assert "cursor-agent" in cmd
    assert "--resume" in cmd
    assert "abc-123" in cmd


def test_build_inject_command_with_cwd():
    adapter = CursorAdapter()
    cmd = adapter.build_inject_command("ctx", cwd="/tmp/proj")
    assert cmd.startswith("cd /tmp/proj")
    assert "cursor-agent" in cmd
