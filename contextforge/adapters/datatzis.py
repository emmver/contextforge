"""Adapter for the datatzis harness (per-repo writing harness).

The datatzis harness stores its data inside each repository under
``<repo>/.datatzis/``:

* ``workspaces/<32-hex>.json`` — chat sessions.  A single JSON object with an
  ``id`` (32-hex), ``name``, ``transcript`` (list of ``{role, text}`` rows with
  roles ``user`` / ``assistant`` / ``notice``), the ids of the orchestrator
  tasks it spawned, ``run_id``, ``agent`` spec and an ``updated_at`` stamp.
* ``tasks/<32-hex>.json`` — one orchestrator execution per user prompt.  These
  are the harness's sub-agent units: each records the ``prompt``, ``status``
  (``running`` / ``completed`` / ``failed``), epoch float ``started`` /
  ``ended`` stamps, tool ``events`` (tool name, ``arguments_sha256`` — actual
  arguments are intentionally not stored — and results) and an ``info`` block
  with token usage.  Workspaces link to their tasks via ``data["tasks"]``.
* ``consultations/<id>.json`` and ``runs/<ts-slug>/`` are tied to research
  dossiers rather than chat sessions and are deliberately not tracked here.

Because ``notice`` rows carry stage/system notices, they are mapped to
``system`` messages.  Per-message timestamps do not exist in workspaces (only
a session-level ``updated_at``); tasks carry epoch stamps and are used for the
sub-agent messages.

The harness CLI is interactive-only: ``datatzis chat --resume <id-or-name>``
reopens a session.  It has no non-interactive system-prompt surface, so
``build_inject_command`` returns either a resume command or a plain ``datatzis
chat`` launch.
"""
from __future__ import annotations

import json
import os
import shlex
from datetime import datetime, timezone
from pathlib import Path

from contextforge.adapters.base import ToolAdapter
from contextforge.models.session import Message, Session
from contextforge.utils.tokens import count_tokens

DEFAULT_SEARCH_ROOTS: tuple[Path, ...] = (Path.home() / "Documents" / "Github",)

_TITLE_SNIPPET_LEN = 80


def _search_roots() -> list[Path]:
    """Repository roots scanned for ``*/.datatzis/workspaces`` folders."""
    env = os.environ.get("DATATZIS_SEARCH_ROOTS", "")
    if env.strip():
        return [Path(p).expanduser() for p in env.split(os.pathsep) if p.strip()]
    return list(DEFAULT_SEARCH_ROOTS)


def _parse_iso(ts) -> datetime | None:
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _parse_epoch(ts) -> datetime | None:
    if isinstance(ts, (int, float)) and ts > 0:
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _snippet(text: str, limit: int = _TITLE_SNIPPET_LEN) -> str:
    return " ".join(str(text).split())[:limit]


def _json_text(value) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return ""


def _load_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _compute_message_tokens(msg: Message) -> int:
    total = count_tokens(msg.content)
    for tc in msg.tool_calls:
        total += count_tokens(tc.get("input", ""))
    for tr in msg.tool_results:
        total += count_tokens(tr.get("output", ""))
    return total


class DatatzisAdapter(ToolAdapter):
    tool_name = "datatzis"
    default_paths = list(DEFAULT_SEARCH_ROOTS)

    def is_available(self) -> bool:
        return any(root.exists() for root in _search_roots())

    # ------------------------------------------------------------------
    # Storage layout
    # ------------------------------------------------------------------

    def _iter_workspaces(self) -> list[tuple[Path, Path]]:
        """Yield (workspace_file, repo_root) pairs for every known repo."""
        results: list[tuple[Path, Path]] = []
        seen: set[Path] = set()
        for root in _search_roots():
            if not root.is_dir():
                continue
            candidates: list[Path] = []
            for depth in (1, 2):  # <root>/<repo> and nested installs
                star = "/".join(["*"] * depth)
                candidates.extend(root.glob(f"{star}/.datatzis/workspaces"))
            candidates.append(root / ".datatzis" / "workspaces")
            for ws_dir in sorted(set(candidates)):
                if not ws_dir.is_dir():
                    continue
                repo_root = ws_dir.parent.parent
                for ws_file in sorted(ws_dir.glob("*.json")):
                    if ws_file in seen:
                        continue
                    seen.add(ws_file)
                    results.append((ws_file, repo_root))
        return results

    def _task_file(self, repo_root: Path, task_id: str) -> Path:
        return repo_root / ".datatzis" / "tasks" / f"{task_id}.json"

    def _session_path(self, session_id: str) -> tuple[Path, Path] | None:
        for ws_file, repo_root in self._iter_workspaces():
            if ws_file.stem == session_id:
                return ws_file, repo_root
            data = _load_json(ws_file)
            if data and data.get("id") == session_id:
                return ws_file, repo_root
        for ws_file, repo_root in self._iter_workspaces():
            task_file = self._task_file(repo_root, session_id)
            if task_file.exists():
                return task_file, repo_root
        # Fallback for ids found in the header when workspaces live outside
        # the configured roots is not needed: files are named after ids.
        return None

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover_sessions(self) -> list[Session]:
        """Discover workspace sessions and their orchestrator-task sub-agents."""
        sessions: list[Session] = []
        for ws_file, repo_root in self._iter_workspaces():
            data = _load_json(ws_file)
            if not data or not data.get("id"):
                continue
            sessions.append(self._workspace_session(ws_file, repo_root, data))
            sessions.extend(self._task_sessions(repo_root, data))
        return sessions

    def _workspace_session(self, ws_file: Path, repo_root: Path, data: dict) -> Session:
        session_id = str(data["id"])
        tasks = data.get("tasks") or []
        created: datetime | None = None
        updated = _parse_iso(data.get("updated_at"))
        if updated is None:
            updated = datetime.fromtimestamp(ws_file.stat().st_mtime, tz=timezone.utc)
        if tasks:
            for task_id in tasks:
                task = _load_json(self._task_file(repo_root, str(task_id)))
                started = _parse_epoch(task.get("started")) if task else None
                if started is not None:
                    created = started if created is None else min(created, started)
        if created is None:
            created = updated

        # Token count: the displayed transcript (not the agent restore-thread)
        tokens = 0
        for row in data.get("transcript") or []:
            tokens += count_tokens(str(row.get("text", "")))
        tokens += count_tokens("\n".join(str(u) for u in data.get("unresolved") or []))

        name = str(data.get("name") or "")
        title = name if name and name != "Untitled" else None
        if title is None:
            for row in data.get("transcript") or []:
                if row.get("role") == "user" and row.get("text"):
                    title = _snippet(row["text"])
                    break

        return Session(
            id=session_id,
            tool=self.tool_name,
            title=title,
            cwd=str(repo_root),
            created_at=created,
            updated_at=updated,
            status="unknown",
            token_count=tokens or None,
            raw_path=str(ws_file),
        )

    def _task_sessions(self, repo_root: Path, workspace: dict) -> list[Session]:
        """One Session per orchestrator task (sub-agent execution)."""
        session: list[Session] = []
        workspace_id = str(workspace.get("id"))
        for task_id in workspace.get("tasks") or []:
            task_file = self._task_file(repo_root, str(task_id))
            data = _load_json(task_file)
            if not data:
                continue
            created = _parse_epoch(data.get("started"))
            updated = _parse_epoch(data.get("ended"))
            events = data.get("events") or []
            if updated is None and events:
                updated = _parse_epoch(events[-1].get("ended")) or created
            updated = updated or created
            tokens = count_tokens(str(data.get("prompt", "")))
            summary = None
            for event in events:
                tokens += count_tokens(event.get("tool", ""))
                result = event.get("result")
                if result is not None:
                    tokens += count_tokens(_json_text(result))
                if event.get("error"):
                    tokens += count_tokens(str(event["error"]))
                    summary = str(event["error"])
            if data.get("error"):
                summary = str(data["error"])

            title = _snippet(str(data.get("prompt", ""))) or None
            session.append(
                Session(
                    id=str(data.get("id") or task_id),
                    tool=self.tool_name,
                    title=title,
                    cwd=str(repo_root),
                    created_at=created,
                    updated_at=updated,
                    status=str(data.get("status") or "unknown"),
                    token_count=tokens or None,
                    raw_path=str(task_file),
                    summary=summary,
                    tags=["subagent", f"workspace:{workspace_id}"],
                )
            )
        return session

    # ------------------------------------------------------------------
    # Message loading
    # ------------------------------------------------------------------

    def load_messages(self, session_id: str) -> list[Message]:
        resolved = self._session_path(session_id)
        if resolved is None:
            return []
        path, _repo_root = resolved
        data = _load_json(path)
        if data is None:
            return []
        if data.get("id") == session_id and (data.get("transcript") is not None or "tasks" in data):
            return self._workspace_messages(data)  # main workspace session
        if "events" in data or "prompt" in data:
            return self._task_messages(data)  # orchestrator task session
        return []

    def _workspace_messages(self, data: dict) -> list[Message]:
        messages: list[Message] = []
        for row in data.get("transcript") or []:
            text = str(row.get("text", "") or "")
            if not text:
                continue
            role = row.get("role")
            if role in ("user", "assistant"):
                mapped = role
            elif role == "notice":
                mapped = "system"
            else:
                continue
            msg = Message(role=mapped, content=text)
            msg.token_count = count_tokens(text)
            messages.append(msg)
        return messages

    def _task_messages(self, data: dict) -> list[Message]:
        """User prompt + one aggregate assistant turn holding all tool events.

        Task events record ``arguments_sha256`` only (the harness does not
        persist raw arguments), so tool-call ``input`` is empty by design.
        """
        messages: list[Message] = []
        prompt = str(data.get("prompt", "") or "")
        if prompt:
            msg = Message(role="user", content=prompt, timestamp=_parse_epoch(data.get("started")))
            msg.token_count = count_tokens(prompt)
            messages.append(msg)

        tool_calls: list[dict] = []
        tool_results: list[dict] = []
        last_ended = _parse_epoch(data.get("ended"))
        for event in data.get("events") or []:
            tool_name = event.get("tool", "?")
            tool_calls.append({"name": tool_name, "input": ""})
            result = event.get("result")
            if result is not None:
                tool_results.append({"tool": tool_name, "output": _json_text(result)})
            if event.get("error"):
                tool_results.append({"tool": tool_name, "output": f"error: {event['error']}"})
            ended = _parse_epoch(event.get("ended"))
            if ended is not None:
                last_ended = ended if last_ended is None else max(last_ended, ended)
        if tool_calls:
            msg = Message(
                role="assistant",
                content=f"{len(tool_calls)} tool calls ({data.get('status') or 'unknown'})",
                timestamp=last_ended,
                tool_calls=tool_calls,
                tool_results=tool_results,
            )
            msg.token_count = _compute_message_tokens(msg)
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
        # datatzis chat is interactive-only: there is no system-prompt or
        # one-shot flag, so injection opens (or resumes) the session and the
        # harness's own handoff/line-mode delivers the context.
        if method == "resume" and target_session_id:
            if cwd:
                return f"cd {shlex.quote(cwd)} && datatzis chat --resume {shlex.quote(target_session_id)}"
            return f"datatzis chat --resume {shlex.quote(target_session_id)}"
        cmd = "datatzis chat"
        if cwd:
            cmd = f"cd {shlex.quote(cwd)} && {cmd}"
        return cmd