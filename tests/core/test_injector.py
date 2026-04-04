"""Tests for core/injector.py — build_inject_command and execute_transfer."""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from contextforge.core.db import get_db, get_bundle, upsert_session
from contextforge.core.injector import _INLINE_TOKEN_LIMIT, build_inject_command, execute_transfer
from contextforge.models.session import ContextBundle, Session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bundle(
    text: str = "Prior context summary.",
    tool: str | None = None,
    strategy: str = "summary_only",
    sessions: list[str] | None = None,
) -> ContextBundle:
    from contextforge.utils.tokens import count_tokens
    return ContextBundle(
        name="test-bundle",
        source_sessions=sessions or ["ses-001"],
        compacted_text=text,
        token_count=count_tokens(text),
        strategy=strategy,
        target_tool=tool,
    )


def _large_bundle() -> ContextBundle:
    """A bundle whose token count exceeds the inline limit."""
    # Build text that is definitely > _INLINE_TOKEN_LIMIT tokens
    text = ("This is a long prior context. " * 200).strip()
    from contextforge.utils.tokens import count_tokens
    token_count = count_tokens(text)
    # Patch the count so we don't need a truly huge string in tests
    b = ContextBundle(
        name="large-bundle",
        source_sessions=["ses-big"],
        compacted_text=text,
        token_count=_INLINE_TOKEN_LIMIT + 1,  # force file method
        strategy="key_messages",
        target_tool=None,
    )
    return b


# ---------------------------------------------------------------------------
# build_inject_command — per-tool command shape
# ---------------------------------------------------------------------------

class TestBuildInjectCommandClaudeCode:
    def test_new_session_uses_system_prompt(self):
        bundle = _bundle("some context")
        cmd, method = build_inject_command(bundle, "claude_code")
        assert method == "system_prompt"
        assert "claude" in cmd
        assert "--system-prompt" in cmd
        assert "some context" in cmd

    def test_resume_session_uses_append_system_prompt(self):
        bundle = _bundle("some context")
        cmd, method = build_inject_command(
            bundle, "claude_code", target_session_id="abc-123"
        )
        assert method == "resume"
        assert "--resume" in cmd
        assert "abc-123" in cmd

    def test_explicit_method_override(self):
        bundle = _bundle("ctx")
        cmd, method = build_inject_command(
            bundle, "claude_code", method="new_with_prompt"
        )
        assert method == "new_with_prompt"
        assert "claude" in cmd

    def test_cwd_prepended_for_new_session(self):
        bundle = _bundle("ctx")
        cmd, _ = build_inject_command(bundle, "claude_code", cwd="/tmp/myproject")
        assert "/tmp/myproject" in cmd
        assert cmd.startswith("cd ")


class TestBuildInjectCommandCodex:
    def test_new_session_uses_exec(self):
        bundle = _bundle("codex context")
        cmd, method = build_inject_command(bundle, "codex")
        assert method == "system_prompt"
        assert "codex exec" in cmd
        assert "codex context" in cmd

    def test_resume_session(self):
        bundle = _bundle("ctx")
        cmd, method = build_inject_command(
            bundle, "codex", target_session_id="thread-999"
        )
        assert method == "resume"
        assert "codex resume" in cmd
        assert "thread-999" in cmd

    def test_fork_method(self):
        bundle = _bundle("ctx")
        cmd, method = build_inject_command(
            bundle, "codex", target_session_id="thread-999", method="fork"
        )
        assert method == "fork"
        assert "codex fork" in cmd
        assert "thread-999" in cmd


class TestBuildInjectCommandAltimateCode:
    def test_new_session(self):
        bundle = _bundle("altimate context")
        cmd, method = build_inject_command(bundle, "altimate_code")
        assert method == "system_prompt"
        assert "altimate-code run" in cmd
        assert "altimate context" in cmd

    def test_resume_session(self):
        bundle = _bundle("ctx")
        cmd, method = build_inject_command(
            bundle, "altimate_code", target_session_id="ses-alt-01"
        )
        assert method == "resume"
        assert "altimate-code run -s" in cmd
        assert "ses-alt-01" in cmd

    def test_fork_method(self):
        bundle = _bundle("ctx")
        cmd, method = build_inject_command(
            bundle, "altimate_code", target_session_id="ses-alt-01", method="fork"
        )
        assert method == "fork"
        assert "--fork" in cmd


# ---------------------------------------------------------------------------
# Large-context → file injection
# ---------------------------------------------------------------------------

class TestLargeContextFileInjection:
    def test_large_bundle_uses_file_method(self):
        bundle = _large_bundle()
        cmd, method = build_inject_command(bundle, "claude_code")
        assert method == "file"

    def test_large_bundle_command_references_context_md(self):
        bundle = _large_bundle()
        cmd, method = build_inject_command(bundle, "claude_code")
        assert "CONTEXT.md" in cmd

    def test_large_bundle_codex_uses_file_method(self):
        bundle = _large_bundle()
        cmd, method = build_inject_command(bundle, "codex")
        assert method == "file"
        assert "CONTEXT.md" in cmd

    def test_execute_transfer_writes_context_md(self):
        bundle = _large_bundle()

        with tempfile.TemporaryDirectory() as tmpdir:
            db = get_db(Path(tmpdir) / "test.db")

            with patch("contextforge.core.injector.subprocess.run") as mock_run:
                execute_transfer(
                    db=db,
                    bundle=bundle,
                    bundle_id=1,
                    target_tool="claude_code",
                    cwd=tmpdir,
                )

            context_md = Path(tmpdir) / "CONTEXT.md"
            assert context_md.exists(), "CONTEXT.md should be written for large bundles"
            content = context_md.read_text()
            assert bundle.compacted_text[:50] in content

    def test_small_bundle_does_not_write_context_md(self):
        bundle = _bundle("Small context that fits inline.")

        with tempfile.TemporaryDirectory() as tmpdir:
            db = get_db(Path(tmpdir) / "test.db")

            with patch("contextforge.core.injector.subprocess.run"):
                execute_transfer(
                    db=db,
                    bundle=bundle,
                    bundle_id=1,
                    target_tool="claude_code",
                    cwd=tmpdir,
                )

            context_md = Path(tmpdir) / "CONTEXT.md"
            assert not context_md.exists(), "CONTEXT.md should NOT be written for small bundles"


# ---------------------------------------------------------------------------
# Transfer DB recording
# ---------------------------------------------------------------------------

class TestTransferDBRecording:
    def test_execute_transfer_records_to_db(self):
        bundle = _bundle("Transfer context")

        with tempfile.TemporaryDirectory() as tmpdir:
            db = get_db(Path(tmpdir) / "test.db")

            with patch("contextforge.core.injector.subprocess.run"):
                execute_transfer(
                    db=db,
                    bundle=bundle,
                    bundle_id=42,
                    target_tool="codex",
                    cwd=tmpdir,
                )

            transfers = list(db["transfers"].rows)
            assert len(transfers) == 1
            t = transfers[0]
            assert t["bundle_id"] == 42
            assert t["target_tool"] == "codex"
            assert "codex" in t["command_used"]

    def test_execute_transfer_records_method(self):
        bundle = _bundle("ctx")

        with tempfile.TemporaryDirectory() as tmpdir:
            db = get_db(Path(tmpdir) / "test.db")

            with patch("contextforge.core.injector.subprocess.run"):
                execute_transfer(
                    db=db,
                    bundle=bundle,
                    bundle_id=1,
                    target_tool="altimate_code",
                    method="resume",
                    target_session_id="ses-test",
                    cwd=tmpdir,
                )

            t = list(db["transfers"].rows)[0]
            assert t["method"] == "resume"

    def test_execute_transfer_runs_command(self):
        bundle = _bundle("ctx")

        with tempfile.TemporaryDirectory() as tmpdir:
            db = get_db(Path(tmpdir) / "test.db")

            with patch("contextforge.core.injector.subprocess.run") as mock_run:
                execute_transfer(
                    db=db,
                    bundle=bundle,
                    bundle_id=1,
                    target_tool="claude_code",
                    cwd=tmpdir,
                )

            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args
            assert call_kwargs.kwargs.get("shell") is True


# ---------------------------------------------------------------------------
# Cross-tool transfer scenarios
# ---------------------------------------------------------------------------

class TestCrossToolTransfer:
    """Verify that context from tool A can be injected into tool B."""

    def test_claude_code_to_codex(self):
        bundle = _bundle(
            "Session on utastar_thesis: implemented ensemble pruning. "
            "Files changed: utastar.py, prune.py.",
            tool="codex",
            sessions=["cc-session-abc"],
        )
        cmd, method = build_inject_command(bundle, "codex")
        assert "codex" in cmd
        assert method in ("system_prompt", "resume")

    def test_codex_to_altimate_code(self):
        bundle = _bundle(
            "Codex session: refactored data pipeline. Key decision: switch to DuckDB.",
            tool="altimate_code",
            sessions=["codex-thread-xyz"],
        )
        cmd, method = build_inject_command(bundle, "altimate_code")
        assert "altimate-code" in cmd

    def test_altimate_code_to_claude_code(self):
        bundle = _bundle(
            "altimate-code session: built Jira MCP integration. Auth via OAuth.",
            tool="claude_code",
            sessions=["alt-ses-123"],
        )
        cmd, method = build_inject_command(bundle, "claude_code")
        assert "claude" in cmd
        assert "OAuth" in cmd or "altimate" in cmd.lower() or "--system-prompt" in cmd

    def test_multi_session_bundle_claude_to_codex(self):
        """Multi-source bundle (2 CC sessions) injected into Codex."""
        bundle = _bundle(
            "Session A: designed DB schema.\n\n---\n\nSession B: wrote migration scripts.",
            tool="codex",
            sessions=["cc-a", "cc-b"],
        )
        assert len(bundle.source_sessions) == 2
        cmd, _ = build_inject_command(bundle, "codex")
        assert "codex" in cmd

    def test_unknown_tool_raises(self):
        bundle = _bundle("ctx")
        with pytest.raises(ValueError, match="Unknown tool"):
            build_inject_command(bundle, "nonexistent_tool")
