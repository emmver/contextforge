"""StatusBar widget — bottom bar showing scan time and session counts by tool."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from textual.widget import Widget
from textual.widgets import Static


class StatusBar(Widget):
    """Shows last scan time and per-tool session counts."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        dock: bottom;
        background: $primary-background-darken-2;
        color: $text-muted;
        padding: 0 1;
        layout: horizontal;
    }
    StatusBar Static {
        width: auto;
        margin-right: 2;
    }
    """

    def compose(self):
        yield Static("", id="status-counts")
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
        for row in rows:
            tool = row.get("tool", "unknown")
            counts[tool] = counts.get(tool, 0) + 1

        tool_parts = []
        labels = {"claude_code": "CC", "codex": "Codex", "altimate_code": "Alt"}
        for tool, label in labels.items():
            n = counts.get(tool, 0)
            tool_parts.append(f"{label}:{n}")

        self.query_one("#status-counts").update(
            f"Sessions — {' │ '.join(tool_parts)} │ Total:{len(rows)}"
        )

        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.query_one("#status-scan-time").update(f"Updated {now}")
