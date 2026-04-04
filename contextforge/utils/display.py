"""Rich console helpers."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table

console = Console()
err_console = Console(stderr=True)

TOOL_COLORS = {
    "claude_code": "cyan",
    "codex": "green",
    "altimate_code": "magenta",
}

TOOL_SHORT = {
    "claude_code": "claude",
    "codex": "codex",
    "altimate_code": "altimate",
}


def tool_color(tool: str) -> str:
    return TOOL_COLORS.get(tool, "white")


def _clean_title(raw: str, max_len: int = 42) -> str:
    """Return a single-line, truncated title from raw session display text."""
    # Take only the first non-empty line
    first_line = next((l.strip() for l in raw.splitlines() if l.strip()), raw.strip())
    # Collapse internal whitespace
    first_line = re.sub(r"\s+", " ", first_line)
    if len(first_line) > max_len:
        return first_line[: max_len - 1] + "…"
    return first_line


def _project_name(cwd: str) -> str:
    """Return just the last two path components of a CWD."""
    if not cwd:
        return ""
    parts = cwd.rstrip("/").split("/")
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return parts[-1]


def sessions_table(rows: list[dict]) -> Table:
    table = Table(
        show_header=True,
        header_style="bold",
        show_lines=True,           # horizontal rule between every row
        expand=False,
    )
    table.add_column("ID", style="dim", no_wrap=True, width=13)
    table.add_column("Tool", no_wrap=True, width=10)
    table.add_column("Title", no_wrap=True, max_width=44)
    table.add_column("Project", no_wrap=True, max_width=22, style="dim")
    table.add_column("Tokens", justify="right", no_wrap=True, width=8)
    table.add_column("Updated", no_wrap=True, width=16)
    table.add_column("Summary", no_wrap=True, max_width=45, style="dim")

    for row in rows:
        tool = row.get("tool", "")
        color = tool_color(tool)
        tool_label = f"[{color}]{TOOL_SHORT.get(tool, tool)}[/{color}]"

        session_id = str(row.get("id", ""))[:13]
        title = _clean_title(row.get("title") or "")
        project = _project_name(row.get("cwd") or "")

        raw_tokens = row.get("token_count")
        if raw_tokens and raw_tokens > 1_000_000:
            tokens = f"{raw_tokens / 1_000_000:.1f}M"
        elif raw_tokens and raw_tokens > 1_000:
            tokens = f"{raw_tokens // 1000}k"
        elif raw_tokens:
            tokens = str(raw_tokens)
        else:
            tokens = ""

        updated_ms = row.get("updated_at") or 0
        try:
            updated_dt = datetime.fromtimestamp(updated_ms / 1000, tz=timezone.utc)
            updated = updated_dt.strftime("%m-%d %H:%M")
        except Exception:
            updated = "?"

        summary_raw = row.get("summary") or ""
        summary = _clean_title(summary_raw, max_len=45)

        table.add_row(
            session_id,
            tool_label,
            title,
            project,
            tokens,
            updated,
            summary,
        )

    return table
