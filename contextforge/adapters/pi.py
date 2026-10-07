"""Adapter for Pi coding agent (pi CLI) sessions.

Pi stores its sessions as JSONL transcript files under
``~/.pi/agent/sessions/<escaped-cwd>/<timestamp>_<session-id>.jsonl``.

Sub-agent sessions (launched via the pi-subagents extension) live in a nested
directory named after the *parent* session file stem::

    ~/.pi/agent/sessions/
      <escaped-cwd>/
        <timestamp>_<parent-session-id>.jsonl        <- main session
        <timestamp>_<parent-session-id>/             <- parent session stem dir
          <agent-run-uuid>/
            run-0/session.jsonl                      <- sub-agent transcript
            run-1/session.jsonl

JSONL entry types: ``session`` (header), ``session_info`` (name / parent link),
``model_change``, ``thinking_level_change``, ``custom``, and ``message`` (the
actual transcript).  Message roles are ``system``, ``user``, ``assistant`` and
``toolResult``.  Assistant message content is a list of blocks with types
``text``, ``thinking`` and ``toolCall``; tool results are separate
``toolResult`` messages attributed back to the assistant turn that issued them.
"""
from __future__ import annotations

import json
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path

from contextforge.adapters.base import ToolAdapter
from contextforge.models.session import Message, Session
from contextforge.utils.tokens import count_tokens

_SESSIONS_ROOT = Path.home() / ".pi" / "agent" / "sessions"

_TITLE_SNIPPET_LEN = 80
# <timestamp>_<session-uuid>.jsonl — the session id is the last "_"-separated part
_FILENAME_ID_RE = re.compile(r"_(?P<id>[0-9a-fA-F-]{36})$")


def _parse_iso(ts) -> datetime | None:
    """Parse an ISO-8601 timestamp string (with an optional trailing 'Z')."""
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _text_blocks(content) -> list[str]:
    """Extract the text of every ``text`` block from a message content field."""
    parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if isinstance(text, list):  # some builders emit paragraph lists
                    text = "\n".join(str(t) for t in text)
                parts.append(str(text))
    elif isinstance(content, str):
        parts.append(content)
    return parts


def _compute_message_tokens(msg: Message) -> int:
    """Count tokens across all content in a message: text + tool_call inputs + tool_result outputs."""
    total = count_tokens(msg.content)
    for tc in msg.tool_calls:
        total += count_tokens(tc.get("input", ""))
    for tr in msg.tool_results:
        total += count_tokens(tr.get("output", ""))
    return total


class PiAdapter(ToolAdapter):
    tool_name = "pi"
    default_paths = [_SESSIONS_ROOT]

    # ------------------------------------------------------------------
    # Session file index
    # ------------------------------------------------------------------

    def _iter_session_files(self) -> list[tuple[Path, Path | None]]:
        """Yield (session_file, parent_session_file) pairs for all known sessions.

        Main sessions are top-level ``*.jsonl`` files; sub-agent sessions are
        ``session.jsonl`` files nested under a directory named after the parent
        session's file stem.  ``parent_session_file`` is the resolved parent
        ``*.jsonl`` file when it exists.
        """
        if not _SESSIONS_ROOT.exists():
            return []
        results: list[tuple[Path, Path | None]] = []
        for project_dir in sorted(_SESSIONS_ROOT.iterdir()):
            if not project_dir.is_dir():
                continue
            for jsonl_file in sorted(project_dir.glob("*.jsonl")):
                results.append((jsonl_file, None))
            for stem_dir in project_dir.iterdir():
                if not stem_dir.is_dir() or stem_dir.name == "subagent-artifacts":
                    continue
                parent_file = project_dir / (stem_dir.name + ".jsonl")
                if not parent_file.exists():
                    parent_file = None
                for sess_file in sorted(stem_dir.rglob("session.jsonl")):
                    if "subagent-artifacts" in sess_file.parts:
                        continue
                    results.append((sess_file, parent_file))
        return results

    def _session_path(self, session_id: str) -> Path | None:
        """Resolve a session id (or file stem) to its JSONL file."""
        # Header-id match first: all sub-agent transcripts share the stem "session"
        for path, _parent in self._iter_session_files():
            header = self._read_header(path)
            if header and header.get("id") == session_id:
                return path
        # Fall back to main-session file stems
        for path, _parent in self._iter_session_files():
            if path.stem == session_id:
                return path
        return None

    def _read_header(self, path: Path) -> dict | None:
        """Read the ``session`` header entry (first line) of a transcript file."""
        try:
            with path.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        return None
                    return entry if entry.get("type") == "session" else None
        except OSError:
            return None
        return None

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover_sessions(self) -> list[Session]:
        """Discover main Pi sessions and their sub-agent sessions."""
        sessions: list[Session] = []
        for path, parent_path in self._iter_session_files():
            sessions.append(self._session_from_file(path, parent_path))
        return sessions

    def _session_from_file(self, path: Path, parent_path: Path | None) -> Session:
        header = self._read_header(path)
        session_id = self._file_session_id(path, header)
        header = header or {}
        cwd = header.get("cwd")
        created = _parse_iso(header.get("timestamp"))
        if created is None:
            created = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)

        updated = self._last_message_time(path) or created
        # NB: pass the header id, not the path — all sub-agent transcripts share
        # the stem "session", so stem-based lookup would collide.
        token_count = self._count_session_tokens(str(session_id))

        if parent_path is not None:
            # Sub-agent session: derive agent name and parent linkage
            parent_id = (self._read_header(parent_path) or {}).get("id") or self._id_from_filename(parent_path)
            name = self._subagent_name(path)
            return Session(
                id=str(session_id),
                tool=self.tool_name,
                title=name,
                cwd=cwd,
                created_at=created,
                updated_at=updated,
                status="unknown",
                token_count=token_count,
                raw_path=str(path),
                tags=["subagent", f"parent:{parent_id}"],
            )

        return Session(
            id=str(session_id),
            tool=self.tool_name,
            title=None,  # Pi stores no native title; the summarizer can add one
            cwd=cwd,
            created_at=created,
            updated_at=updated,
            status="unknown",
            token_count=token_count,
            raw_path=str(path),
        )

    @staticmethod
    def _id_from_filename(path: Path) -> str:
        stem = path.stem
        m = _FILENAME_ID_RE.search(stem)
        return m.group("id") if m else stem

    @staticmethod
    def _file_session_id(path: Path, header: dict | None) -> str:
        """Best-effort stable session id: header id first, then filename/paths.

        All sub-agent transcripts share the name ``session.jsonl``, so their
        fallback id is derived from the enclosing run directories instead of
        the (colliding) file stem.
        """
        header_id = (header or {}).get("id")
        if isinstance(header_id, str) and header_id.strip():
            return header_id
        if path.name == "session.jsonl":
            # .../<parent-stem>/<agent-run-uuid>/run-N/session.jsonl
            run_dir = path.parent.name
            agent_dir = path.parent.parent.name
            return f"{agent_dir}@{run_dir}"
        return PiAdapter._id_from_filename(path)

    def _last_message_time(self, path: Path) -> datetime | None:
        """Read the trailing chunk of the file for the most recent message timestamp."""
        try:
            with path.open("rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 8192))
                tail = f.read().decode("utf-8", "replace")
            last_ts: datetime | None = None
            for line in tail.splitlines()[1:]:  # first partial line may be garbage
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") == "custom":
                    continue
                ts = entry.get("timestamp") or (entry.get("message") or {}).get("timestamp")
                parsed = _parse_iso(ts) or self._parse_millis(ts)
                if parsed is not None:
                    last_ts = parsed
            return last_ts
        except OSError:
            return None

    @staticmethod
    def _parse_millis(ts) -> datetime | None:
        """Parse epoch-millisecond timestamps (toolResult messages use them)."""
        if isinstance(ts, (int, float)) and ts > 0:
            try:
                return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
        return None

    def _subagent_name(self, path: Path) -> str | None:
        """Extract a human-readable agent name for a sub-agent session.

        Pi's ``session_info.name`` looks like
        ``subagent-<agent-name>-<agent-run-uuid>-<run-index>``.  The parent
        ``session_info.parentId`` is a short id (not the full session id), so
        parent linkage is derived from the directory layout instead.
        """
        raw = self._read_session_info_name(path)
        if not raw or not raw.startswith("subagent-"):
            return raw[:_TITLE_SNIPPET_LEN] if raw else None
        # Strip "subagent-" and the trailing "-<uuid>-<index>" suffix
        name = raw[len("subagent-"):]
        name = re.sub(r"-[0-9a-fA-F-]{36}-\d+$", "", name)
        name = re.sub(r"-\d+$", "", name)
        return name[:_TITLE_SNIPPET_LEN] or "unknown"

    def _read_session_info_name(self, path: Path) -> str | None:
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
                    if entry.get("type") == "custom":
                        continue
                    if entry.get("type") == "session_info":
                        return entry.get("name")
        except OSError:
            return None
        return None

    # ------------------------------------------------------------------
    # Message loading
    # ------------------------------------------------------------------

    @staticmethod
    def _read_message_entries(path: Path) -> list[dict]:
        """Parse all ``message`` entries, tolerating malformed/truncated lines."""
        entries: list[dict] = []
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue  # truncated tail or corrupted record
                if isinstance(entry, dict) and entry.get("type") == "message":
                    entries.append(entry)
        return entries

    @staticmethod
    def _active_branch(entries: list[dict]) -> list[dict]:
        """Reconstruct the active conversation path from the entry tree.

        Pi sessions are trees: steering/forking creates sibling branches under
        the same parent.  The active view is the chain of ancestors ending at
        the *last* written message entry.  Falls back to the full linear order
        when ids/parent ids are missing or duplicated.
        """
        entry_by_id: dict[str, dict] = {}
        for entry in entries:
            eid = entry.get("id")
            if eid is None or eid in entry_by_id:
                return entries  # cannot trace reliably; keep linear order
            entry_by_id[eid] = entry

        chain: list[dict] = []
        seen: set = set()
        current: dict | None = entries[-1]
        while current is not None and current["id"] not in seen:
            chain.append(current)
            seen.add(current["id"])
            parent_id = current.get("parentId")
            current = entry_by_id.get(parent_id) if parent_id is not None else None
        chain.reverse()
        return chain  # sibling branches not on the leaf chain are inactive

    def load_messages(self, session_id: str) -> list[Message]:
        path = self._session_path(session_id)
        if path is None or not path.exists():
            return []

        entries = self._read_message_entries(path)
        if not entries:
            return []
        chain = self._active_branch(entries)

        messages: list[Message] = []
        assistant_by_call_id: dict[str, Message] = {}

        for entry in chain:
            msg_data = entry.get("message", {})
            role = msg_data.get("role")
            ts = _parse_iso(entry.get("timestamp")) or self._parse_millis(
                entry.get("timestamp") or msg_data.get("timestamp")
            )

            if role == "user":
                content = "\n".join(p for p in _text_blocks(msg_data.get("content")) if p)
                if not content:
                    continue
                msg = Message(role="user", content=content, timestamp=ts)
                msg.token_count = count_tokens(content)
                messages.append(msg)

            elif role == "assistant":
                content = "\n".join(p for p in _text_blocks(msg_data.get("content")) if p)
                tool_calls = []
                raw_content = msg_data.get("content")
                if isinstance(raw_content, list):
                    for block in raw_content:
                        if not isinstance(block, dict) or block.get("type") != "toolCall":
                            continue
                        try:
                            input_str = json.dumps(block.get("arguments", {}))
                        except (TypeError, ValueError):
                            input_str = ""
                        block_id = block.get("id")
                        call = {"name": block.get("name", "?"), "input": input_str}
                        if block_id:
                            call["id"] = block_id
                        tool_calls.append(call)
                if not content and not tool_calls:
                    continue
                msg = Message(role="assistant", content=content, timestamp=ts, tool_calls=tool_calls)
                msg.token_count = _compute_message_tokens(msg)
                for call in tool_calls:
                    if "id" in call:
                        assistant_by_call_id[call["id"]] = msg
                messages.append(msg)

            elif role == "toolResult":
                # Attribute results to the assistant turn that issued the call,
                # matched by toolCallId; fall back to the latest assistant turn.
                last = messages[-1] if messages else None
                if last is not None and last.role != "assistant":
                    last = None
                call_id = msg_data.get("toolCallId")
                if call_id:
                    last = assistant_by_call_id.get(call_id, last)
                if last is None:
                    continue
                for text in _text_blocks(msg_data.get("content")):
                    if text:
                        last.tool_results.append(
                            {"tool": msg_data.get("toolName", "?"), "output": text}
                        )
                last.token_count = _compute_message_tokens(last)

            elif role == "system":
                sections = msg_data.get("sections")
                preamble = sections.get("preamble", "") if isinstance(sections, dict) else ""
                content = preamble or str(msg_data.get("content") or "")
                if not content:
                    continue
                msg = Message(role="system", content=content, timestamp=ts)
                msg.token_count = count_tokens(content)
                messages.append(msg)

        return messages

    # ------------------------------------------------------------------
    # Injection
    # ------------------------------------------------------------------

    def build_inject_command(
        self,
        context: str,
        target_session_id: str | None = None,
        cwd: str | None = None,
        method: str = "system_prompt",
    ) -> str:
        safe_ctx = shlex.quote(context)
        if method == "resume" and target_session_id:
            return f"pi --session {target_session_id} --append-system-prompt {safe_ctx}"
        if method == "new_with_prompt":
            cmd = f"pi -p {safe_ctx}"
            if cwd:
                cmd = f"cd {shlex.quote(cwd)} && {cmd}"
            return cmd
        # Default: fresh session seeded with the context as its system prompt
        cmd = f"pi --system-prompt {safe_ctx}"
        if cwd:
            cmd = f"cd {shlex.quote(cwd)} && {cmd}"
        return cmd