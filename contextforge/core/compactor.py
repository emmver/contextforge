"""Context compaction: convert one or more sessions into a token-efficient ContextBundle."""
from __future__ import annotations

import time
from typing import Literal

import sqlite_utils

from contextforge.adapters.registry import get_adapter
from contextforge.core.db import get_session
from contextforge.models.session import ContextBundle, Message
from contextforge.utils.tokens import count_tokens, truncate_to_budget

Strategy = Literal["summary_only", "key_messages", "full_recent"]


def _message_importance(msg: Message, index: int, total: int) -> float:
    score = 0.0
    content = msg.content

    # Recency bonus (later messages score higher)
    score += (index / max(total, 1)) * 2.0

    # Length is a rough proxy for information density
    score += min(len(content) / 500, 3.0)

    # Code blocks, file paths, decisions
    if "```" in content:
        score += 2.0
    if any(kw in content.lower() for kw in ("decided", "solution", "fixed", "implemented", "created")):
        score += 1.5
    if "/" in content and any(ext in content for ext in (".py", ".ts", ".js", ".go", ".rs", ".md")):
        score += 1.0
    if msg.role == "user":
        score += 0.5

    return score


def _compact_summary_only(
    db: sqlite_utils.Database,
    session_ids: list[str],
    token_budget: int,
) -> str:
    parts = []
    for sid in session_ids:
        row = get_session(db, sid)
        if row is None:
            continue
        tool = row.get("tool", "?")
        title = row.get("title") or sid[:12]
        summary = row.get("summary") or row.get("first_message") or "(no summary)"
        parts.append(f"## Session: {title} [{tool}]\n{summary}")
    return "\n\n---\n\n".join(parts)


def _compact_key_messages(
    db: sqlite_utils.Database,
    session_ids: list[str],
    token_budget: int,
) -> str:
    parts = []
    per_session_budget = token_budget // max(len(session_ids), 1)

    for sid in session_ids:
        row = get_session(db, sid)
        if row is None:
            continue
        tool = row.get("tool", "?")
        title = row.get("title") or sid[:12]

        try:
            adapter = get_adapter(tool)
            messages = adapter.load_messages(sid)
        except Exception:
            messages = []

        if not messages:
            summary = row.get("summary") or row.get("first_message") or ""
            parts.append(f"## Session: {title} [{tool}]\n{summary}")
            continue

        # Score and select
        scored = [
            (msg, _message_importance(msg, i, len(messages)))
            for i, msg in enumerate(messages)
        ]
        scored.sort(key=lambda x: x[1], reverse=True)

        selected: list[Message] = []
        used_tokens = 0
        header_tokens = count_tokens(f"## Session: {title} [{tool}]\n")
        remaining = per_session_budget - header_tokens

        for msg, _ in scored:
            t = count_tokens(f"{msg.role.capitalize()}: {msg.content}\n\n")
            if used_tokens + t > remaining:
                break
            selected.append(msg)
            used_tokens += t

        # Restore chronological order
        original_order = {id(m): i for i, m in enumerate(messages)}
        selected.sort(key=lambda m: original_order.get(id(m), 0))

        lines = [f"## Session: {title} [{tool}]"]
        for msg in selected:
            lines.append(f"{msg.role.capitalize()}: {msg.content}")
        parts.append("\n\n".join(lines))

    return "\n\n---\n\n".join(parts)


def _compact_full_recent(
    db: sqlite_utils.Database,
    session_ids: list[str],
    token_budget: int,
) -> str:
    parts = []
    per_session_budget = token_budget // max(len(session_ids), 1)

    for sid in session_ids:
        row = get_session(db, sid)
        if row is None:
            continue
        tool = row.get("tool", "?")
        title = row.get("title") or sid[:12]

        try:
            adapter = get_adapter(tool)
            messages = adapter.load_messages(sid)
        except Exception:
            messages = []

        lines = [f"## Session: {title} [{tool}]"]
        used = count_tokens(lines[0])

        for msg in reversed(messages):
            chunk = f"{msg.role.capitalize()}: {msg.content}"
            t = count_tokens(chunk)
            if used + t > per_session_budget:
                break
            lines.insert(1, chunk)
            used += t

        parts.append("\n\n".join(lines))

    return "\n\n---\n\n".join(parts)


def compact(
    db: sqlite_utils.Database,
    session_ids: list[str],
    strategy: Strategy = "summary_only",
    token_budget: int = 4096,
    name: str | None = None,
    target_tool: str | None = None,
) -> ContextBundle:
    if not name:
        name = f"bundle-{int(time.time())}"

    if strategy == "summary_only":
        text = _compact_summary_only(db, session_ids, token_budget)
    elif strategy == "key_messages":
        text = _compact_key_messages(db, session_ids, token_budget)
    elif strategy == "full_recent":
        text = _compact_full_recent(db, session_ids, token_budget)
    else:
        raise ValueError(f"Unknown strategy: {strategy!r}")

    # Hard cap: never exceed budget
    text = truncate_to_budget(text, token_budget)
    actual_tokens = count_tokens(text)

    return ContextBundle(
        name=name,
        source_sessions=session_ids,
        compacted_text=text,
        token_count=actual_tokens,
        strategy=strategy,
        target_tool=target_tool,
    )
