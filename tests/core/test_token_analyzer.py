"""Tests for core/token_analyzer.py."""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from contextforge.core.db import get_db, upsert_session
from contextforge.core.token_analyzer import analyze_tokens
from contextforge.models.session import Message, Session


def _seed(db, session_id="s1", tool="claude_code"):
    now = datetime.now(timezone.utc)
    upsert_session(db, Session(
        id=session_id, tool=tool, title="Test",
        cwd="/tmp", created_at=now, updated_at=now,
    ))


def _messages():
    return [
        Message(role="user", content="Hello, implement a binary search."),
        Message(role="assistant", content="Here is a binary search:\n```python\ndef bs(arr, t):\n    lo, hi = 0, len(arr)-1\n    while lo <= hi:\n        mid = (lo+hi)//2\n        if arr[mid] == t: return mid\n        elif arr[mid] < t: lo = mid+1\n        else: hi = mid-1\n    return -1\n```"),
        Message(role="user", content="Add tests please."),
        Message(role="assistant", content="Sure, here are pytest tests for binary search."),
    ]


def test_analyze_returns_correct_turn_count():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = get_db(Path(tmpdir) / "test.db")
        _seed(db)
        mock_adapter = MagicMock()
        mock_adapter.load_messages.return_value = _messages()
        with patch("contextforge.core.token_analyzer.get_adapter", return_value=mock_adapter):
            report = analyze_tokens(db, "s1")
    assert report is not None
    assert report.turn_count == 4


def test_analyze_cumulative_is_monotonically_increasing():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = get_db(Path(tmpdir) / "test.db")
        _seed(db)
        mock_adapter = MagicMock()
        mock_adapter.load_messages.return_value = _messages()
        with patch("contextforge.core.token_analyzer.get_adapter", return_value=mock_adapter):
            report = analyze_tokens(db, "s1")
    cumuls = [t.cumulative for t in report.turns]
    assert cumuls == sorted(cumuls)
    assert cumuls[-1] == report.total


def test_analyze_role_totals():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = get_db(Path(tmpdir) / "test.db")
        _seed(db)
        mock_adapter = MagicMock()
        mock_adapter.load_messages.return_value = _messages()
        with patch("contextforge.core.token_analyzer.get_adapter", return_value=mock_adapter):
            report = analyze_tokens(db, "s1")
    assert report.user_total + report.assistant_total == report.total
    assert report.user_total > 0
    assert report.assistant_total > 0


def test_analyze_max_turn_is_heaviest():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = get_db(Path(tmpdir) / "test.db")
        _seed(db)
        mock_adapter = MagicMock()
        mock_adapter.load_messages.return_value = _messages()
        with patch("contextforge.core.token_analyzer.get_adapter", return_value=mock_adapter):
            report = analyze_tokens(db, "s1")
    assert report.max_turn is not None
    assert report.max_turn.tokens == max(t.tokens for t in report.turns)


def test_analyze_returns_none_for_unknown_session():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = get_db(Path(tmpdir) / "test.db")
        mock_adapter = MagicMock()
        with patch("contextforge.core.token_analyzer.get_adapter", return_value=mock_adapter):
            report = analyze_tokens(db, "nonexistent")
    assert report is None


def test_analyze_empty_messages():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = get_db(Path(tmpdir) / "test.db")
        _seed(db)
        mock_adapter = MagicMock()
        mock_adapter.load_messages.return_value = []
        with patch("contextforge.core.token_analyzer.get_adapter", return_value=mock_adapter):
            report = analyze_tokens(db, "s1")
    assert report is not None
    assert report.turn_count == 0
    assert report.total == 0


def test_preview_is_single_line():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = get_db(Path(tmpdir) / "test.db")
        _seed(db)
        multiline_msg = Message(role="user", content="Line one\nLine two\nLine three")
        mock_adapter = MagicMock()
        mock_adapter.load_messages.return_value = [multiline_msg]
        with patch("contextforge.core.token_analyzer.get_adapter", return_value=mock_adapter):
            report = analyze_tokens(db, "s1")
    assert "\n" not in report.turns[0].content_preview
