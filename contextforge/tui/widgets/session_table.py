"""SessionTable widget — scrollable DataTable of all indexed sessions."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Button, DataTable, Input, Label

# Unicode symbols that evoke each tool brand (terminals can't render images)
TOOL_ICON = {
    "claude_code":    "◆",   # Anthropic — solid diamond
    "codex":          "⬡",   # OpenAI — hexagon
    "altimate_code":  "⚡",   # Altimate — lightning
    "claude_desktop": "◇",   # Claude Desktop — open diamond
    "gemini":         "✦",   # Google Gemini — four-pointed star
    "antigravity":    "◉",   # Antigravity — circle
    "cursor":         "▲",   # Cursor — triangle
    "pi":             "∏",   # Pi — product symbol
    "datatzis":       "✎",   # writing harness — pencil
}

TOOL_MARKUP = {
    "claude_code":    "[cyan]◆ Claude[/cyan]",
    "codex":          "[green]⬡ Codex[/green]",
    "altimate_code":  "[magenta]⚡ Alt[/magenta]",
    "claude_desktop": "[yellow]◇ Desktop[/yellow]",
    "gemini":         "[blue]✦ Gemini[/blue]",
    "antigravity":    "[white]◉ Agy[/white]",
    "cursor":         "[bright_white]▲ Cursor[/bright_white]",
    "pi":             "[bright_cyan]∏ Pi[/bright_cyan]",
    "datatzis":       "[orange1]✎ Datz[/orange1]",
}

TOOL_DISPLAY = {
    "claude_code":    "◆ Claude",
    "codex":          "⬡ Codex",
    "altimate_code":  "⚡ Alt",
    "claude_desktop": "◇ Desktop",
    "gemini":         "✦ Gemini",
    "antigravity":    "◉ Agy",
    "cursor":         "▲ Cursor",
    "pi":             "∏ Pi",
    "datatzis":       "✎ Datz",
}

_FTOOL_MAP = {
    "ftool-all":   None,
    "ftool-cc":    "claude_code",
    "ftool-codex": "codex",
    "ftool-gem":   "gemini",
    "ftool-alt":   "altimate_code",
    "ftool-agy":   "antigravity",
    "ftool-cur":   "cursor",
    "ftool-pi":    "pi",
    "ftool-datz":  "datatzis",
}


def _fmt_tokens(n: int | None) -> str:
    if not n:
        return ""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n // 1_000}k"
    return str(n)


def _token_markup(n: int | None) -> str:
    if not n:
        return "[dim]—[/dim]"
    raw = _fmt_tokens(n)
    if n >= 100_000:
        return f"[bold red]{raw}[/bold red]"
    if n >= 20_000:
        return f"[yellow]{raw}[/yellow]"
    return f"[dim]{raw}[/dim]"


class SessionTable(Widget):
    """Displays all sessions in a navigable DataTable with live filtering."""

    DEFAULT_CSS = """
    SessionTable {
        width: 1fr;
        height: 1fr;
        border: tall $primary;
        border-title-color: $accent;
        border-title-style: bold;
    }
    SessionTable > Vertical {
        height: 1fr;
    }
    SessionTable DataTable {
        height: 1fr;
    }
    SessionTable #filter-bar {
        height: 3;
        background: $primary-background-darken-1;
        padding: 0 1;
        layout: horizontal;
        align: left middle;
    }
    SessionTable #filter-label {
        width: auto;
        margin-right: 1;
        color: $accent;
        text-style: bold;
    }
    SessionTable #filter-input {
        width: 26;
        margin-right: 1;
        border: tall $primary;
    }
    SessionTable .filter-tool-btn {
        min-width: 9;
        height: 1;
        margin-right: 1;
        background: $surface;
        border: none;
        color: $text-muted;
    }
    SessionTable .filter-tool-btn.active {
        background: $primary;
        color: $text;
        text-style: bold;
    }
    SessionTable .hidden {
        display: none;
    }
    """

    # ── Messages ──────────────────────────────────────────────────────────────

    class RowSelected(Message):
        """Emitted when the user highlights a session row."""
        def __init__(self, session_id: str, tool: str) -> None:
            super().__init__()
            self.session_id = session_id
            self.tool = tool

    class FilterChanged(Message):
        """Emitted when filter state changes."""
        def __init__(self, text: str, tool: str | None, match_count: int) -> None:
            super().__init__()
            self.text = text
            self.tool = tool
            self.match_count = match_count

    # ── State ─────────────────────────────────────────────────────────────────

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._rows: list[dict] = []
        self._display_rows: list[dict] = []
        self._filter_text: str = ""
        self._filter_tool: str | None = None
        self._filter_visible: bool = False
        self._expanded_parents: set[str] = set()
        self._preserve_cursor_id: str | None = None

    # ── Compose ───────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(id="filter-bar", classes="hidden"):
                yield Label("Filter:", id="filter-label")
                yield Input(placeholder="title or project…", id="filter-input")
                yield Button("All",      id="ftool-all",    classes="filter-tool-btn active")
                yield Button("◆ CC",    id="ftool-cc",     classes="filter-tool-btn")
                yield Button("⬡ Codex", id="ftool-codex",  classes="filter-tool-btn")
                yield Button("✦ Gem",   id="ftool-gem",    classes="filter-tool-btn")
                yield Button("⚡ Alt",   id="ftool-alt",    classes="filter-tool-btn")
                yield Button("◉ Agy",   id="ftool-agy",    classes="filter-tool-btn")
                yield Button("▲ Cursor", id="ftool-cur",    classes="filter-tool-btn")
                yield Button("∏ Pi",    id="ftool-pi",     classes="filter-tool-btn")
                yield Button("✎ Datz",  id="ftool-datz",   classes="filter-tool-btn")
            yield DataTable(cursor_type="row", zebra_stripes=True, id="sessions-datatable")

    def on_mount(self) -> None:
        self.border_title = "◈ Sessions — select a parent to expand sub-agents"
        table = self.query_one(DataTable)
        table.add_columns("Tool", "Title", "Updated", "Tokens", "ID")
        self.reload()

    # ── Public API ────────────────────────────────────────────────────────────

    def toggle_filter(self) -> None:
        """Show or hide the filter bar."""
        bar = self.query_one("#filter-bar")
        if self._filter_visible:
            self._filter_visible = False
            bar.add_class("hidden")
            self._filter_text = ""
            self._filter_tool = None
            self.query_one("#filter-input", Input).value = ""
            # Reset active button
            for btn_id in _FTOOL_MAP:
                btn = self.query_one(f"#{btn_id}", Button)
                if btn_id == "ftool-all":
                    btn.add_class("active")
                else:
                    btn.remove_class("active")
            self.reload()
            self.post_message(self.FilterChanged("", None, len(self._rows)))
        else:
            self._filter_visible = True
            bar.remove_class("hidden")
            self.query_one("#filter-input", Input).focus()

    def reload(self, tool_filter: str | None = None) -> None:
        """Re-read sessions from DB and refresh the table."""
        db_path: Path | None = getattr(self.app, "db_path", None)
        if db_path is None:
            return

        from contextforge.core.db import get_db, get_sessions
        db = get_db(db_path)
        rows = get_sessions(db, tool=tool_filter, limit=500)
        self._rows = rows
        self._rerender()

    # ── Hierarchy: parent sessions first, sub-agents under their parent ───────

    @staticmethod
    def _children_by_parent(rows: list[dict]) -> dict[str, list[dict]]:
        from contextforge.utils.display import parent_id_of

        children: dict[str, list[dict]] = {}
        for row in rows:
            parent = parent_id_of(row)
            if parent:
                children.setdefault(parent, []).append(row)
        return children

    @staticmethod
    def _mains(rows: list[dict]) -> list[dict]:
        from contextforge.utils.display import is_subagent
        return [row for row in rows if not is_subagent(row)]

    def _build_display_rows(self, mains: list[dict]) -> list[dict]:
        """Main sessions in order, with children of expanded parents nested."""
        children = self._children_by_parent(self._rows)
        display: list[dict] = []
        for main in mains:
            display.append(main)
            if main["id"] in self._expanded_parents:
                for child in children.get(main["id"], []):
                    row = dict(child)
                    row["_is_child"] = True
                    display.append(row)
        return display

    def _rerender(self) -> None:
        """Recompute the display list (filter + expansion) and render."""
        self._apply_filter_and_render()

    def _apply_filter_and_render(self) -> None:
        text = self._filter_text.lower()
        tool = self._filter_tool

        def _row_matches(row: dict) -> bool:
            if tool and row.get("tool") != tool:
                return False
            if not text:
                return True
            return (
                text in (row.get("title") or "").lower()
                or text in (row.get("cwd") or "").lower()
            )

        children = self._children_by_parent(self._rows)

        filtered: list[dict] = []
        for main in self._mains(self._rows):
            if _row_matches(main):
                filtered.append(main)
                continue
            # A child's match keeps its parent (and its siblings) visible
            for child in children.get(main["id"], []):
                if _row_matches(child):
                    filtered.append(main)
                    break
        self._render_rows(self._build_display_rows(filtered))
        self.post_message(self.FilterChanged(self._filter_text, tool, len(self._display_rows)))

    def toggle_expansion(self, parent_id: str | None) -> None:
        """Expand (or collapse) a parent session's sub-agent rows in place."""
        if not parent_id:
            return
        if parent_id in self._expanded_parents:
            self._expanded_parents.discard(parent_id)
        else:
            self._expanded_parents.add(parent_id)
        self._preserve_cursor_id = parent_id
        self._rerender()

    # ── Internal rendering ────────────────────────────────────────────────────

    def _render_rows(self, rows: list[dict]) -> None:
        """Populate the DataTable from a list of session dicts."""
        self._display_rows = rows
        table = self.query_one(DataTable)
        restore_id = self._preserve_cursor_id
        restore_row_index: int | None = None
        table.clear()

        from contextforge.utils.display import _clean_title, display_safe, is_subagent

        for idx, row in enumerate(rows):
            tool = row.get("tool", "")
            is_child = bool(row.get("_is_child"))
            tool_label = TOOL_MARKUP.get(tool, f"⚪ {tool}")

            title = _clean_title(row.get("title") or "", max_len=40) or "(no title)"
            if is_subagent(row):
                title = f"↳ {title}"
                if is_child:
                    title = f"  {title}"
            title = display_safe(title)

            updated_ms = row.get("updated_at") or 0
            try:
                dt = datetime.fromtimestamp(updated_ms / 1000, tz=timezone.utc)
                updated = dt.strftime("%m-%d %H:%M")
            except Exception:
                updated = "?"

            tokens = _token_markup(row.get("token_count"))
            session_id = str(row.get("id", ""))[:12]

            table.add_row(tool_label, title, updated, tokens, session_id)
            if restore_id is not None and str(row.get("id", "")) == restore_id:
                restore_row_index = idx

        if restore_row_index is not None:
            table.move_cursor(row=restore_row_index)
        self._preserve_cursor_id = None

    def _apply_filter(self) -> None:
        """Filter in memory: parent rows match their own fields or any of
        their children's; then re-render with the current expansion state."""
        self._apply_filter_and_render()

    # ── Event handlers ────────────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-input":
            self._filter_text = event.value
            self._apply_filter()

    def on_key(self, event: Key) -> None:
        if event.key == "escape" and self._filter_visible:
            self.toggle_filter()
            event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id not in _FTOOL_MAP:
            return
        self._filter_tool = _FTOOL_MAP[btn_id]
        for bid in _FTOOL_MAP:
            btn = self.query_one(f"#{bid}", Button)
            if bid == btn_id:
                btn.add_class("active")
            else:
                btn.remove_class("active")
        self._apply_filter()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter/click on a parent row expands its sub-agent rows beneath it."""
        idx = event.cursor_row
        display = self._display_rows
        if 0 <= idx < len(display):
            row = display[idx]
            from contextforge.utils.display import is_subagent
            if not is_subagent(row):
                self.toggle_expansion(str(row["id"]))
                event.stop()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        idx = event.cursor_row
        display = self._display_rows
        if 0 <= idx < len(display):
            row = display[idx]
            self.post_message(self.RowSelected(
                session_id=str(row["id"]),
                tool=str(row.get("tool", "")),
            ))
