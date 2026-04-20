"""Per-turn token analysis for a session."""
from __future__ import annotations

from dataclasses import dataclass, field

import sqlite_utils

from contextforge.adapters.registry import get_adapter
from contextforge.core.db import get_session
from contextforge.models.session import Message
from contextforge.utils.tokens import count_tokens


@dataclass
class TurnStats:
    turn: int
    role: str
    tokens: int
    content_preview: str  # first 80 chars, single line
    cumulative: int = 0
    text_tokens: int = 0
    tool_call_tokens: int = 0
    tool_result_tokens: int = 0


@dataclass
class SessionTokenReport:
    session_id: str
    tool: str
    title: str
    turns: list[TurnStats] = field(default_factory=list)
    has_tool_data: bool = False

    @property
    def total(self) -> int:
        return sum(t.tokens for t in self.turns)

    @property
    def tool_call_total(self) -> int:
        return sum(t.tool_call_tokens for t in self.turns)

    @property
    def tool_result_total(self) -> int:
        return sum(t.tool_result_tokens for t in self.turns)

    @property
    def user_total(self) -> int:
        return sum(t.tokens for t in self.turns if t.role == "user")

    @property
    def assistant_total(self) -> int:
        return sum(t.tokens for t in self.turns if t.role == "assistant")

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    @property
    def avg_user(self) -> float:
        user = [t.tokens for t in self.turns if t.role == "user"]
        return sum(user) / len(user) if user else 0.0

    @property
    def avg_assistant(self) -> float:
        asst = [t.tokens for t in self.turns if t.role == "assistant"]
        return sum(asst) / len(asst) if asst else 0.0

    @property
    def max_turn(self) -> TurnStats | None:
        return max(self.turns, key=lambda t: t.tokens) if self.turns else None


def analyze_tokens(
    db: sqlite_utils.Database,
    session_id: str,
) -> SessionTokenReport | None:
    row = get_session(db, session_id)
    if row is None:
        return None

    adapter = get_adapter(row["tool"])
    messages = adapter.load_messages(session_id)

    turns: list[TurnStats] = []
    cumulative = 0
    has_tool_data = False
    for i, msg in enumerate(messages, 1):
        text_tokens = count_tokens(msg.content)
        tool_call_tokens = sum(count_tokens(tc.get("input", "")) for tc in msg.tool_calls)
        tool_result_tokens = sum(count_tokens(tr.get("output", "")) for tr in msg.tool_results)
        tokens = text_tokens + tool_call_tokens + tool_result_tokens
        if tool_call_tokens or tool_result_tokens:
            has_tool_data = True
        cumulative += tokens
        preview = msg.content.replace("\n", " ").strip()[:80]
        turns.append(TurnStats(
            turn=i,
            role=msg.role,
            tokens=tokens,
            content_preview=preview,
            cumulative=cumulative,
            text_tokens=text_tokens,
            tool_call_tokens=tool_call_tokens,
            tool_result_tokens=tool_result_tokens,
        ))

    return SessionTokenReport(
        session_id=session_id,
        tool=row["tool"],
        title=row.get("title") or session_id[:16],
        turns=turns,
        has_tool_data=has_tool_data,
    )
