from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: Literal["user", "assistant", "tool", "system"]
    content: str
    timestamp: datetime | None = None
    token_count: int | None = None
    tool_calls: list[dict] = Field(default_factory=list)  # [{name, input}] on assistant turns
    tool_results: list[dict] = Field(default_factory=list)  # [{output}] attributed to the assistant turn that invoked them


class Session(BaseModel):
    id: str
    tool: str
    title: str | None = None
    cwd: str | None = None
    created_at: datetime
    updated_at: datetime
    messages: list[Message] = Field(default_factory=list)
    token_count: int | None = None
    raw_path: str | None = None
    status: str = "unknown"
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)


class ContextBundle(BaseModel):
    name: str
    source_sessions: list[str]
    compacted_text: str
    token_count: int
    strategy: str
    target_tool: str | None = None
