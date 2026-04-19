"""Adapter for Claude Code (claude CLI) sessions."""
from __future__ import annotations

import json
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path

import tiktoken

from contextforge.adapters.base import ToolAdapter
from contextforge.models.session import Message, Session

_HISTORY_PATH = Path.home() / ".claude" / "history.jsonl"
_PROJECTS_DIR = Path.home() / ".claude" / "projects"
_DESKTOP_SESSIONS_DIR = (
    Path.home() / "Library" / "Application Support" / "Claude" / "claude-code-sessions"
)


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


def _count_tokens(text: str) -> int:
    """Count tokens in text using tiktoken for Claude models."""
    try:
        enc = tiktoken.encoding_for_model("claude-3-5-sonnet-20241022")
        return len(enc.encode(text))
    except Exception:
        # Fallback: rough estimate (~4 chars per token)
        return len(text) // 4


class ClaudeCodeAdapter(ToolAdapter):
    tool_name = "claude_code"
    default_paths = [_HISTORY_PATH, _PROJECTS_DIR]

    def discover_sessions(self) -> list[Session]:
        """Discover sessions from both history.jsonl and projects directory.

        history.jsonl only contains CLI-initiated sessions (not Desktop App sessions),
        so we always scan the projects directory and use history.jsonl for metadata
        enrichment only.
        """
        # Build metadata map from history.jsonl (display name, timestamp, cwd)
        history_meta: dict[str, dict] = {}
        if _HISTORY_PATH.exists():
            history_meta = self._load_history_meta()

        # Always scan the full projects dir as the source of truth
        sessions: list[Session] = []
        if _PROJECTS_DIR.exists():
            sessions = self._scan_projects_dir_with_meta(history_meta)
        elif history_meta:
            # No projects dir — fall back to history-only (edge case)
            sessions = self._from_history()

        return sessions

    def _load_history_meta(self) -> dict[str, dict]:
        """Return {session_id: entry} from history.jsonl + Desktop App JSON files."""
        meta: dict[str, dict] = {}

        # CLI history
        if _HISTORY_PATH.exists():
            with _HISTORY_PATH.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    sid = entry.get("sessionId") or entry.get("session_id")
                    if sid and sid not in meta:
                        meta[sid] = entry

        # Desktop App session JSON files (macOS only)
        # Structure: <_DESKTOP_SESSIONS_DIR>/<profile-uuid>/<session-uuid>/local_<cli-id>.json
        if _DESKTOP_SESSIONS_DIR.exists():
            for profile_dir in _DESKTOP_SESSIONS_DIR.iterdir():
                if not profile_dir.is_dir():
                    continue
                for session_dir in profile_dir.iterdir():
                    if not session_dir.is_dir():
                        continue
                    for json_file in session_dir.glob("local_*.json"):
                        try:
                            with json_file.open() as f:
                                entry = json.load(f)
                        except (json.JSONDecodeError, OSError):
                            continue
                        cli_id = entry.get("cliSessionId")
                        if cli_id and cli_id not in meta:
                            # Normalize to the same shape history.jsonl uses
                            ts_ms = entry.get("createdAt") or entry.get("lastActivityAt")
                            ts_str = None
                            if ts_ms:
                                try:
                                    ts_str = datetime.fromtimestamp(
                                        ts_ms / 1000, tz=timezone.utc
                                    ).isoformat()
                                except (TypeError, ValueError):
                                    pass
                            meta[cli_id] = {
                                "sessionId": cli_id,
                                "display": entry.get("title"),
                                "project": entry.get("cwd") or entry.get("originCwd"),
                                "timestamp": ts_str,
                                "_source": "desktop",
                            }

        return meta

    def _scan_projects_dir_with_meta(self, history_meta: dict[str, dict]) -> list[Session]:
        """Scan all top-level JSONL files in projects dir, enriching with history metadata."""
        sessions: list[Session] = []
        for project_dir in sorted(_PROJECTS_DIR.iterdir()):
            if not project_dir.is_dir():
                continue
            cwd = _decode_path(project_dir.name)
            for jsonl_file in sorted(project_dir.glob("*.jsonl")):
                session_id = jsonl_file.stem
                meta = history_meta.get(session_id, {})

                # Prefer history metadata for title/cwd/timestamp; fall back to file mtime
                display = meta.get("display") or meta.get("title") or None
                project = meta.get("project") or meta.get("cwd") or cwd

                ts_str = meta.get("timestamp")
                if ts_str:
                    try:
                        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    except (ValueError, AttributeError):
                        dt = None
                else:
                    dt = None

                if dt is None:
                    mtime = jsonl_file.stat().st_mtime
                    dt = datetime.fromtimestamp(mtime, tz=timezone.utc)

                # Calculate token count from session content
                token_count = self._count_session_tokens(jsonl_file)

                sessions.append(
                    Session(
                        id=session_id,
                        tool=self.tool_name,
                        title=display,
                        cwd=project or None,
                        created_at=dt,
                        updated_at=dt,
                        raw_path=str(jsonl_file),
                        status="unknown",
                        token_count=token_count,
                    )
                )
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

    def _count_session_tokens(self, jsonl_path: Path) -> int:
        """Count total tokens in a session JSONL file."""
        total = 0
        try:
            with jsonl_path.open() as f:
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
                    content_raw = msg_data.get("content", "")

                    # Skip tool-result-only arrays (not actual user/assistant content)
                    if isinstance(content_raw, list):
                        if not any(block.get("type") == "text" for block in content_raw if isinstance(block, dict)):
                            continue

                    content = _parse_content(content_raw)
                    if content:
                        total += _count_tokens(content)
        except Exception:
            pass
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
