"""Tests for core/summarizer.py."""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from contextforge.core.db import get_db, get_session, upsert_session
from contextforge.core.summarizer import batch_summarize, summarize_session
from contextforge.models.config import ForgeConfig, LLMConfig
from contextforge.models.session import Message, Session


def _make_session(session_id: str, tool: str = "claude_code") -> Session:
    now = datetime.now(timezone.utc)
    return Session(
        id=session_id,
        tool=tool,
        title="Test session",
        cwd="/tmp/test",
        created_at=now,
        updated_at=now,
    )


def _config_with_key(api_key: str = "sk-test-key") -> ForgeConfig:
    return ForgeConfig(llm=LLMConfig(api_key=api_key))


def _config_no_key() -> ForgeConfig:
    return ForgeConfig(llm=LLMConfig(api_key=None))


def _make_messages() -> list[Message]:
    return [
        Message(role="user", content="Implement a Redis cache module"),
        Message(role="assistant", content="Here is the Redis cache implementation:\n```python\nclass RedisCache:\n    pass\n```"),
        Message(role="user", content="Add a TTL parameter"),
        Message(role="assistant", content="Updated with TTL support."),
    ]


# ---------------------------------------------------------------------------
# LLM path (mocked Anthropic client)
# ---------------------------------------------------------------------------

def test_summarize_calls_llm_when_api_key_set():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = get_db(Path(tmpdir) / "test.db")
        session = _make_session("s1")
        upsert_session(db, session)

        mock_content = MagicMock()
        mock_content.text = "Session worked on Redis caching with TTL support."
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        mock_adapter = MagicMock()
        mock_adapter.get_rollup_summary.return_value = None
        mock_adapter.load_messages.return_value = _make_messages()

        with patch("contextforge.core.summarizer.get_adapter", return_value=mock_adapter), \
             patch("anthropic.Anthropic", return_value=mock_client):
            summary = summarize_session(db, "s1", _config_with_key())

        assert summary == "Session worked on Redis caching with TTL support."
        row = get_session(db, "s1")
        assert row["summary"] == summary


def test_summarize_returns_cached_without_llm_call():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = get_db(Path(tmpdir) / "test.db")
        session = _make_session("s2")
        upsert_session(db, session)
        from contextforge.core.db import update_summary
        update_summary(db, "s2", "Cached summary text.")

        mock_adapter = MagicMock()
        with patch("contextforge.core.summarizer.get_adapter", return_value=mock_adapter):
            summary = summarize_session(db, "s2", _config_with_key())

        assert summary == "Cached summary text."
        mock_adapter.get_rollup_summary.assert_not_called()
        mock_adapter.load_messages.assert_not_called()


def test_summarize_force_bypasses_cache():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = get_db(Path(tmpdir) / "test.db")
        session = _make_session("s3")
        upsert_session(db, session)
        from contextforge.core.db import update_summary
        update_summary(db, "s3", "Old cached summary.")

        mock_content = MagicMock()
        mock_content.text = "Fresh LLM summary."
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        mock_adapter = MagicMock()
        mock_adapter.get_rollup_summary.return_value = None
        mock_adapter.load_messages.return_value = _make_messages()

        with patch("contextforge.core.summarizer.get_adapter", return_value=mock_adapter), \
             patch("anthropic.Anthropic", return_value=mock_client):
            summary = summarize_session(db, "s3", _config_with_key(), force=True)

        assert summary == "Fresh LLM summary."


# ---------------------------------------------------------------------------
# Codex rollup shortcut
# ---------------------------------------------------------------------------

def test_summarize_uses_rollup_summary_without_llm():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = get_db(Path(tmpdir) / "test.db")
        session = _make_session("codex-1", tool="codex")
        upsert_session(db, session)

        mock_adapter = MagicMock()
        mock_adapter.get_rollup_summary.return_value = "Pre-computed rollup summary from Codex."

        with patch("contextforge.core.summarizer.get_adapter", return_value=mock_adapter):
            summary = summarize_session(db, "codex-1", _config_no_key())

        assert summary == "Pre-computed rollup summary from Codex."
        mock_adapter.load_messages.assert_not_called()
        row = get_session(db, "codex-1")
        assert row["summary"] == "Pre-computed rollup summary from Codex."


# ---------------------------------------------------------------------------
# Graceful degradation (no API key)
# ---------------------------------------------------------------------------

def test_summarize_falls_back_to_first_message_preview_when_no_key():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = get_db(Path(tmpdir) / "test.db")
        session = _make_session("s4")
        upsert_session(db, session)

        mock_adapter = MagicMock()
        mock_adapter.get_rollup_summary.return_value = None
        mock_adapter.load_messages.return_value = _make_messages()

        with patch("contextforge.core.summarizer.get_adapter", return_value=mock_adapter):
            summary = summarize_session(db, "s4", _config_no_key())

        assert summary is not None
        assert "Redis" in summary  # first user message content
        assert len(summary) <= 200


def test_summarize_returns_none_when_no_messages():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = get_db(Path(tmpdir) / "test.db")
        session = _make_session("s5")
        upsert_session(db, session)

        mock_adapter = MagicMock()
        mock_adapter.get_rollup_summary.return_value = None
        mock_adapter.load_messages.return_value = []

        with patch("contextforge.core.summarizer.get_adapter", return_value=mock_adapter):
            summary = summarize_session(db, "s5", _config_no_key())

        assert summary is None


def test_summarize_returns_none_for_unknown_session():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = get_db(Path(tmpdir) / "test.db")
        summary = summarize_session(db, "nonexistent-id", _config_no_key())
        assert summary is None


# ---------------------------------------------------------------------------
# batch_summarize
# ---------------------------------------------------------------------------

def test_batch_summarize_counts():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = get_db(Path(tmpdir) / "test.db")
        for i in range(3):
            upsert_session(db, _make_session(f"batch-{i}"))

        mock_adapter = MagicMock()
        mock_adapter.get_rollup_summary.return_value = None
        mock_adapter.load_messages.return_value = _make_messages()

        with patch("contextforge.core.summarizer.get_adapter", return_value=mock_adapter):
            result = batch_summarize(db, _config_no_key())

        # All 3 sessions should get the first-message preview
        assert result.summarized == 3
        assert result.skipped == 0
        assert result.errors == []


def test_batch_summarize_skips_already_summarized():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = get_db(Path(tmpdir) / "test.db")
        from contextforge.core.db import update_summary

        for i in range(2):
            upsert_session(db, _make_session(f"bs-{i}"))
        update_summary(db, "bs-0", "Already summarized.")

        mock_adapter = MagicMock()
        mock_adapter.get_rollup_summary.return_value = None
        mock_adapter.load_messages.return_value = _make_messages()

        with patch("contextforge.core.summarizer.get_adapter", return_value=mock_adapter):
            result = batch_summarize(db, _config_no_key())

        # Only bs-1 should be summarized (bs-0 is already done)
        assert result.summarized == 1
