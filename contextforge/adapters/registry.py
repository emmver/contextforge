"""Adapter registry — maps tool names to adapter instances."""
from __future__ import annotations

from contextforge.adapters.altimate_code import AltimateCodeAdapter
from contextforge.adapters.base import ToolAdapter
from contextforge.adapters.claude_code import ClaudeCodeAdapter
from contextforge.adapters.claude_desktop import ClaudeDesktopAdapter
from contextforge.adapters.codex import CodexAdapter

ADAPTERS: dict[str, type[ToolAdapter]] = {
    "claude_code": ClaudeCodeAdapter,
    "claude_desktop": ClaudeDesktopAdapter,
    "codex": CodexAdapter,
    "altimate_code": AltimateCodeAdapter,
}


def get_adapter(tool_name: str) -> ToolAdapter:
    cls = ADAPTERS.get(tool_name)
    if cls is None:
        raise ValueError(f"Unknown tool: {tool_name!r}. Available: {list(ADAPTERS)}")
    return cls()


def get_all_adapters() -> list[ToolAdapter]:
    return [cls() for cls in ADAPTERS.values()]


def get_available_adapters() -> list[ToolAdapter]:
    """Return only adapters whose data paths exist on this machine."""
    return [a for a in get_all_adapters() if a.is_available()]
