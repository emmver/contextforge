"""Adapter for Google Gemini CLI sessions."""
from __future__ import annotations

import json
import shlex
from datetime import datetime, timezone
from pathlib import Path

from contextforge.adapters.base import ToolAdapter
from contextforge.models.session import Message, Session

_GEMINI_HISTORY_PATH = Path.home() / ".gemini" / "history.jsonl"
_GEMINI_SESSIONS_DIR = Path.home() / ".gemini" / "sessions"


class GeminiAdapter(ToolAdapter):
    tool_name = "gemini"
    default_paths = [_GEMINI_HISTORY_PATH, _GEMINI_SESSIONS_DIR]

    def discover_sessions(self) -> list[Session]:
        """Discover Gemini sessions from history and sessions directory."""
        sessions: list[Session] = []

        # Try to load from sessions directory first
        if _GEMINI_SESSIONS_DIR.exists():
            sessions = self._scan_sessions_dir()

        # Fall back to history.jsonl if no sessions directory
        if not sessions and _GEMINI_HISTORY_PATH.exists():
            sessions = self._from_history()

        return sessions

    def _scan_sessions_dir(self) -> list[Session]:
        """Scan the Gemini sessions directory for session files."""
        sessions: list[Session] = []

        if not _GEMINI_SESSIONS_DIR.exists():
            return sessions

        for session_file in sorted(_GEMINI_SESSIONS_DIR.glob("*.jsonl")):
            session_id = session_file.stem
            mtime = session_file.stat().st_mtime
            dt = datetime.fromtimestamp(mtime, tz=timezone.utc)

            # Try to extract title from first few lines
            title = None
            try:
                with session_file.open() as f:
                    for line in f:
                        try:
                            entry = json.loads(line)
                            if entry.get("type") == "user" and not title:
                                content = entry.get("message", {}).get("content", "")
                                if isinstance(content, str):
                                    title = content[:100]  # First 100 chars as title
                                break
                        except json.JSONDecodeError:
                            pass
            except Exception:
                pass

            sessions.append(
                Session(
                    id=session_id,
                    tool=self.tool_name,
                    title=title,
                    cwd=None,
                    created_at=dt,
                    updated_at=dt,
                    raw_path=str(session_file),
                    status="unknown",
                )
            )

        return sessions

    def _from_history(self) -> list[Session]:
        """Load sessions from history.jsonl file."""
        sessions: list[Session] = []
        seen: set[str] = set()

        if not _GEMINI_HISTORY_PATH.exists():
            return sessions

        with _GEMINI_HISTORY_PATH.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                session_id = entry.get("session_id") or entry.get("sessionId")
                if not session_id or session_id in seen:
                    continue
                seen.add(session_id)

                title = entry.get("title") or entry.get("display")
                ts = entry.get("timestamp")

                try:
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                except (ValueError, AttributeError, TypeError):
                    dt = datetime.now(timezone.utc)

                sessions.append(
                    Session(
                        id=session_id,
                        tool=self.tool_name,
                        title=title,
                        cwd=None,
                        created_at=dt,
                        updated_at=dt,
                        raw_path=None,
                        status="unknown",
                    )
                )

        return sessions

    def load_messages(self, session_id: str) -> list[Message]:
        """Load messages for a given session ID."""
        # Try to find session file
        if _GEMINI_SESSIONS_DIR.exists():
            session_file = _GEMINI_SESSIONS_DIR / f"{session_id}.jsonl"
            if session_file.exists():
                return self._parse_session_file(session_file)

        return []

    def _parse_session_file(self, path: Path) -> list[Message]:
        """Parse a Gemini session JSONL file."""
        messages: list[Message] = []

        try:
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
                    content = msg_data.get("content", "")

                    if not content:
                        continue

                    # Handle both string and list content formats
                    if isinstance(content, list):
                        content = " ".join(str(item) for item in content if item)

                    ts_str = entry.get("timestamp")
                    ts = None
                    if ts_str:
                        try:
                            ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                        except (ValueError, AttributeError, TypeError):
                            pass

                    messages.append(
                        Message(
                            role="user" if entry_type == "user" else "assistant",
                            content=str(content),
                            timestamp=ts,
                        )
                    )
        except Exception:
            pass

        return messages

    def build_inject_command(
        self,
        context: str,
        target_session_id: str | None = None,
        cwd: str | None = None,
        method: str = "system_prompt",
    ) -> str:
        """Build a shell command to start/resume a Gemini session with context."""
        safe_ctx = shlex.quote(context)

        if method == "resume" and target_session_id:
            return f"gemini resume {target_session_id} {safe_ctx}"

        if method == "fork" and target_session_id:
            return f"gemini fork {target_session_id} {safe_ctx}"

        # Default: new session
        cmd = f"gemini exec {safe_ctx}"
        if cwd:
            cmd = f"cd {shlex.quote(cwd)} && {cmd}"
        return cmd
