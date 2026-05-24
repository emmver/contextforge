"""Adapter for Google Antigravity CLI sessions."""
from __future__ import annotations

import json
import shlex
from datetime import datetime, timezone
from pathlib import Path

import tiktoken

from contextforge.adapters.base import ToolAdapter
from contextforge.models.session import Message, Session

_AGY_HISTORY = Path.home() / ".gemini" / "antigravity-cli" / "history.jsonl"
_AGY_CONVERSATIONS = Path.home() / ".gemini" / "antigravity-cli" / "conversations"
_AGY_ALT_HISTORY = Path.home() / ".gemini" / "antigravity" / "history.jsonl"
_AGY_ALT_CONVERSATIONS = Path.home() / ".gemini" / "antigravity" / "conversations"


def _count_tokens(text: str) -> int:
    """Count tokens in text using tiktoken for Claude models."""
    try:
        enc = tiktoken.encoding_for_model("claude-3-5-sonnet-20241022")
        return len(enc.encode(text))
    except Exception:
        # Fallback: rough estimate (~4 chars per token)
        return len(text) // 4


def _ms_to_dt(ts: int) -> datetime:
    """Convert a millisecond timestamp to UTC datetime."""
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)


class AntigravityAdapter(ToolAdapter):
    tool_name = "antigravity"
    default_paths = [_AGY_HISTORY, _AGY_ALT_HISTORY]

    def _history_path(self) -> Path | None:
        """Return the first existing history.jsonl path."""
        for p in (_AGY_HISTORY, _AGY_ALT_HISTORY):
            if p.exists():
                return p
        return None

    def _conversations_dir(self) -> Path | None:
        """Return the conversations directory that matches the history path."""
        history = self._history_path()
        if history is not None:
            conversations = history.parent / "conversations"
            if conversations.exists():
                return conversations
        for p in (_AGY_CONVERSATIONS, _AGY_ALT_CONVERSATIONS):
            if p.exists():
                return p
        return None

    def discover_sessions(self) -> list[Session]:
        """Discover Antigravity sessions from history.jsonl.

        history.jsonl contains one JSON object per user prompt.  Prompts that
        belong to the same saved conversation share a ``conversationId``; we
        group those into a single ``Session``.
        """
        sessions: list[Session] = []
        history_path = self._history_path()
        if history_path is None:
            return sessions

        # conversationId -> list of history entries
        groups: dict[str, list[dict]] = {}

        with history_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                conversation_id = entry.get("conversationId")
                if not conversation_id:
                    # Skip un-persisted one-off prompts.
                    continue
                groups.setdefault(conversation_id, []).append(entry)

        for conversation_id, entries in groups.items():
            if not entries:
                continue
            # Use earliest timestamp as created_at, latest as updated_at.
            timestamps = [e.get("timestamp", 0) for e in entries if e.get("timestamp")]
            created_ts = min(timestamps) if timestamps else 0
            updated_ts = max(timestamps) if timestamps else 0

            created_at = _ms_to_dt(created_ts) if created_ts else datetime.now(timezone.utc)
            updated_at = _ms_to_dt(updated_ts) if updated_ts else created_at

            # Title from the first entry's display prompt.
            first_display = entries[0].get("display", "")
            title = first_display[:200] if first_display else None
            cwd = entries[0].get("workspace") or None

            sessions.append(
                Session(
                    id=conversation_id,
                    tool=self.tool_name,
                    title=title,
                    cwd=cwd,
                    created_at=created_at,
                    updated_at=updated_at,
                    raw_path=str(history_path),
                    status="unknown",
                )
            )

        return sessions

    def load_messages(self, session_id: str) -> list[Message]:
        """Load messages for a given Antigravity session ID.

        Antigravity stores full conversations as encrypted protobuf (``.pb``)
        files under ``conversations/``.  ContextForge cannot currently parse
        those encrypted files, so this method attempts the following, in order:

        1. Look for a JSON sidecar ``<session_id>.json`` (e.g. from a test
           fixture or future export).
        2. Read the history JSONL entries with this ``conversationId`` and
           synthesise minimal ``Message`` objects from the user prompts.
        """
        conv_dir = self._conversations_dir()
        if conv_dir is not None:
            json_path = conv_dir / f"{session_id}.json"
            if json_path.exists():
                return self._parse_json_conversation(json_path)

        # Fallback: reconstruct user turns from history.jsonl.
        history_path = self._history_path()
        if history_path is None:
            return []

        messages: list[Message] = []
        with history_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("conversationId") != session_id:
                    continue

                text = entry.get("display", "")
                if not text:
                    continue

                ts = entry.get("timestamp")
                timestamp = _ms_to_dt(ts) if ts else None
                messages.append(
                    Message(
                        role="user",
                        content=text,
                        timestamp=timestamp,
                        token_count=_count_tokens(text),
                    )
                )

        return messages

    def _parse_json_conversation(self, path: Path) -> list[Message]:
        """Parse a JSON-sidecar conversation file into Messages.

        Expected shape::

            {
                "messages": [
                    {"role": "user", "content": "...", "timestamp": 1234567890000},
                    {"role": "assistant", "content": "...", "timestamp": 1234567891000}
                ]
            }
        """
        messages: list[Message] = []
        try:
            with path.open() as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return messages

        for msg in data.get("messages", []):
            role = msg.get("role", "")
            if role not in ("user", "assistant"):
                continue
            content = str(msg.get("content", ""))
            if not content:
                continue
            ts = msg.get("timestamp")
            timestamp = _ms_to_dt(ts) if isinstance(ts, int) else None
            messages.append(
                Message(
                    role=role,
                    content=content,
                    timestamp=timestamp,
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
        """Build a shell command to start/resume an Antigravity CLI session.

        ``agy -i <prompt>`` starts an interactive session seeded with the given
        prompt.  ``agy --conversation <id>`` resumes an existing conversation.
        """
        safe_ctx = shlex.quote(context)

        if method == "resume" and target_session_id:
            return f"agy --conversation {target_session_id}"

        # Default: new interactive session with the context as the opening prompt.
        cmd = f"agy -i {safe_ctx}"
        if cwd:
            cmd = f"cd {shlex.quote(cwd)} && {cmd}"
        return cmd
