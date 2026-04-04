"""Tests for Claude Code adapter using fixture data."""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from contextforge.adapters.claude_code import ClaudeCodeAdapter, _decode_path, _parse_content


def test_decode_path_absolute():
    assert _decode_path("-Users-alice-myproject") == "/Users/alice/myproject"


def test_decode_path_no_leading_dash():
    assert _decode_path("home-alice") == "home/alice"


def test_parse_content_string():
    assert _parse_content("hello") == "hello"


def test_parse_content_blocks():
    blocks = [
        {"type": "text", "text": "Part A"},
        {"type": "text", "text": "Part B"},
    ]
    result = _parse_content(blocks)
    assert "Part A" in result
    assert "Part B" in result


def test_load_messages_from_fixture():
    fixture = Path(__file__).parent.parent / "fixtures" / "claude_session_sample.jsonl"
    assert fixture.exists()

    # Monkey-patch the adapter to read from our fixture
    adapter = ClaudeCodeAdapter()

    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir) / ".claude" / "projects" / "-test-project"
        projects_dir.mkdir(parents=True)
        session_file = projects_dir / "test-session-123.jsonl"
        session_file.write_text(fixture.read_text())

        # Directly call the message parser
        import contextforge.adapters.claude_code as mod
        original = mod._PROJECTS_DIR
        mod._PROJECTS_DIR = Path(tmpdir) / ".claude" / "projects"
        try:
            messages = adapter.load_messages("test-session-123")
        finally:
            mod._PROJECTS_DIR = original

    assert len(messages) == 4
    assert messages[0].role == "user"
    assert "binary search tree" in messages[0].content
    assert messages[1].role == "assistant"


def test_build_inject_command_system_prompt():
    adapter = ClaudeCodeAdapter()
    cmd = adapter.build_inject_command("some context", method="system_prompt")
    assert "claude" in cmd
    assert "--system-prompt" in cmd


def test_build_inject_command_resume():
    adapter = ClaudeCodeAdapter()
    cmd = adapter.build_inject_command("context", target_session_id="abc-123", method="resume")
    assert "--resume" in cmd
    assert "abc-123" in cmd
