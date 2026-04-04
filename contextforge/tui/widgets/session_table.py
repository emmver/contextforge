"""SessionTable widget — scrollable DataTable of all indexed sessions."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from textual.app import ComposeResult
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import DataTable, Label

TOOL_EMOJI = {
    "claude_code": "🔵",
    "codex": "🟢",
    "altimate_code": "🟣",
}

TOOL_DISPLAY = {
    "claude_code": "Claude Code",
    "codex": "Codex",
    "altimate_code": "altimate",
}


class SessionTable(Widget):
    """Displays all sessions in a navigable DataTable."""

    DEFAULT_CSS = """
    SessionTable {
        width: 1fr;
        height: 1fr;
    }
    SessionTable DataTable {
        height: 1fr;
    }
    """

    class RowSelected(Message):
        """Emitted when the user highlights a session row."""
        def __init__(self, session_id: str, tool: str) -> None:
            super().__init__()
            self.session_id = session_id
            self.tool = tool

    _rows: list[dict] = []

    def compose(self) -> ComposeResult:
        yield DataTable(cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Tool", "Title", "Updated", "Tokens", "ID")
        self.reload()

    def reload(self, tool_filter: str | None = None) -> None:
        """Re-read sessions from DB and refresh the table."""
        db_path: Path | None = getattr(self.app, "db_path", None)
        if db_path is None:
            return

        from contextforge.core.db import get_db, get_sessions
        db = get_db(db_path)
        rows = get_sessions(db, tool=tool_filter, limit=500)
        self._rows = rows

        table = self.query_one(DataTable)
        table.clear()

        for row in rows:
            tool = row.get("tool", "")
            emoji = TOOL_EMOJI.get(tool, "⚪")
            tool_label = f"{emoji} {TOOL_DISPLAY.get(tool, tool)}"

            title = (row.get("title") or "")[:38] or "(no title)"

            updated_ms = row.get("updated_at") or 0
            try:
                dt = datetime.fromtimestamp(updated_ms / 1000, tz=timezone.utc)
                updated = dt.strftime("%m-%d %H:%M")
            except Exception:
                updated = "?"

            tokens = str(row.get("token_count") or "")
            session_id = str(row.get("id", ""))[:12]

            table.add_row(tool_label, title, updated, tokens, session_id)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._rows):
            row = self._rows[idx]
            self.post_message(self.RowSelected(
                session_id=str(row["id"]),
                tool=str(row.get("tool", "")),
            ))
