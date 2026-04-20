"""Adapter for OpenAI Codex CLI sessions."""
from __future__ import annotations

import json
import shlex
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import tiktoken

from contextforge.adapters.base import ToolAdapter
from contextforge.models.session import Message, Session

_CODEX_DB = Path.home() / ".codex" / "state_5.sqlite"

# Timestamps above this threshold are milliseconds; below are seconds.
# 1e11 ms ≈ year 1973, so any value > 1e11 must be ms-since-epoch.
_MS_THRESHOLD = 100_000_000_000


def _ts_to_dt(ts) -> datetime:
    """Convert a Codex timestamp (seconds or milliseconds) to a UTC datetime."""
    if ts is None:
        return datetime.now(timezone.utc)
    ts = int(ts)
    if ts > _MS_THRESHOLD:
        ts = ts // 1000  # milliseconds → seconds
    return datetime.fromtimestamp(ts, tz=timezone.utc)
_CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
_SESSION_INDEX = Path.home() / ".codex" / "session_index.jsonl"


def _count_tokens(text: str) -> int:
    """Count tokens in text using tiktoken for Claude models."""
    try:
        enc = tiktoken.encoding_for_model("claude-3-5-sonnet-20241022")
        return len(enc.encode(text))
    except Exception:
        # Fallback: rough estimate (~4 chars per token)
        return len(text) // 4


class CodexAdapter(ToolAdapter):
    tool_name = "codex"
    default_paths = [_CODEX_DB, _CODEX_SESSIONS_DIR]

    def discover_sessions(self) -> list[Session]:
        if _CODEX_DB.exists():
            return self._from_sqlite()
        if _SESSION_INDEX.exists():
            return self._from_index()
        return []

    def _from_sqlite(self) -> list[Session]:
        sessions: list[Session] = []
        try:
            conn = sqlite3.connect(str(_CODEX_DB))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, title, cwd, tokens_used, first_user_message,
                       created_at, updated_at, rollout_path
                FROM threads
                ORDER BY updated_at DESC
                """
            )
            rows = cur.fetchall()
            conn.close()
        except (sqlite3.Error, Exception):
            return []

        for row in rows:
            try:
                created = _ts_to_dt(row["created_at"])
                updated = _ts_to_dt(row["updated_at"])
            except (TypeError, ValueError):
                now = datetime.now(timezone.utc)
                created = updated = now

            sessions.append(
                Session(
                    id=str(row["id"]),
                    tool=self.tool_name,
                    title=row["title"] or None,
                    cwd=row["cwd"] or None,
                    created_at=created,
                    updated_at=updated,
                    token_count=row["tokens_used"],
                    raw_path=row["rollout_path"] or None,
                    status="unknown",
                )
            )
        return sessions

    def _from_index(self) -> list[Session]:
        sessions: list[Session] = []
        with _SESSION_INDEX.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = entry.get("timestamp") or entry.get("created_at")
                try:
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    dt = datetime.now(timezone.utc)

                sessions.append(
                    Session(
                        id=entry.get("id", ""),
                        tool=self.tool_name,
                        title=entry.get("title"),
                        cwd=entry.get("cwd"),
                        created_at=dt,
                        updated_at=dt,
                        status="unknown",
                    )
                )
        return sessions

    def get_rollup_summary(self, session_id: str) -> str | None:
        """Return Codex's pre-computed rollup summary if available (zero LLM cost)."""
        if not _CODEX_DB.exists():
            return None
        try:
            conn = sqlite3.connect(str(_CODEX_DB))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            # Check if stage1_outputs table exists
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='stage1_outputs'"
            )
            if not cur.fetchone():
                conn.close()
                return None
            cur.execute(
                "SELECT rollout_summary FROM stage1_outputs WHERE thread_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (session_id,),
            )
            row = cur.fetchone()
            conn.close()
            if row and row["rollout_summary"]:
                return str(row["rollout_summary"]).strip() or None
        except sqlite3.Error:
            pass
        return None

    def load_messages(self, session_id: str) -> list[Message]:
        # Try to get rollout path from SQLite
        rollout_path: Path | None = None
        if _CODEX_DB.exists():
            try:
                conn = sqlite3.connect(str(_CODEX_DB))
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("SELECT rollout_path FROM threads WHERE id = ?", (session_id,))
                row = cur.fetchone()
                conn.close()
                if row and row["rollout_path"]:
                    rollout_path = Path(row["rollout_path"])
            except sqlite3.Error:
                pass

        if rollout_path and rollout_path.exists():
            return self._parse_rollout(rollout_path)

        # Fallback: scan sessions directory
        for year_dir in _CODEX_SESSIONS_DIR.glob("*"):
            for month_dir in year_dir.glob("*"):
                session_dir = month_dir / session_id
                if session_dir.exists():
                    for jsonl in sorted(session_dir.glob("rollout-*.jsonl")):
                        return self._parse_rollout(jsonl)

        return []

    def _parse_rollout(self, path: Path) -> list[Message]:
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
                payload = entry.get("payload", {})

                if entry_type == "event_msg":
                    msg_type = payload.get("type", "")
                    # user_message / agent_message are the clean human-facing turns.
                    # response_item entries are skipped — they contain system context
                    # injections (AGENTS.md, environment_context) and duplicate content.
                    if msg_type == "user_message":
                        text = payload.get("message", "")
                        if text:
                            text_str = str(text)
                            messages.append(Message(
                                role="user",
                                content=text_str,
                                token_count=_count_tokens(text_str),
                            ))
                    elif msg_type == "agent_message":
                        text = payload.get("message", "")
                        if text:
                            text_str = str(text)
                            messages.append(Message(
                                role="assistant",
                                content=text_str,
                                token_count=_count_tokens(text_str),
                            ))

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
            return f"codex resume {target_session_id} {safe_ctx}"
        if method == "fork" and target_session_id:
            return f"codex fork {target_session_id} {safe_ctx}"
        # New session
        cmd = f"codex exec {safe_ctx}"
        if cwd:
            cmd = f"cd {shlex.quote(cwd)} && {cmd}"
        return cmd
