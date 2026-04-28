"""SessionDetail widget — right panel showing summary and metadata for selected session."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Markdown, Static, TextArea

TOOL_COLORS = {
    "claude_code":    "cyan",
    "codex":          "green",
    "altimate_code":  "magenta",
    "claude_desktop": "yellow",
    "gemini":         "blue",
}
TOOL_LABELS = {
    "claude_code":    "◆ Claude Code",
    "codex":          "⬡ Codex",
    "altimate_code":  "⚡ Altimate",
    "claude_desktop": "◇ Claude Desktop",
    "gemini":         "✦ Gemini",
}


def _meta_line(label: str, value: str, color: str = "cyan") -> str:
    return f"[dim]{label:<10}[/dim] {value}"


def _fmt_tokens(n: int | None) -> str:
    if not n:
        return "—"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n // 1_000}k"
    return str(n)


def _token_color(n: int | None) -> str:
    if not n:
        return "dim"
    if n >= 100_000:
        return "bold red"
    if n >= 20_000:
        return "yellow"
    return "dim"


def _fmt_ts(ms: int | None, fmt: str = "%Y-%m-%d %H:%M UTC") -> str:
    if not ms:
        return "?"
    try:
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        return dt.strftime(fmt)
    except Exception:
        return "?"


class SessionDetail(Widget):
    """Shows title, metadata, and summary for the currently selected session."""

    DEFAULT_CSS = """
    SessionDetail {
        width: 2fr;
        height: 1fr;
        padding: 1 2;
        border: tall $primary;
        border-title-color: $accent;
        border-title-style: bold;
        overflow-y: auto;
    }
    SessionDetail #detail-placeholder {
        color: $text-disabled;
        margin: 4 0;
        text-align: center;
        text-style: italic;
    }
    SessionDetail #detail-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
        padding-bottom: 1;
        border-bottom: solid $primary-background-lighten-1;
    }
    SessionDetail #detail-meta-block {
        margin-bottom: 1;
        border-bottom: solid $primary-background-lighten-1;
        padding-bottom: 1;
    }
    SessionDetail #detail-summary-label {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
        margin-top: 1;
    }
    SessionDetail Markdown {
        background: transparent;
        color: $text;
    }
    SessionDetail TextArea {
        height: 1;
        border: none;
        background: transparent;
        color: $text;
        padding: 0;
        margin: 0;
    }
    SessionDetail TextArea:focus {
        border: none;
    }
    """

    _current_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Static("← Select a session", id="detail-placeholder")
        yield Static("", id="detail-title")
        with Vertical(id="detail-meta-block"):
            yield Static("", id="detail-tool")
            yield TextArea("", id="detail-cwd", read_only=True)
            yield Static("", id="detail-tokens")
            yield Static("", id="detail-created")
            yield Static("", id="detail-updated")
            yield Static("", id="detail-status")
            yield Static("", id="detail-tags")
            yield TextArea("", id="detail-id", read_only=True)
        yield Static("✎ Summary", id="detail-summary-label")
        yield Markdown("", id="detail-summary-md")

    def on_mount(self) -> None:
        self.border_title = "◈ Detail"
        self.query_one("#detail-summary-label").display = False
        self.query_one("#detail-meta-block").display = False
        self.query_one("#detail-title").display = False

    def load(self, session_id: str) -> None:
        """Load and display the given session."""
        if session_id == self._current_id:
            return
        self._current_id = session_id

        db_path: Path | None = getattr(self.app, "db_path", None)
        if db_path is None:
            return

        from contextforge.core.db import get_db, get_session
        db = get_db(db_path)
        row = get_session(db, session_id)
        if row is None:
            return

        tool = row.get("tool", "?")
        title = row.get("title") or "(no title)"
        cwd = row.get("cwd") or "?"
        tokens_int = row.get("token_count")
        status = row.get("status") or "?"
        tags_raw = row.get("tags") or "[]"
        try:
            tags = ", ".join(json.loads(tags_raw)) or "—"
        except Exception:
            tags = "—"

        created_str = _fmt_ts(row.get("created_at"))
        updated_str = _fmt_ts(row.get("updated_at"))

        summary = row.get("summary") or "*No summary yet* — run: `cf summarize <id>`"

        # Tool label with color
        tool_color = TOOL_COLORS.get(tool, "white")
        tool_display = TOOL_LABELS.get(tool, tool)
        tool_markup = f"[{tool_color}]{tool_display}[/{tool_color}]"

        # Token display with color
        tok_color = _token_color(tokens_int)
        tok_str = _fmt_tokens(tokens_int)
        tok_markup = f"[{tok_color}]{tok_str}[/{tok_color}]"

        # Show everything
        self.query_one("#detail-placeholder").display = False
        self.query_one("#detail-title").display = True
        self.query_one("#detail-meta-block").display = True
        self.query_one("#detail-summary-label").display = True

        self.query_one("#detail-title", Static).update(f"[bold]{title}[/bold]")
        self.query_one("#detail-tool", Static).update(_meta_line("Tool", tool_markup))
        self.query_one("#detail-cwd", TextArea).load_text("Project   " + cwd)
        self.query_one("#detail-tokens", Static).update(_meta_line("Tokens", tok_markup))
        self.query_one("#detail-created", Static).update(_meta_line("Created", f"[dim]{created_str}[/dim]"))
        self.query_one("#detail-updated", Static).update(_meta_line("Updated", f"[dim]{updated_str}[/dim]"))
        self.query_one("#detail-status", Static).update(_meta_line("Status", f"[dim]{status}[/dim]"))
        self.query_one("#detail-tags", Static).update(_meta_line("Tags", f"[dim]{tags}[/dim]"))
        self.query_one("#detail-id", TextArea).load_text("ID         " + session_id)

        md = self.query_one("#detail-summary-md", Markdown)
        self.app.call_later(md.update, summary)

    def clear(self) -> None:
        self._current_id = None
        self.query_one("#detail-placeholder").display = True
        self.query_one("#detail-title").display = False
        self.query_one("#detail-meta-block").display = False
        self.query_one("#detail-summary-label").display = False
        self.query_one("#detail-title", Static).update("")
        self.app.call_later(self.query_one("#detail-summary-md", Markdown).update, "")
