"""Adapter for Google Gemini CLI sessions."""
from __future__ import annotations

import json
import shlex
from datetime import datetime, timezone
from pathlib import Path

import tiktoken

from contextforge.adapters.base import ToolAdapter
from contextforge.models.session import Message, Session

_GEMINI_TMP_BASE = Path.home() / ".gemini" / "tmp"


def _count_tokens(text: str) -> int:
    """Count tokens in text using tiktoken for Claude models."""
    try:
        enc = tiktoken.encoding_for_model("claude-3-5-sonnet-20241022")
        return len(enc.encode(text))
    except Exception:
        # Fallback: rough estimate (~4 chars per token)
        return len(text) // 4


class GeminiAdapter(ToolAdapter):
    tool_name = "gemini"
    default_paths = [_GEMINI_TMP_BASE]

    def discover_sessions(self) -> list[Session]:
        """Discover Gemini sessions from ~/.gemini/tmp/<project_hash>/chats/."""
        sessions: list[Session] = []

        if not _GEMINI_TMP_BASE.exists():
            return sessions

        # Scan all project hash directories
        for project_dir in _GEMINI_TMP_BASE.iterdir():
            if not project_dir.is_dir():
                continue

            chats_dir = project_dir / "chats"
            if not chats_dir.exists():
                continue

            # Scan for session JSON files in format: session-<timestamp>-<hash>.json
            for session_file in sorted(chats_dir.glob("session-*.json")):
                try:
                    with session_file.open() as f:
                        data = json.load(f)
                except (json.JSONDecodeError, OSError):
                    continue

                session_id = data.get("sessionId")
                if not session_id:
                    continue

                # Extract timestamps
                start_time_str = data.get("startTime")
                last_updated_str = data.get("lastUpdated")

                created_at = None
                updated_at = None

                if start_time_str:
                    try:
                        created_at = datetime.fromisoformat(
                            start_time_str.replace("Z", "+00:00")
                        )
                    except (ValueError, AttributeError):
                        pass

                if last_updated_str:
                    try:
                        updated_at = datetime.fromisoformat(
                            last_updated_str.replace("Z", "+00:00")
                        )
                    except (ValueError, AttributeError):
                        pass

                if created_at is None:
                    created_at = datetime.now(timezone.utc)
                if updated_at is None:
                    updated_at = created_at

                # Extract title from first user message
                title = None
                messages = data.get("messages", [])
                for msg in messages:
                    if msg.get("type") == "user":
                        content = msg.get("content", [])
                        if isinstance(content, list) and content:
                            text = content[0].get("text", "")
                            if text:
                                title = text[:100]
                                break

                sessions.append(
                    Session(
                        id=session_id,
                        tool=self.tool_name,
                        title=title,
                        cwd=None,
                        created_at=created_at,
                        updated_at=updated_at,
                        raw_path=str(session_file),
                        status="unknown",
                    )
                )

        return sessions

    def load_messages(self, session_id: str) -> list[Message]:
        """Load messages for a given session ID by scanning all projects."""
        if not _GEMINI_TMP_BASE.exists():
            return []

        # Brute-force search across all projects to find the session
        for project_dir in _GEMINI_TMP_BASE.iterdir():
            if not project_dir.is_dir():
                continue

            chats_dir = project_dir / "chats"
            if not chats_dir.exists():
                continue

            for session_file in chats_dir.glob("session-*.json"):
                try:
                    with session_file.open() as f:
                        data = json.load(f)
                except (json.JSONDecodeError, OSError):
                    continue

                if data.get("sessionId") == session_id:
                    return self._parse_session_data(data)

        return []

    def _parse_session_data(self, data: dict) -> list[Message]:
        """Parse Gemini session JSON object into Message list."""
        messages: list[Message] = []

        for msg in data.get("messages", []):
            msg_type = msg.get("type", "")
            if msg_type not in ("user", "gemini"):
                continue

            # Extract content
            content_raw = msg.get("content", "")
            if isinstance(content_raw, list):
                # User messages: content is list of {text: ...}
                parts = []
                for item in content_raw:
                    if isinstance(item, dict) and "text" in item:
                        parts.append(item["text"])
                content = " ".join(parts)
            else:
                # Gemini messages: content is a string
                content = str(content_raw)

            if not content or not content.strip():
                continue

            ts_str = msg.get("timestamp")
            ts = None
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    pass

            messages.append(
                Message(
                    role="user" if msg_type == "user" else "assistant",
                    content=content,
                    timestamp=ts,
                    token_count=_count_tokens(content),
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
        """Build a shell command to start/resume a Gemini session with context."""
        safe_ctx = shlex.quote(context)

        if method == "resume" and target_session_id:
            return f"gemini /resume {target_session_id}"

        # Default: new session with context injected via system prompt
        cmd = f"gemini {safe_ctx}"
        if cwd:
            cmd = f"cd {shlex.quote(cwd)} && {cmd}"
        return cmd
