"""Adapter for Claude Desktop App local-agent-mode sessions.

These sessions are created by the Desktop App's built-in agent mode. Each session
is stored under:
  ~/Library/Application Support/Claude/local-agent-mode-sessions/
    <profile-uuid>/<workspace-uuid>/local_<desktop-session-id>/

Inside each session directory:
  - A sibling JSON file (local_<id>.json) holds metadata (title, cwd, cliSessionId)
  - An embedded .claude/projects/<encoded-cwd>/<cliSessionId>.jsonl holds the full
    conversation in the same JSONL format as regular Claude Code CLI sessions
  - An audit.jsonl holds a sparse high-level audit log (used as fallback only)

We use cliSessionId as the canonical session ID so the JSONL path matches.
"""
from __future__ import annotations

import json
import shlex
from datetime import datetime, timezone
from pathlib import Path

import tiktoken

from contextforge.adapters.base import ToolAdapter
from contextforge.models.session import Message, Session

_AGENT_SESSIONS_DIR = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Claude"
    / "local-agent-mode-sessions"
)


def _parse_content(content) -> str:
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


def _count_tokens(text: str) -> int:
    """Count tokens in text using tiktoken for Claude models."""
    try:
        enc = tiktoken.encoding_for_model("claude-3-5-sonnet-20241022")
        return len(enc.encode(text))
    except Exception:
        # Fallback: rough estimate (~4 chars per token)
        return len(text) // 4


class ClaudeDesktopAdapter(ToolAdapter):
    """Adapter for Claude Desktop App local agent-mode sessions."""

    tool_name = "claude_desktop"
    default_paths = [_AGENT_SESSIONS_DIR]

    def discover_sessions(self) -> list[Session]:
        if not _AGENT_SESSIONS_DIR.exists():
            return []

        sessions: list[Session] = []
        for profile_dir in sorted(_AGENT_SESSIONS_DIR.iterdir()):
            if not profile_dir.is_dir():
                continue
            for workspace_dir in sorted(profile_dir.iterdir()):
                if not workspace_dir.is_dir():
                    continue
                for session_dir in sorted(workspace_dir.iterdir()):
                    if not session_dir.is_dir():
                        continue

                    dir_name = session_dir.name
                    meta_file = workspace_dir / f"{dir_name}.json"
                    if not meta_file.exists():
                        continue

                    try:
                        with meta_file.open() as f:
                            meta = json.load(f)
                    except (json.JSONDecodeError, OSError):
                        continue

                    # Use cliSessionId as the canonical ID — it matches the embedded JSONL
                    cli_id = meta.get("cliSessionId")
                    if not cli_id:
                        continue

                    title = meta.get("title")
                    raw_cwd = meta.get("cwd") or meta.get("originCwd")
                    cwd = raw_cwd if raw_cwd and not raw_cwd.startswith("/sessions/") else None

                    ts_ms = meta.get("createdAt") or meta.get("lastActivityAt")
                    dt: datetime | None = None
                    if ts_ms:
                        try:
                            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                        except (TypeError, ValueError):
                            pass

                    # Find the embedded JSONL (preferred) or fall back to audit.jsonl
                    raw_path = self._find_embedded_jsonl(session_dir, cli_id)
                    if raw_path is None:
                        audit = session_dir / "audit.jsonl"
                        raw_path = audit if audit.exists() else None

                    if raw_path is None:
                        continue

                    if dt is None:
                        dt = datetime.fromtimestamp(raw_path.stat().st_mtime, tz=timezone.utc)

                    # Calculate token count from session content
                    token_count = self._count_session_tokens(cli_id)

                    sessions.append(
                        Session(
                            id=cli_id,
                            tool=self.tool_name,
                            title=title,
                            cwd=cwd,
                            created_at=dt,
                            updated_at=dt,
                            raw_path=str(raw_path),
                            status="unknown",
                            token_count=token_count,
                        )
                    )
        return sessions

    def _find_embedded_jsonl(self, session_dir: Path, cli_id: str) -> Path | None:
        """Find the embedded .claude/projects/*/<cli_id>.jsonl inside a session dir."""
        projects_dir = session_dir / ".claude" / "projects"
        if not projects_dir.exists():
            return None
        for project_dir in projects_dir.iterdir():
            candidate = project_dir / f"{cli_id}.jsonl"
            if candidate.exists():
                return candidate
        return None

    def load_messages(self, session_id: str) -> list[Message]:
        # Locate the session directory and its raw_path
        raw_path = self._find_raw_path(session_id)
        if raw_path is None or not raw_path.exists():
            return []

        # Embedded JSONL uses the same format as Claude Code CLI sessions
        if "audit.jsonl" not in raw_path.name:
            return self._parse_cli_jsonl(raw_path)

        # Fallback: parse audit.jsonl
        return self._parse_audit_jsonl(raw_path)

    def _find_raw_path(self, session_id: str) -> Path | None:
        """Find the raw_path for a session given its cliSessionId."""
        if not _AGENT_SESSIONS_DIR.exists():
            return None
        for profile_dir in _AGENT_SESSIONS_DIR.iterdir():
            if not profile_dir.is_dir():
                continue
            for workspace_dir in profile_dir.iterdir():
                if not workspace_dir.is_dir():
                    continue
                for session_dir in workspace_dir.iterdir():
                    if not session_dir.is_dir():
                        continue
                    meta_file = workspace_dir / f"{session_dir.name}.json"
                    if not meta_file.exists():
                        continue
                    try:
                        with meta_file.open() as f:
                            meta = json.load(f)
                    except (json.JSONDecodeError, OSError):
                        continue
                    if meta.get("cliSessionId") == session_id:
                        embedded = self._find_embedded_jsonl(session_dir, session_id)
                        if embedded:
                            return embedded
                        audit = session_dir / "audit.jsonl"
                        return audit if audit.exists() else None
        return None

    def _parse_cli_jsonl(self, path: Path) -> list[Message]:
        """Parse the embedded JSONL using the same format as Claude Code CLI sessions."""
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
                content_raw = msg_data.get("content", "")

                # Skip entries where content is an array of tool results/tool use
                # (these are Claude's internal messages, not user input or assistant responses)
                if isinstance(content_raw, list):
                    # Only include arrays that contain text blocks (actual assistant content)
                    if not any(block.get("type") == "text" for block in content_raw if isinstance(block, dict)):
                        continue

                content = _parse_content(content_raw)
                if not content:
                    continue

                ts: datetime | None = None
                ts_str = entry.get("timestamp")
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    except (ValueError, AttributeError):
                        pass

                messages.append(
                    Message(role="user" if role == "user" else "assistant",
                            content=content, timestamp=ts)
                )
        return messages

    def _parse_audit_jsonl(self, path: Path) -> list[Message]:
        """Fallback: parse the sparse audit.jsonl format."""
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

                msg = entry.get("message", {})
                role = msg.get("role", entry_type)
                if role not in ("user", "assistant"):
                    continue

                content = _parse_content(msg.get("content", ""))
                if not content:
                    continue

                ts: datetime | None = None
                ts_str = entry.get("_audit_timestamp")
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    except (ValueError, AttributeError):
                        pass

                messages.append(Message(role=role, content=content, timestamp=ts))
        return messages

    def _count_session_tokens(self, session_id: str) -> int:
        """Count total tokens in a session."""
        messages = self.load_messages(session_id)
        total = 0
        for msg in messages:
            total += _count_tokens(msg.content)
        return total

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
        cmd = f"claude --system-prompt {safe_ctx}"
        if cwd:
            cmd = f"cd {shlex.quote(cwd)} && {cmd}"
        return cmd
