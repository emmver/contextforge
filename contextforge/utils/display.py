"""Rich console helpers."""
from __future__ import annotations

from rich.console import Console
from rich.table import Table

console = Console()
err_console = Console(stderr=True)

TOOL_COLORS = {
    "claude_code": "cyan",
    "codex": "green",
    "altimate_code": "magenta",
}


def tool_color(tool: str) -> str:
    return TOOL_COLORS.get(tool, "white")


def sessions_table(rows: list[dict]) -> Table:
    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="dim", max_width=12)
    table.add_column("Tool")
    table.add_column("Title", max_width=40)
    table.add_column("CWD", max_width=30, style="dim")
    table.add_column("Tokens", justify="right")
    table.add_column("Updated")
    table.add_column("Summary", max_width=60, style="dim")

    for row in rows:
        tool = row.get("tool", "")
        color = tool_color(tool)
        session_id = str(row.get("id", ""))[:12]
        title = row.get("title") or ""
        cwd = row.get("cwd") or ""
        if len(cwd) > 30:
            cwd = "…" + cwd[-29:]
        tokens = str(row.get("token_count") or "")
        updated_ms = row.get("updated_at") or 0
        from datetime import datetime, timezone
        updated_dt = datetime.fromtimestamp(updated_ms / 1000, tz=timezone.utc)
        updated = updated_dt.strftime("%Y-%m-%d %H:%M")
        summary = (row.get("summary") or "")[:60]

        table.add_row(
            session_id,
            f"[{color}]{tool}[/{color}]",
            title,
            cwd,
            tokens,
            updated,
            summary,
        )

    return table
