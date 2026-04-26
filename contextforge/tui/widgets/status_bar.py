"""StatusBar widget — bottom bar showing scan time and session counts by tool."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from textual.widget import Widget
from textual.widgets import Static


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M tok"
    if n >= 1_000:
        return f"{n // 1_000}k tok"
    return f"{n} tok"


class StatusBar(Widget):
    """Shows last scan time, per-tool session counts, and filter state."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        dock: bottom;
        background: $primary-background-darken-2;
        color: $text-muted;
        padding: 0 1;
        layout: horizontal;
    }
    StatusBar #status-counts {
        width: auto;
        margin-right: 2;
    }
    StatusBar #status-filter {
        width: 1fr;
    }
    StatusBar #status-scan-time {
        width: auto;
    }
    """

    def compose(self):
        yield Static("", id="status-counts")
        yield Static("", id="status-filter")
        yield Static("", id="status-scan-time")

    def refresh_stats(self) -> None:
        """Re-read DB and update the status bar."""
        db_path: Path | None = getattr(self.app, "db_path", None)
        if db_path is None:
            return

        from contextforge.core.db import get_db, get_sessions
        db = get_db(db_path)
        rows = get_sessions(db, limit=10_000)

        counts: dict[str, int] = {}
        total_tokens = 0
        for row in rows:
            tool = row.get("tool", "unknown")
            counts[tool] = counts.get(tool, 0) + 1
            total_tokens += row.get("token_count") or 0

        tool_parts = []
        labels = {
            "claude_code":    "◆ CC",
            "codex":          "⬡ Codex",
            "altimate_code":  "⚡ Alt",
            "claude_desktop": "◇ Desktop",
            "gemini":         "✦ Gemini",
        }
        for tool, label in labels.items():
            n = counts.get(tool, 0)
            if n:
                tool_parts.append(f"{label} {n}")

        tok_str = _fmt_tokens(total_tokens)
        self.query_one("#status-counts").update(
            f"  {' [dim]│[/dim] '.join(tool_parts)}  [dim]│[/dim]  [bold]{len(rows)}[/bold] sessions  [dim]│[/dim]  {tok_str}"
        )

        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.query_one("#status-scan-time").update(f"[dim]⟳ {now}[/dim]")

    def set_filter_indicator(
        self, text: str, tool: str | None, match_count: int
    ) -> None:
        """Show or clear the active filter indicator."""
        if not text and tool is None:
            self.query_one("#status-filter", Static).update("")
            return

        parts: list[str] = []
        if text:
            parts.append(f'"{text}"')
        if tool:
            short = {"claude_code": "◆ CC", "codex": "⬡ Codex", "altimate_code": "⚡ Alt", "claude_desktop": "◇ Desktop", "gemini": "✦ Gemini"}.get(tool, tool)
            parts.append(short)

        self.query_one("#status-filter", Static).update(
            f"[yellow]⌕ {' + '.join(parts)}  [dim]({match_count} matches)[/dim][/yellow]"
        )
