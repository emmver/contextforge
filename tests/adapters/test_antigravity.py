"""Tests for the Antigravity adapter using fixture data."""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from contextforge.adapters.antigravity import AntigravityAdapter, _count_tokens, _ms_to_dt


def test_ms_to_dt():
    ts = 1779426988061  # milliseconds
    dt = _ms_to_dt(ts)
    assert dt.year == 2026
    assert dt.tzinfo is not None


def test_count_tokens():
    text = "hello world"
    n = _count_tokens(text)
    assert isinstance(n, int) and n > 0


def test_discover_sessions_from_fixture():
    adapter = AntigravityAdapter()

    with tempfile.TemporaryDirectory() as tmpdir:
        history = Path(tmpdir) / ".gemini" / "antigravity-cli" / "history.jsonl"
        history.parent.mkdir(parents=True)
        entries = [
            {
                "display": "First prompt",
                "timestamp": 1700000000000,
                "workspace": "/tmp/ws1",
                "conversationId": "conv-a",
            },
            {
                "display": "Second prompt",
                "timestamp": 1700000100000,
                "workspace": "/tmp/ws1",
                "conversationId": "conv-a",
            },
            {
                "display": "Other prompt",
                "timestamp": 1700001000000,
                "workspace": "/tmp/ws2",
                "conversationId": "conv-b",
            },
        ]
        history.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        import contextforge.adapters.antigravity as mod
        original_history = mod._AGY_HISTORY
        mod._AGY_HISTORY = history
        try:
            sessions = adapter.discover_sessions()
        finally:
            mod._AGY_HISTORY = original_history

    assert len(sessions) == 2
    ids = {s.id for s in sessions}
    assert ids == {"conv-a", "conv-b"}

    conv_a = next(s for s in sessions if s.id == "conv-a")
    assert conv_a.title == "First prompt"
    assert conv_a.cwd == "/tmp/ws1"
    assert conv_a.tool == "antigravity"
    assert conv_a.created_at == _ms_to_dt(1700000000000)
    assert conv_a.updated_at == _ms_to_dt(1700000100000)


def test_discover_skips_entries_without_conversation_id():
    adapter = AntigravityAdapter()

    with tempfile.TemporaryDirectory() as tmpdir:
        history = Path(tmpdir) / ".gemini" / "antigravity-cli" / "history.jsonl"
        history.parent.mkdir(parents=True)
        entries = [
            {"display": "orphan", "timestamp": 1700000000000, "workspace": "/tmp/ws"},
            # No conversationId
        ]
        history.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        import contextforge.adapters.antigravity as mod
        original_history = mod._AGY_HISTORY
        mod._AGY_HISTORY = history
        try:
            sessions = adapter.discover_sessions()
        finally:
            mod._AGY_HISTORY = original_history

    assert sessions == []


def test_load_messages_json_sidecar():
    adapter = AntigravityAdapter()

    with tempfile.TemporaryDirectory() as tmpdir:
        history = Path(tmpdir) / ".gemini" / "antigravity-cli" / "history.jsonl"
        history.parent.mkdir(parents=True)
        history.write_text(json.dumps({"display": "hi", "timestamp": 1700000000000, "conversationId": "c1"}) + "\n")

        conversations = history.parent / "conversations"
        conversations.mkdir()
        json_path = conversations / "c1.json"
        json_path.write_text(
            json.dumps(
                {
                    "messages": [
                        {"role": "user", "content": "hello", "timestamp": 1700000000000},
                        {"role": "assistant", "content": "world", "timestamp": 1700000010000},
                    ]
                }
            )
        )

        import contextforge.adapters.antigravity as mod
        original_history = mod._AGY_HISTORY
        original_conv = mod._AGY_CONVERSATIONS
        mod._AGY_HISTORY = history
        mod._AGY_CONVERSATIONS = conversations
        try:
            messages = adapter.load_messages("c1")
        finally:
            mod._AGY_HISTORY = original_history
            mod._AGY_CONVERSATIONS = original_conv

    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "hello"
    assert messages[1].role == "assistant"
    assert messages[1].content == "world"


def test_load_messages_fallback_to_history():
    adapter = AntigravityAdapter()

    with tempfile.TemporaryDirectory() as tmpdir:
        history = Path(tmpdir) / ".gemini" / "antigravity-cli" / "history.jsonl"
        history.parent.mkdir(parents=True)
        entries = [
            {
                "display": "Prompt one",
                "timestamp": 1700000000000,
                "workspace": "/tmp/ws",
                "conversationId": "c1",
            },
            {
                "display": "Prompt two",
                "timestamp": 1700000010000,
                "workspace": "/tmp/ws",
                "conversationId": "c1",
            },
        ]
        history.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        import contextforge.adapters.antigravity as mod
        original_history = mod._AGY_HISTORY
        mod._AGY_HISTORY = history
        try:
            messages = adapter.load_messages("c1")
        finally:
            mod._AGY_HISTORY = original_history

    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "Prompt one"
    assert messages[1].role == "user"
    assert messages[1].content == "Prompt two"


def test_build_inject_command_new_session():
    adapter = AntigravityAdapter()
    cmd = adapter.build_inject_command("some context", method="system_prompt")
    assert "agy" in cmd
    assert "-i" in cmd
    assert "some context" in cmd


def test_build_inject_command_resume():
    adapter = AntigravityAdapter()
    cmd = adapter.build_inject_command("ctx", target_session_id="abc-123", method="resume")
    assert "agy" in cmd
    assert "--conversation" in cmd
    assert "abc-123" in cmd


def test_build_inject_command_with_cwd():
    adapter = AntigravityAdapter()
    cmd = adapter.build_inject_command("ctx", cwd="/tmp/proj")
    assert cmd.startswith("cd /tmp/proj")
    assert "agy" in cmd
