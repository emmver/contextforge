"""TokensPanel — modal screen showing per-turn token analysis for a session."""
from __future__ import annotations

import json
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from contextforge.models.session import Message


class TurnDetailPanel(ModalScreen):
    """Modal showing the full content of a single turn."""

    BINDINGS = [
        Binding("escape,q", "dismiss(None)", "Close"),
    ]

    DEFAULT_CSS = """
    TurnDetailPanel {
        align: center middle;
    }
    TurnDetailPanel > Vertical {
        width: 92%;
        height: 88%;
        border: thick $accent;
        background: $surface;
        padding: 0 1;
    }
    TurnDetailPanel #turn-header {
        height: auto;
        padding: 1 1 0 1;
        border-bottom: solid $primary-background-lighten-1;
        margin-bottom: 1;
    }
    TurnDetailPanel ScrollableContainer {
        height: 1fr;
        padding: 0 1;
    }
    TurnDetailPanel .section-label {
        color: $text-muted;
        text-style: bold;
        margin-top: 1;
    }
    TurnDetailPanel .content-block {
        color: $text;
        margin-bottom: 1;
    }
    TurnDetailPanel .tool-name {
        color: $warning;
        text-style: bold;
    }
    TurnDetailPanel .tool-input {
        color: $text-muted;
        margin-left: 2;
    }
    TurnDetailPanel .tool-output {
        color: $success;
        margin-left: 2;
    }
    TurnDetailPanel #turn-footer {
        height: 1;
        color: $text-disabled;
        text-align: center;
    }
    """

    def __init__(self, turn_num: int, msg: Message) -> None:
        super().__init__()
        self._turn_num = turn_num
        self._msg = msg

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("", id="turn-header")
            with ScrollableContainer():
                yield Static("", id="turn-body")
            yield Static("ESC / q — close", id="turn-footer")

    def on_mount(self) -> None:
        msg = self._msg
        role_color = "cyan" if msg.role == "user" else "green"

        self.query_one("#turn-header", Static).update(
            f"[bold]Turn #{self._turn_num}[/bold]  "
            f"[{role_color}]{msg.role}[/{role_color}]  "
            f"[dim]{(msg.token_count or 0):,} tokens[/dim]"
            + (f"  [yellow]{len(msg.tool_calls)} call(s)[/yellow]" if msg.tool_calls else "")
            + (f"  [magenta]{len(msg.tool_results)} result(s)[/magenta]" if msg.tool_results else "")
        )

        parts: list[str] = []

        # ── Text content ────────────────────────────────────────────────────
        if msg.content:
            parts.append("[bold dim]── Content ──[/bold dim]")
            parts.append(msg.content)

        # ── Tool calls ──────────────────────────────────────────────────────
        if msg.tool_calls:
            parts.append("\n[bold dim]── Tool Calls ──[/bold dim]")
            for i, tc in enumerate(msg.tool_calls, 1):
                name = tc.get("name", "?")
                raw_input = tc.get("input", "")
                try:
                    formatted = json.dumps(json.loads(raw_input), indent=2)
                except (json.JSONDecodeError, TypeError):
                    formatted = raw_input
                parts.append(f"[bold yellow]{i}. {name}[/bold yellow]")
                parts.append(f"[dim]{formatted}[/dim]")

        # ── Tool results ────────────────────────────────────────────────────
        if msg.tool_results:
            parts.append("\n[bold dim]── Tool Results ──[/bold dim]")
            for i, tr in enumerate(msg.tool_results, 1):
                output = tr.get("output", "")
                parts.append(f"[bold green]Result {i}[/bold green]")
                parts.append(f"[dim]{output}[/dim]")

        self.query_one("#turn-body", Static).update("\n".join(parts))


class TokensPanel(ModalScreen):
    """Full-screen modal showing token breakdown for a session."""

    BINDINGS = [
        Binding("escape,q", "dismiss(None)", "Close"),
        Binding("enter", "open_turn", "View turn"),
    ]

    DEFAULT_CSS = """
    TokensPanel {
        align: center middle;
    }
    TokensPanel > Vertical {
        width: 90%;
        height: 85%;
        border: thick $primary;
        background: $surface;
        padding: 0 1;
    }
    TokensPanel #tokens-header {
        height: auto;
        padding: 1 1 0 1;
        color: $text;
    }
    TokensPanel #tokens-stats {
        height: auto;
        padding: 0 1 1 1;
        color: $text-muted;
        border-bottom: solid $primary-background-lighten-1;
        margin-bottom: 1;
    }
    TokensPanel DataTable {
        height: 1fr;
    }
    TokensPanel #tokens-footer {
        height: 1;
        color: $text-disabled;
        text-align: center;
        padding: 0 1;
    }
    """

    _BAR_MAX = 24

    def __init__(self, session_id: str, db_path: Path) -> None:
        super().__init__()
        self._session_id = session_id
        self._db_path = db_path
        self._messages: list[Message] = []

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("", id="tokens-header")
            yield Static("", id="tokens-stats")
            yield DataTable(id="tokens-table", show_cursor=True, zebra_stripes=True)
            yield Static("ESC / q — close  │  Enter — view turn", id="tokens-footer")

    def on_mount(self) -> None:
        self._load()

    def action_open_turn(self) -> None:
        table = self.query_one("#tokens-table", DataTable)
        row_idx = table.cursor_row
        if 0 <= row_idx < len(self._messages):
            self.app.push_screen(TurnDetailPanel(row_idx + 1, self._messages[row_idx]))

    def _load(self) -> None:
        from contextforge.adapters.registry import get_adapter
        from contextforge.core.db import get_db, get_session
        from contextforge.core.token_analyzer import analyze_tokens

        db = get_db(self._db_path)
        report = analyze_tokens(db, self._session_id)

        # Load messages so TurnDetailPanel can show full content
        row = get_session(db, self._session_id)
        if row is not None:
            try:
                adapter = get_adapter(row["tool"])
                self._messages = adapter.load_messages(self._session_id)
            except Exception:
                self._messages = []

        header = self.query_one("#tokens-header", Static)
        stats = self.query_one("#tokens-stats", Static)
        table = self.query_one("#tokens-table", DataTable)

        if report is None or not report.turns:
            header.update("[bold red]No token data available.[/bold red]")
            return

        # ── Header ──────────────────────────────────────────────────────────
        header.update(
            f"[bold accent]Token Analysis[/bold accent]  "
            f"[dim]{report.title[:60]}[/dim]  "
            f"[dim]{report.tool}[/dim]"
        )

        # ── Stats bar ───────────────────────────────────────────────────────
        max_t = report.max_turn
        tool_summary = ""
        if report.has_tool_data:
            tool_summary = (
                f"  │  [yellow]Calls {report.tool_call_total:,}[/yellow]"
                f"  [magenta]Results {report.tool_result_total:,}[/magenta]"
            )
        stats.update(
            f"Total [bold]{report.total:,}[/bold] tokens  │  "
            f"[cyan]User {report.user_total:,}[/cyan] (avg {report.avg_user:,.0f})  │  "
            f"[green]Asst {report.assistant_total:,}[/green] (avg {report.avg_assistant:,.0f})  │  "
            f"{report.turn_count} turns"
            + tool_summary
            + (f"  │  Heaviest: turn #{max_t.turn} "
               f"[bold]{max_t.tokens:,}[/bold] tok [{max_t.role}]"
               if max_t else "")
        )

        # ── Table ────────────────────────────────────────────────────────────
        if report.has_tool_data:
            table.add_columns("#", "Role", "Tokens", "Text", "Calls", "Results", "Cumul.", "Bar", "Preview")
        else:
            table.add_columns("#", "Role", "Tokens", "Cumul.", "Bar", "Preview")

        max_tokens = max(t.tokens for t in report.turns)

        for t in report.turns:
            bar_len = max(1, int(t.tokens / max_tokens * self._BAR_MAX))

            if t.role == "user":
                role_str = "[cyan]user[/cyan]"
                bar_str = f"[cyan]{'█' * bar_len}[/cyan]"
            elif t.role == "assistant":
                role_str = "[green]asst[/green]"
                bar_str = f"[green]{'█' * bar_len}[/green]"
            else:
                role_str = f"[dim]{t.role}[/dim]"
                bar_str = f"[dim]{'█' * bar_len}[/dim]"

            if report.has_tool_data:
                calls_str = f"[yellow]{t.tool_call_tokens:,}[/yellow]" if t.tool_call_tokens else "[dim]—[/dim]"
                results_str = f"[magenta]{t.tool_result_tokens:,}[/magenta]" if t.tool_result_tokens else "[dim]—[/dim]"
                table.add_row(
                    str(t.turn),
                    role_str,
                    f"{t.tokens:,}",
                    f"{t.text_tokens:,}",
                    calls_str,
                    results_str,
                    f"{t.cumulative:,}",
                    bar_str,
                    t.content_preview,
                )
            else:
                table.add_row(
                    str(t.turn),
                    role_str,
                    f"{t.tokens:,}",
                    f"{t.cumulative:,}",
                    bar_str,
                    t.content_preview,
                )

        # Highlight the heaviest row
        if max_t:
            table.move_cursor(row=max_t.turn - 1)
