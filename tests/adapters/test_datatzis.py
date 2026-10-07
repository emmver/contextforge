"""Tests for the datatzis harness adapter.

The harness stores per-repo data under ``<repo>/.datatzis/``: chat sessions in
``workspaces/*.json`` and one orchestrator execution per user prompt in
``tasks/*.json``.  Tasks are treated as sub-agent sessions, linked to their
workspace via tags.  All tests run offline against checked-in fixtures by
pointing ``DATATZIS_SEARCH_ROOTS`` at a fixture copy.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import pytest

from contextforge.adapters.datatzis import DatatzisAdapter
from contextforge.models.session import Session

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "datatzis"

WORKSPACE_ID = "11111111111111111111111111111111"
TASK_OK_ID = "22222222222222222222222222222222"
TASK_FAILED_ID = "33333333333333333333333333333333"


@pytest.fixture()
def adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DatatzisAdapter:
    """DatatzisAdapter pointed at a copy of the fixture tree."""
    root = tmp_path / "search-roots"
    shutil.copytree(FIXTURE_ROOT, root / "my-repo-parent")
    monkeypatch.setenv("DATATZIS_SEARCH_ROOTS", str(root))
    monkeypatch.setattr(DatatzisAdapter, "default_paths", [root])
    return DatatzisAdapter()


class TestDiscovery:
    def test_discovers_workspace_and_task_sessions(self, adapter: DatatzisAdapter) -> None:
        sessions = adapter.discover_sessions()
        ids = {s.id for s in sessions}
        assert ids == {WORKSPACE_ID, TASK_OK_ID, TASK_FAILED_ID}  # corrupt file ignored

    def test_workspace_session_fields(self, adapter: DatatzisAdapter) -> None:
        session = next(s for s in adapter.discover_sessions() if s.id == WORKSPACE_ID)
        assert isinstance(session, Session)
        assert session.tool == "datatzis"
        assert session.title == "Inflation analysis"
        assert session.cwd and session.cwd.endswith("my-repo")
        assert str(session.updated_at.isoformat()).startswith("2026-01-15T12:30:00")
        # created_at derived from the first linked task's started epoch
        assert str(session.created_at.isoformat()).startswith("2026-01-15T12:10:00")
        assert session.token_count and session.token_count > 0

    def test_task_sessions_tagged_as_subagents(self, adapter: DatatzisAdapter) -> None:
        sessions = adapter.discover_sessions()
        tasks = [s for s in sessions if s.id != WORKSPACE_ID]
        assert len(tasks) == 2
        for task in tasks:
            assert "subagent" in task.tags
            assert f"workspace:{WORKSPACE_ID}" in task.tags
            assert task.title  # derived from the task prompt
        ok = next(s for s in tasks if s.id == TASK_OK_ID)
        failed = next(s for s in tasks if s.id == TASK_FAILED_ID)
        assert ok.status == "completed"
        assert failed.status == "failed"
        assert failed.summary == "draft.md not found in workspace"

    def test_is_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATATZIS_SEARCH_ROOTS", str(FIXTURE_ROOT))
        assert DatatzisAdapter().is_available()
        monkeypatch.setenv("DATATZIS_SEARCH_ROOTS", "/nonexistent")
        assert not DatatzisAdapter().is_available()


class TestLoadMessages:
    def test_workspace_roles_notice_maps_to_system(self, adapter: DatatzisAdapter) -> None:
        messages = adapter.load_messages(WORKSPACE_ID)
        assert [m.role for m in messages] == ["user", "system", "assistant"]
        assert messages[1].content.startswith("Research stage:")

    def test_task_messages_prompt_and_aggregate_turn(self, adapter: DatatzisAdapter) -> None:
        messages = adapter.load_messages(TASK_OK_ID)
        assert [m.role for m in messages] == ["user", "assistant"]
        assistant = messages[1]
        assert "retrieve_documents" in [tc["name"] for tc in assistant.tool_calls]
        assert "edit_draft" in [tc["name"] for tc in assistant.tool_calls]
        outputs = [tr["output"] for tr in assistant.tool_results]
        assert any("CPI bulletin" in o for o in outputs)

    def test_task_failure_recorded_as_error_result(self, adapter: DatatzisAdapter) -> None:
        messages = adapter.load_messages(TASK_FAILED_ID)
        assistant = messages[-1]
        assert any("error:" in tr["output"] for tr in assistant.tool_results)

    def test_task_timestamps_from_epoch(self, adapter: DatatzisAdapter) -> None:
        messages = adapter.load_messages(TASK_OK_ID)
        assert isinstance(messages[0].timestamp, datetime)

    def test_load_unknown_id_returns_empty(self, adapter: DatatzisAdapter) -> None:
        assert adapter.load_messages("0" * 32) == []

    def test_token_counts_positive(self, adapter: DatatzisAdapter) -> None:
        for session_id in (WORKSPACE_ID, TASK_OK_ID):
            for m in adapter.load_messages(session_id):
                assert m.token_count and m.token_count > 0


class TestBuildInjectCommand:
    def test_resume(self, adapter: DatatzisAdapter) -> None:
        cmd = adapter.build_inject_command("ctx", target_session_id=WORKSPACE_ID, method="resume")
        assert cmd == f"datatzis chat --resume {WORKSPACE_ID}"

    def test_resume_with_cwd(self, adapter: DatatzisAdapter) -> None:
        cmd = adapter.build_inject_command(
            "ctx", target_session_id=WORKSPACE_ID, method="resume", cwd="/home/u/repo"
        )
        assert cmd == f"cd /home/u/repo && datatzis chat --resume {WORKSPACE_ID}"

    def test_default_and_new_with_prompt_open_chat(self, adapter: DatatzisAdapter) -> None:
        # The harness has no non-interactive system-prompt surface.
        assert adapter.build_inject_command("ctx") == "datatzis chat"
        cmd = adapter.build_inject_command("ctx", method="new_with_prompt", cwd="/home/u/repo")
        assert cmd == "cd /home/u/repo && datatzis chat"


class TestScannerIntegration:
    def test_scan_persists_datatzis_sessions_hermetically(
        self, adapter: DatatzisAdapter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from contextforge.core import scanner as scanner_module
        from contextforge.core.db import get_db
        from contextforge.core.scanner import scan

        db = get_db(tmp_path / "cf_test.db")
        monkeypatch.setattr(scanner_module, "get_available_adapters", lambda: [adapter])
        result = scan(db, quiet=True)
        assert result.total == 3
        assert not result.errors

        rows = [
            r
            for r in db["sessions"].rows_where("tool = 'datatzis'")
        ]
        assert len(rows) == 3
        sub_rows = [r for r in rows if "subagent" in (r.get("tags") or "")]
        assert len(sub_rows) == 2
        for row in sub_rows:
            assert WORKSPACE_ID in row["tags"]

        result2 = scan(db, quiet=True)
        assert result2.total == 3
        assert result2.new == 0