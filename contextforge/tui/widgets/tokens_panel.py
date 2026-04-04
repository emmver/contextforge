"""TokensPanel — modal screen showing per-turn token analysis for a session."""
from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Static


class TokensPanel(ModalScreen):
    """Full-screen modal showing token breakdown for a session."""

    BINDINGS = [
        Binding("escape,q", "dismiss(None)", "Close"),
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

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("", id="tokens-header")
            yield Static("", id="tokens-stats")
            yield DataTable(id="tokens-table", show_cursor=True, zebra_stripes=True)
            yield Static("ESC / q — close", id="tokens-footer")

    def on_mount(self) -> None:
        self._load()

    def _load(self) -> None:
        from contextforge.core.db import get_db
        from contextforge.core.token_analyzer import analyze_tokens

        db = get_db(self._db_path)
        report = analyze_tokens(db, self._session_id)

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
        stats.update(
            f"Total [bold]{report.total:,}[/bold] tokens  │  "
            f"[cyan]User {report.user_total:,}[/cyan] (avg {report.avg_user:,.0f})  │  "
            f"[green]Asst {report.assistant_total:,}[/green] (avg {report.avg_assistant:,.0f})  │  "
            f"{report.turn_count} turns"
            + (f"  │  Heaviest: turn #{max_t.turn} "
               f"[bold]{max_t.tokens:,}[/bold] tok [{max_t.role}]"
               if max_t else "")
        )

        # ── Table ────────────────────────────────────────────────────────────
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
