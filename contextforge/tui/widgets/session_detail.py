"""SessionDetail widget — right panel showing summary and metadata for selected session."""
from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label, Markdown, Static


class SessionDetail(Widget):
    """Shows title, metadata, and summary for the currently selected session."""

    DEFAULT_CSS = """
    SessionDetail {
        width: 2fr;
        height: 1fr;
        padding: 1 2;
        border-left: tall $primary;
        overflow-y: auto;
    }
    SessionDetail #detail-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    SessionDetail #detail-meta {
        color: $text-muted;
        margin-bottom: 1;
    }
    SessionDetail #detail-summary {
        margin-top: 1;
    }
    SessionDetail #detail-placeholder {
        color: $text-disabled;
        margin: 4 0;
        text-align: center;
    }
    """

    _current_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Static("No session selected", id="detail-placeholder")
        yield Static("", id="detail-title")
        yield Static("", id="detail-meta")
        yield Static("", id="detail-summary")

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

        from datetime import datetime, timezone
        updated_ms = row.get("updated_at") or 0
        try:
            dt = datetime.fromtimestamp(updated_ms / 1000, tz=timezone.utc)
            updated_str = dt.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            updated_str = "?"

        tool = row.get("tool", "?")
        title = row.get("title") or "(no title)"
        cwd = row.get("cwd") or "?"
        tokens = str(row.get("token_count") or "?")
        status = row.get("status") or "?"
        tags_raw = row.get("tags") or "[]"
        import json
        try:
            tags = ", ".join(json.loads(tags_raw)) or "—"
        except Exception:
            tags = "—"

        summary = row.get("summary") or "No summary yet — run: cf summarize <id>"

        self.query_one("#detail-placeholder").display = False
        self.query_one("#detail-title").update(f"● {title}")
        self.query_one("#detail-meta").update(
            f"Tool:     {tool}\n"
            f"CWD:      {cwd}\n"
            f"Tokens:   {tokens}\n"
            f"Updated:  {updated_str}\n"
            f"Status:   {status}\n"
            f"Tags:     {tags}\n"
            f"ID:       {session_id}"
        )
        self.query_one("#detail-summary").update(
            f"\n── Summary ──\n\n{summary}"
        )

    def clear(self) -> None:
        self._current_id = None
        self.query_one("#detail-placeholder").display = True
        self.query_one("#detail-title").update("")
        self.query_one("#detail-meta").update("")
        self.query_one("#detail-summary").update("")
