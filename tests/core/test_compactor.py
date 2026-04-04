"""Tests for core/compactor.py."""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

from contextforge.core.compactor import compact, _message_importance
from contextforge.core.db import get_db, upsert_session, update_summary
from contextforge.models.session import Message, Session
from contextforge.utils.tokens import count_tokens


def _seed_db(db, session_id="ses-001", tool="claude_code", summary="This is a test summary."):
    now = datetime.now(timezone.utc)
    session = Session(
        id=session_id,
        tool=tool,
        title="Test session",
        cwd="/tmp/test",
        created_at=now,
        updated_at=now,
    )
    upsert_session(db, session)
    update_summary(db, session_id, summary)


def test_compact_summary_only_respects_budget():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = get_db(Path(tmpdir) / "test.db")
        _seed_db(db, "s1", summary="Short summary for session one.")
        _seed_db(db, "s2", summary="Short summary for session two.")

        bundle = compact(db, ["s1", "s2"], strategy="summary_only", token_budget=512)

        assert bundle.token_count <= 512
        assert "s1" in bundle.source_sessions
        assert "s2" in bundle.source_sessions
        assert bundle.strategy == "summary_only"


def test_compact_key_messages_uses_adapter():
    messages = [
        Message(role="user", content="Implement a cache"),
        Message(role="assistant", content="Here is a caching implementation:\n```python\nclass Cache:\n    pass\n```"),
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        db = get_db(Path(tmpdir) / "test.db")
        _seed_db(db, "s1")

        mock_adapter = MagicMock()
        mock_adapter.load_messages.return_value = messages

        with patch("contextforge.core.compactor.get_adapter", return_value=mock_adapter):
            bundle = compact(db, ["s1"], strategy="key_messages", token_budget=2048)

        assert bundle.token_count <= 2048
        assert "cache" in bundle.compacted_text.lower() or "Cache" in bundle.compacted_text


def test_message_importance_scores_code_higher():
    msg_code = Message(role="assistant", content="```python\nclass Foo:\n    pass\n```")
    msg_plain = Message(role="user", content="hi")
    assert _message_importance(msg_code, 0, 2) > _message_importance(msg_plain, 0, 2)
