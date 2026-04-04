"""Adapter for Claude Code (claude CLI) sessions."""
from __future__ import annotations

import json
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path

from contextforge.adapters.base import ToolAdapter
from contextforge.models.session import Message, Session

_HISTORY_PATH = Path.home() / ".claude" / "history.jsonl"
_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def _decode_path(encoded: str) -> str:
    """Convert Claude Code encoded project path back to a real path.

    Claude Code encodes the project directory as the path with '/' replaced by '-',
    with a leading '-' for the root '/'.  e.g. '-Users-alice-myproject'.
    """
    if encoded.startswith("-"):
        return "/" + encoded[1:].replace("-", "/")
    return encoded.replace("-", "/")


def _parse_content(content) -> str:
    """Extract text from a content field that may be a string or list of blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    parts.append(f"[tool: {block.get('name', '?')}]")
                elif block.get("type") == "tool_result":
                    inner = block.get("content", "")
                    if isinstance(inner, list):
                        for item in inner:
                            if isinstance(item, dict) and item.get("type") == "text":
                                parts.append(item.get("text", ""))
                    else:
                        parts.append(str(inner))
        return "\n".join(p for p in parts if p)
    return str(content)


class ClaudeCodeAdapter(ToolAdapter):
    tool_name = "claude_code"
    default_paths = [_HISTORY_PATH, _PROJECTS_DIR]

    def discover_sessions(self) -> list[Session]:
        sessions: list[Session] = []

        if _HISTORY_PATH.exists():
            sessions.extend(self._from_history())
        elif _PROJECTS_DIR.exists():
            sessions.extend(self._scan_projects_dir())

        return sessions

    def _from_history(self) -> list[Session]:
        sessions: list[Session] = []
        seen: set[str] = set()

        with _HISTORY_PATH.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                session_id = entry.get("sessionId") or entry.get("session_id")
                if not session_id or session_id in seen:
                    continue
                seen.add(session_id)

                display = entry.get("display") or entry.get("title") or ""
                project = entry.get("project") or entry.get("cwd") or ""
                ts = entry.get("timestamp")

                if ts:
                    try:
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except (ValueError, AttributeError):
                        dt = datetime.now(timezone.utc)
                else:
                    dt = datetime.now(timezone.utc)

                # Find the raw JSONL path for this session
                raw_path = self._find_session_path(project, session_id)

                sessions.append(
                    Session(
                        id=session_id,
                        tool=self.tool_name,
                        title=display or None,
                        cwd=project or None,
                        created_at=dt,
                        updated_at=dt,
                        raw_path=str(raw_path) if raw_path else None,
                        status="unknown",
                    )
                )

        return sessions

    def _find_session_path(self, cwd: str, session_id: str) -> Path | None:
        if not _PROJECTS_DIR.exists():
            return None
        # Try to match encoded dir by cwd
        for project_dir in _PROJECTS_DIR.iterdir():
            if not project_dir.is_dir():
                continue
            candidate = project_dir / f"{session_id}.jsonl"
            if candidate.exists():
                return candidate
        return None

    def _scan_projects_dir(self) -> list[Session]:
        sessions: list[Session] = []
        for project_dir in _PROJECTS_DIR.iterdir():
            if not project_dir.is_dir():
                continue
            cwd = _decode_path(project_dir.name)
            for jsonl_file in project_dir.glob("*.jsonl"):
                session_id = jsonl_file.stem
                mtime = jsonl_file.stat().st_mtime
                dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
                sessions.append(
                    Session(
                        id=session_id,
                        tool=self.tool_name,
                        title=None,
                        cwd=cwd,
                        created_at=dt,
                        updated_at=dt,
                        raw_path=str(jsonl_file),
                        status="unknown",
                    )
                )
        return sessions

    def load_messages(self, session_id: str) -> list[Message]:
        path = self._find_session_path("", session_id)
        if path is None:
            # Brute-force search
            for project_dir in _PROJECTS_DIR.iterdir():
                candidate = project_dir / f"{session_id}.jsonl"
                if candidate.exists():
                    path = candidate
                    break

        if path is None or not path.exists():
            return []

        messages: list[Message] = []
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                entry_type = entry.get("type", "")
                if entry_type not in ("user", "assistant"):
                    continue

                msg_data = entry.get("message", {})
                role = msg_data.get("role", entry_type)
                content = _parse_content(msg_data.get("content", ""))
                if not content:
                    continue

                ts_str = entry.get("timestamp")
                ts = None
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    except (ValueError, AttributeError):
                        pass

                messages.append(
                    Message(
                        role="user" if role == "user" else "assistant",
                        content=content,
                        timestamp=ts,
                    )
                )

        return messages

    def build_inject_command(
        self,
        context: str,
        target_session_id: str | None = None,
        cwd: str | None = None,
        method: str = "system_prompt",
    ) -> str:
        safe_ctx = shlex.quote(context)
        if method == "resume" and target_session_id:
            return f"claude --resume {target_session_id} --append-system-prompt {safe_ctx}"
        if method == "new_with_prompt":
            cmd = f"claude -p {safe_ctx}"
            if cwd:
                cmd = f"cd {shlex.quote(cwd)} && {cmd}"
            return cmd
        # Default: system_prompt for a new session
        cmd = f"claude --system-prompt {safe_ctx}"
        if cwd:
            cmd = f"cd {shlex.quote(cwd)} && {cmd}"
        return cmd
