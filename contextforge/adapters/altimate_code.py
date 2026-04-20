"""Adapter for altimate-code (opencode) sessions."""
from __future__ import annotations

import json
import shlex
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import tiktoken

from contextforge.adapters.base import ToolAdapter
from contextforge.models.session import Message, Session

_OPENCODE_DB = Path.home() / ".local" / "share" / "altimate-code" / "opencode.db"


def _count_tokens(text: str) -> int:
    """Count tokens in text using tiktoken for Claude models."""
    try:
        enc = tiktoken.encoding_for_model("claude-3-5-sonnet-20241022")
        return len(enc.encode(text))
    except Exception:
        # Fallback: rough estimate (~4 chars per token)
        return len(text) // 4


class AltimateCodeAdapter(ToolAdapter):
    tool_name = "altimate_code"
    default_paths = [_OPENCODE_DB]

    def discover_sessions(self) -> list[Session]:
        if not _OPENCODE_DB.exists():
            return []

        sessions: list[Session] = []
        try:
            conn = sqlite3.connect(str(_OPENCODE_DB))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, title, directory, time_created, time_updated
                FROM session
                ORDER BY time_updated DESC
                """
            )
            rows = cur.fetchall()
            conn.close()
        except sqlite3.Error:
            return []

        for row in rows:
            try:
                created = datetime.fromtimestamp(row["time_created"] / 1000, tz=timezone.utc)
                updated = datetime.fromtimestamp(row["time_updated"] / 1000, tz=timezone.utc)
            except (TypeError, ValueError):
                now = datetime.now(timezone.utc)
                created = updated = now

            # Calculate token count from session messages
            session_id = str(row["id"])
            token_count = self._count_session_tokens(session_id)

            sessions.append(
                Session(
                    id=session_id,
                    tool=self.tool_name,
                    title=row["title"] or None,
                    cwd=row["directory"] or None,
                    created_at=created,
                    updated_at=updated,
                    raw_path=str(_OPENCODE_DB),
                    status="unknown",
                    token_count=token_count,
                )
            )

        return sessions

    def load_messages(self, session_id: str) -> list[Message]:
        if not _OPENCODE_DB.exists():
            return []

        try:
            conn = sqlite3.connect(str(_OPENCODE_DB))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            # Join message + part to get full content
            # Note: altimate-code stores data as JSON in 'data' column
            cur.execute(
                """
                SELECT
                    m.id,
                    json_extract(m.data, '$.role') as role,
                    m.time_created as ts,
                    json_extract(p.data, '$.type') as part_type,
                    json_extract(p.data, '$.text') as content
                FROM message m
                LEFT JOIN part p ON p.message_id = m.id
                WHERE m.session_id = ?
                ORDER BY m.time_created ASC, p.id ASC
                """,
                (session_id,),
            )
            rows = cur.fetchall()
            conn.close()
        except sqlite3.Error:
            return []

        messages: list[Message] = []
        current_msg_id: str | None = None
        current_role: str | None = None
        current_parts: list[str] = []
        current_ts: datetime | None = None

        def flush():
            nonlocal current_msg_id, current_role, current_parts, current_ts
            if current_role and current_parts:
                content = "\n".join(p for p in current_parts if p)
                if content:
                    messages.append(
                        Message(
                            role=current_role,
                            content=content,
                            timestamp=current_ts,
                            token_count=_count_tokens(content),
                        )
                    )
            current_msg_id = None
            current_role = None
            current_parts = []
            current_ts = None

        for row in rows:
            msg_id = row["id"]
            role = row["role"]
            if role not in ("user", "assistant"):
                continue

            # Flush when message ID changes (new message in the message table)
            if msg_id != current_msg_id:
                flush()
                current_msg_id = msg_id
                current_role = role
                try:
                    current_ts = datetime.fromtimestamp(row["ts"] / 1000, tz=timezone.utc)
                except (TypeError, ValueError):
                    current_ts = None

            part_type = row["part_type"] or ""
            content = row["content"] or ""

            if part_type in ("text", "reasoning") and content:
                if isinstance(content, str):
                    try:
                        parsed = json.loads(content)
                        if isinstance(parsed, dict):
                            content = parsed.get("text", str(parsed))
                    except (json.JSONDecodeError, TypeError):
                        pass
                current_parts.append(str(content).strip())

        flush()
        return messages

    def _count_session_tokens(self, session_id: str) -> int:
        """Count total tokens in a session."""
        messages = self.load_messages(session_id)
        total = 0
        for msg in messages:
            total += msg.token_count or _count_tokens(msg.content)
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
            return f"altimate-code run -s {target_session_id} {safe_ctx}"
        if method == "fork" and target_session_id:
            return f"altimate-code run --fork -s {target_session_id} {safe_ctx}"
        # New session
        cmd = f"altimate-code run {safe_ctx}"
        if cwd:
            cmd = f"cd {shlex.quote(cwd)} && {cmd}"
        return cmd
