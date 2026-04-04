"""Build and optionally execute context injection commands."""
from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path

import sqlite_utils

from contextforge.adapters.registry import get_adapter
from contextforge.core.db import save_transfer
from contextforge.models.session import ContextBundle

# If compacted text exceeds this, write to a file instead of inline system prompt
_INLINE_TOKEN_LIMIT = 4096


def build_inject_command(
    bundle: ContextBundle,
    target_tool: str,
    target_session_id: str | None = None,
    cwd: str | None = None,
    method: str | None = None,
) -> tuple[str, str]:
    """Return (shell_command, method_used)."""
    adapter = get_adapter(target_tool)

    # Choose injection method based on token count
    if method is None:
        if bundle.token_count > _INLINE_TOKEN_LIMIT:
            method = "file"
        elif target_session_id:
            method = "resume"
        else:
            method = "system_prompt"

    if method == "file":
        # Context goes into a temp CONTEXT.md; system prompt references it
        ctx_summary = (
            f"Prior session context has been written to CONTEXT.md in this directory. "
            f"Read it before proceeding. Summary: {bundle.compacted_text[:300]}..."
        )
        cmd = adapter.build_inject_command(
            context=ctx_summary,
            target_session_id=None,
            cwd=cwd,
            method="system_prompt",
        )
    else:
        cmd = adapter.build_inject_command(
            context=bundle.compacted_text,
            target_session_id=target_session_id,
            cwd=cwd,
            method=method,
        )

    return cmd, method


def execute_transfer(
    db: sqlite_utils.Database,
    bundle: ContextBundle,
    bundle_id: int,
    target_tool: str,
    target_session_id: str | None = None,
    cwd: str | None = None,
    method: str | None = None,
) -> str:
    """Build the command, write CONTEXT.md if needed, record transfer, and execute."""
    cmd, actual_method = build_inject_command(
        bundle, target_tool, target_session_id, cwd, method
    )

    context_file: Path | None = None

    if actual_method == "file":
        work_dir = Path(cwd) if cwd else Path.cwd()
        context_file = work_dir / "CONTEXT.md"
        context_file.write_text(bundle.compacted_text, encoding="utf-8")

    save_transfer(
        db=db,
        bundle_id=bundle_id,
        target_tool=target_tool,
        method=actual_method,
        command_used=cmd,
    )

    # Execute via shell
    subprocess.run(cmd, shell=True, cwd=cwd or None)

    return cmd
