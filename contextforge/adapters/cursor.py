"""Adapter for Cursor IDE chat/composer sessions.

Cursor stores conversation data in SQLite ("state.vscdb") files under its
user data directory:

  - ``globalStorage/state.vscdb`` — authoritative store for full conversation
    content, shared across all workspaces. Conversations ("composers") live
    in the ``cursorDiskKV`` table as ``composerData:<composerId>`` rows, and
    individual chat turns ("bubbles") as ``bubbleId:<composerId>:<bubbleId>``
    rows.
  - ``workspaceStorage/<hash>/state.vscdb`` + ``workspace.json`` — per-workspace
    metadata used here only to recover the project cwd for a composer (older
    Cursor versions also kept a workspace-local composer index here).

Cursor's storage schema has drifted across versions (schema version is
tracked via a ``_v`` field on composer objects), so this adapter reads
fields defensively with ``.get()`` and falls back to scanning bubbles
directly when the ``fullConversationHeadersOnly`` index is absent.
"""
from __future__ import annotations

import json
import os
import shlex
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import tiktoken

from contextforge.adapters.base import ToolAdapter
from contextforge.models.session import Message, Session


def _cursor_user_dir() -> Path:
    """Return Cursor's per-OS "User" data directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Cursor" / "User"
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "Cursor" / "User"
    return Path.home() / ".config" / "Cursor" / "User"


_CURSOR_USER_DIR = _cursor_user_dir()
_GLOBAL_DB = _CURSOR_USER_DIR / "globalStorage" / "state.vscdb"
_WORKSPACE_STORAGE_DIR = _CURSOR_USER_DIR / "workspaceStorage"


def _count_tokens(text: str) -> int:
    """Count tokens in text using tiktoken for Claude models."""
    try:
        enc = tiktoken.encoding_for_model("claude-3-5-sonnet-20241022")
        return len(enc.encode(text))
    except Exception:
        # Fallback: rough estimate (~4 chars per token)
        return len(text) // 4


def _ms_to_dt(ts) -> datetime | None:
    """Convert a millisecond timestamp to UTC datetime, or None if unusable."""
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _uri_to_path(uri: str) -> str | None:
    """Convert a workspace.json ``file://`` URI to a plain filesystem path."""
    if not uri:
        return None
    try:
        parsed = urlparse(uri)
    except ValueError:
        return None
    if parsed.scheme == "file":
        return unquote(parsed.path)
    return uri


def _load_json_blob(value) -> dict | list | None:
    """Decode a BLOB/TEXT value from an ItemTable/cursorDiskKV row into JSON."""
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


class CursorAdapter(ToolAdapter):
    tool_name = "cursor"
    default_paths = [_GLOBAL_DB]

    def _workspace_paths(self) -> dict[str, str]:
        """Map workspaceStorage hash-dir names to their project path."""
        mapping: dict[str, str] = {}
        if not _WORKSPACE_STORAGE_DIR.exists():
            return mapping
        for ws_dir in _WORKSPACE_STORAGE_DIR.iterdir():
            if not ws_dir.is_dir():
                continue
            meta_file = ws_dir / "workspace.json"
            if not meta_file.exists():
                continue
            try:
                with meta_file.open() as f:
                    meta = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            uri = meta.get("folder") or meta.get("workspace")
            path = _uri_to_path(uri) if isinstance(uri, str) else None
            if path:
                mapping[ws_dir.name] = path
        return mapping

    def _composer_workspace_map(self) -> dict[str, str]:
        """Map composerId -> project cwd via each workspace's local state.vscdb.

        Only populated on Cursor <=2.6, which kept a per-workspace composer
        index (``composer.composerData`` in ``ItemTable``) before the 3.0+
        global-index refactor. Newer versions simply won't match here, and
        callers should treat a missing entry as "cwd unknown".
        """
        composer_to_ws: dict[str, str] = {}
        if not _WORKSPACE_STORAGE_DIR.exists():
            return composer_to_ws
        ws_paths = self._workspace_paths()
        for ws_dir in _WORKSPACE_STORAGE_DIR.iterdir():
            cwd = ws_paths.get(ws_dir.name)
            db_path = ws_dir / "state.vscdb"
            if not cwd or not db_path.exists():
                continue
            try:
                conn = sqlite3.connect(str(db_path))
                cur = conn.cursor()
                cur.execute(
                    "SELECT value FROM ItemTable WHERE key = 'composer.composerData'"
                )
                row = cur.fetchone()
                conn.close()
            except sqlite3.Error:
                continue
            data = _load_json_blob(row[0]) if row else None
            if not isinstance(data, dict):
                continue
            for entry in data.get("allComposers") or []:
                cid = entry.get("composerId") if isinstance(entry, dict) else None
                if cid:
                    composer_to_ws[cid] = cwd
        return composer_to_ws

    def discover_sessions(self) -> list[Session]:
        if not _GLOBAL_DB.exists():
            return []

        try:
            conn = sqlite3.connect(str(_GLOBAL_DB))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT key, value FROM cursorDiskKV WHERE key LIKE 'composerData:%'"
            )
            rows = cur.fetchall()
        except sqlite3.Error:
            return []

        composer_to_ws = self._composer_workspace_map()

        sessions: list[Session] = []
        for row in rows:
            key = row["key"]
            composer_id = key.split(":", 1)[1] if ":" in key else key
            data = _load_json_blob(row["value"])
            if not isinstance(data, dict):
                continue

            headers = data.get("fullConversationHeadersOnly") or []
            created_at = _ms_to_dt(data.get("createdAt"))
            updated_at = _ms_to_dt(data.get("lastUpdatedAt")) or created_at

            title = data.get("name") or None
            if not title:
                title = self._first_bubble_text(cur, composer_id, headers)

            if created_at is None and updated_at is None and not headers and not title:
                # Nothing usable — likely an empty/never-started composer.
                continue

            now = datetime.now(timezone.utc)
            sessions.append(
                Session(
                    id=composer_id,
                    tool=self.tool_name,
                    title=title[:200] if title else None,
                    cwd=composer_to_ws.get(composer_id),
                    created_at=created_at or now,
                    updated_at=updated_at or created_at or now,
                    raw_path=str(_GLOBAL_DB),
                    status="unknown",
                )
            )

        conn.close()
        return sessions

    def _first_bubble_text(
        self, cur: sqlite3.Cursor, composer_id: str, headers: list
    ) -> str | None:
        """Best-effort title fallback: text of the first bubble in the conversation."""
        first_id = None
        for h in headers:
            if isinstance(h, dict) and h.get("bubbleId"):
                first_id = h["bubbleId"]
                break
        if first_id is None:
            return None
        try:
            cur.execute(
                "SELECT value FROM cursorDiskKV WHERE key = ?",
                (f"bubbleId:{composer_id}:{first_id}",),
            )
            row = cur.fetchone()
        except sqlite3.Error:
            return None
        bubble = _load_json_blob(row[0]) if row else None
        if isinstance(bubble, dict):
            text = bubble.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
        return None

    def load_messages(self, session_id: str) -> list[Message]:
        if not _GLOBAL_DB.exists():
            return []

        try:
            conn = sqlite3.connect(str(_GLOBAL_DB))
            cur = conn.cursor()
            cur.execute(
                "SELECT value FROM cursorDiskKV WHERE key = ?",
                (f"composerData:{session_id}",),
            )
            row = cur.fetchone()
            composer = _load_json_blob(row[0]) if row else None
            headers = (
                composer.get("fullConversationHeadersOnly")
                if isinstance(composer, dict)
                else None
            )

            if headers:
                ordered_ids = [
                    h.get("bubbleId")
                    for h in headers
                    if isinstance(h, dict) and h.get("bubbleId")
                ]
                keys = [f"bubbleId:{session_id}:{bid}" for bid in ordered_ids]
                value_by_key: dict[str, object] = {}
                if keys:
                    placeholders = ",".join("?" for _ in keys)
                    cur.execute(
                        f"SELECT key, value FROM cursorDiskKV WHERE key IN ({placeholders})",
                        keys,
                    )
                    value_by_key = dict(cur.fetchall())
                ordered_bubbles = []
                for key in keys:
                    parsed = _load_json_blob(value_by_key.get(key))
                    if isinstance(parsed, dict):
                        ordered_bubbles.append(parsed)
            else:
                # No header index (older/partial data) — scan bubbles directly
                # and fall back to sorting by creation time.
                cur.execute(
                    "SELECT value FROM cursorDiskKV WHERE key LIKE ?",
                    (f"bubbleId:{session_id}:%",),
                )
                ordered_bubbles = sorted(
                    (
                        parsed
                        for (v,) in cur.fetchall()
                        if isinstance(parsed := _load_json_blob(v), dict)
                    ),
                    key=lambda b: b.get("createdAt") or 0,
                )

            conn.close()
        except sqlite3.Error:
            return []

        messages: list[Message] = []
        for bubble in ordered_bubbles:
            text = bubble.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            # type: 1 = user, 2 = assistant.
            role = "user" if bubble.get("type") == 1 else "assistant"
            messages.append(
                Message(
                    role=role,
                    content=text,
                    timestamp=_ms_to_dt(bubble.get("createdAt")),
                    token_count=_count_tokens(text),
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
        """Build a shell command to start/resume a Cursor CLI (``cursor-agent``) session.

        ``cursor-agent -p <prompt>`` runs a one-shot non-interactive turn;
        ``cursor-agent --resume <id> -p <prompt>`` continues an existing chat.
        """
        safe_ctx = shlex.quote(context)

        if method == "resume" and target_session_id:
            cmd = f"cursor-agent --resume {shlex.quote(target_session_id)} -p {safe_ctx}"
        else:
            cmd = f"cursor-agent -p {safe_ctx}"

        if cwd:
            cmd = f"cd {shlex.quote(cwd)} && {cmd}"
        return cmd
