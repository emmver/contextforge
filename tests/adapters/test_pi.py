"""Tests for the Pi coding agent adapter.

Pi stores main sessions as JSONL transcripts under
``~/.pi/agent/sessions/<escaped-cwd>/<timestamp>_<session-id>.jsonl`` and
sub-agent transcripts under a directory named after the parent session stem.
All tests run offline against checked-in fixtures.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from contextforge.adapters import pi as pi_module
from contextforge.adapters.pi import PiAdapter
from contextforge.models.session import Session

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "pi"

MAIN_SESSION_ID = "a1b2c3d4-0000-4000-8000-000000000001"
SUBAGENT_SESSION_ID = "b2c3d4e5-0000-4000-8000-000000000002"
SUBAGENT_RETRY_ID = "c3d4e5f6-0000-4000-8000-000000000004"
BROKEN_SESSION_ID = "c9d8e7f6-0000-4000-8000-000000000003"


@pytest.fixture()
def adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PiAdapter:
    """PiAdapter pointed at a copy of the fixture tree."""
    sessions_root = tmp_path / "sessions"
    shutil.copytree(FIXTURE_ROOT, sessions_root)
    monkeypatch.setattr(pi_module, "_SESSIONS_ROOT", sessions_root)
    monkeypatch.setattr(PiAdapter, "default_paths", [sessions_root])
    return PiAdapter()


class TestDiscovery:
    def test_discovers_main_and_subagent_sessions(self, adapter: PiAdapter) -> None:
        sessions = adapter.discover_sessions()
        ids = {s.id for s in sessions}
        # Two sub-agent runs share the file name session.jsonl but must keep
        # distinct ids derived from their headers.
        assert ids == {MAIN_SESSION_ID, SUBAGENT_SESSION_ID, SUBAGENT_RETRY_ID, BROKEN_SESSION_ID}

    def test_main_session_fields(self, adapter: PiAdapter) -> None:
        session = next(s for s in adapter.discover_sessions() if s.id == MAIN_SESSION_ID)
        assert isinstance(session, Session)
        assert session.tool == "pi"
        assert session.cwd == "/home/dev/project-a"
        assert session.raw_path and session.raw_path.endswith(".jsonl")
        assert str(session.created_at.isoformat()).startswith("2026-01-15T10:30:00")
        assert str(session.updated_at.isoformat()).startswith("2026-01-15T10:30:06")
        assert session.token_count and session.token_count > 0
        assert session.tags == []  # main sessions carry no tags

    def test_subagent_session_is_tagged_and_linked(self, adapter: PiAdapter) -> None:
        session = next(s for s in adapter.discover_sessions() if s.id == SUBAGENT_SESSION_ID)
        assert "subagent" in session.tags
        assert f"parent:{MAIN_SESSION_ID}" in session.tags
        assert session.title == "researcher"
        assert session.token_count and session.token_count > 0

    def test_subagent_runs_have_distinct_tokens(self, adapter: PiAdapter) -> None:
        sessions = adapter.discover_sessions()
        by_id = {s.id: s for s in sessions}
        assert by_id[SUBAGENT_SESSION_ID].title == "researcher"
        assert by_id[SUBAGENT_RETRY_ID].title == "worker"
        assert by_id[SUBAGENT_SESSION_ID].token_count != by_id[SUBAGENT_RETRY_ID].token_count

    def test_corrupt_tail_and_garbage_lines_tolerated(self, adapter: PiAdapter) -> None:
        session = next(s for s in adapter.discover_sessions() if s.id == BROKEN_SESSION_ID)
        assert session.token_count and session.token_count > 0
        assert session.cwd == "/home/dev/project-a"

    def test_is_available(self, adapter: PiAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
        assert adapter.is_available()
        missing = Path("/nonexistent/.pi/agent/sessions")
        monkeypatch.setattr(pi_module, "_SESSIONS_ROOT", missing)
        monkeypatch.setattr(PiAdapter, "default_paths", [missing])
        assert not PiAdapter().is_available()


class TestLoadMessages:
    def test_load_main_session_messages(self, adapter: PiAdapter) -> None:
        messages = adapter.load_messages(MAIN_SESSION_ID)
        roles = [m.role for m in messages]
        # Dead sibling branch (s2b/s2c) is excluded from the active chain
        assert roles == ["system", "user", "assistant", "assistant"]

    def test_system_preamble_extracted(self, adapter: PiAdapter) -> None:
        messages = adapter.load_messages(MAIN_SESSION_ID)
        assert "expert coding assistant" in messages[0].content

    def test_tool_call_captured_on_assistant(self, adapter: PiAdapter) -> None:
        messages = adapter.load_messages(MAIN_SESSION_ID)
        first_assistant = next(m for m in messages if m.role == "assistant")
        assert len(first_assistant.tool_calls) == 1
        assert first_assistant.tool_calls[0]["name"] == "bash"
        assert "rg login" in first_assistant.tool_calls[0]["input"]

    def test_tool_result_attributed_to_issuing_assistant(self, adapter: PiAdapter) -> None:
        messages = adapter.load_messages(MAIN_SESSION_ID)
        first_assistant = next(m for m in messages if m.role == "assistant")
        assert len(first_assistant.tool_results) == 1
        assert first_assistant.tool_results[0]["tool"] == "bash"
        assert "login_test.py" in first_assistant.tool_results[0]["output"]

    def test_token_counts_positive_and_summable(self, adapter: PiAdapter) -> None:
        messages = adapter.load_messages(MAIN_SESSION_ID)
        for m in messages:
            assert m.token_count and m.token_count > 0

    def test_load_subagent_messages(self, adapter: PiAdapter) -> None:
        messages = adapter.load_messages(SUBAGENT_SESSION_ID)
        assert [m.role for m in messages] == ["system", "user", "assistant"]
        assert "research worker" in messages[0].content

        retry = adapter.load_messages(SUBAGENT_RETRY_ID)
        assert [m.role for m in retry] == ["system", "user", "assistant"]
        assert "retry run" in retry[0].content

    def test_result_matched_by_toolcall_id_not_position(self, adapter: PiAdapter) -> None:
        """A result arriving after an intervening user message still attaches
        to the assistant turn that issued the tool call (steer flows)."""
        messages = adapter.load_messages(BROKEN_SESSION_ID)
        by_content = {m.content: m for m in messages if m.content}
        issuing = by_content["Running it now."]
        assert issuing.role == "assistant"
        assert issuing.tool_results[0]["output"] == "2 failed, 100 passed"
        assert "uv run pytest" in issuing.tool_calls[0]["input"]

    def test_load_unknown_id_returns_empty(self, adapter: PiAdapter) -> None:
        assert adapter.load_messages("does-not-exist-0000") == []

    def test_timestamps_parsed(self, adapter: PiAdapter) -> None:
        messages = adapter.load_messages(MAIN_SESSION_ID)
        assert messages[1].timestamp is not None
        assert messages[1].timestamp.isoformat().startswith("2026-01-15T10:30:03")


class TestScannerIntegration:
    def test_scan_persists_pi_sessions_hermetically(self, adapter: PiAdapter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """End-to-end: cf scan persists main + subagent Pi sessions with tags.

        The scanner is patched to only this fixture-backed adapter so the test
        is independent of other installed tools and real user data.
        """
        from contextforge.core import scanner as scanner_module
        from contextforge.core.db import get_db
        from contextforge.core.scanner import scan

        db = get_db(tmp_path / "cf_test.db")
        monkeypatch.setattr(scanner_module, "get_available_adapters", lambda: [adapter])
        result = scan(db, quiet=True)
        assert result.total == 4
        assert not result.errors

        pi_rows = list(db["sessions"].rows_where("tool = 'pi'"))
        assert len(pi_rows) == 4
        sub_rows = [r for r in pi_rows if "subagent" in (r.get("tags") or "")]
        assert len(sub_rows) == 2
        for row in sub_rows:
            assert MAIN_SESSION_ID in row["tags"]

        # Rescanning is idempotent: same rows, all "unchanged"
        result2 = scan(db, quiet=True)
        assert result2.total == 4
        assert result2.new == 0


class TestBuildInjectCommand:
    def test_system_prompt_default(self, adapter: PiAdapter) -> None:
        cmd = adapter.build_inject_command("Some context", cwd="/home/dev/project-a")
        assert cmd.startswith("cd /home/dev/project-a && ")
        import shlex

        assert shlex.split(cmd.split("&& ", 1)[1]) == ["pi", "--system-prompt", "Some context"]

    def test_new_with_prompt(self, adapter: PiAdapter) -> None:
        cmd = adapter.build_inject_command("ctx", method="new_with_prompt", cwd="/home/dev/project-a")
        assert cmd == "cd /home/dev/project-a && pi -p ctx"

    def test_resume(self, adapter: PiAdapter) -> None:
        cmd = adapter.build_inject_command(
            "ctx", target_session_id=MAIN_SESSION_ID, method="resume"
        )
        assert cmd == f"pi --session {MAIN_SESSION_ID} --append-system-prompt ctx"

    def test_quoting_survives_roundtrip(self, adapter: PiAdapter) -> None:
        import shlex

        context = "ctx with spaces 'quotes' and $vars"
        cmd = adapter.build_inject_command(context, method="new_with_prompt")
        parsed = shlex.split(cmd)
        assert parsed[-1] == context