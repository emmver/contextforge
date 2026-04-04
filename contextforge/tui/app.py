"""Textual TUI dashboard for ContextForge."""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, Static


class ContextForgeApp(App):
    """ContextForge dashboard."""

    TITLE = "ContextForge"
    CSS_PATH = "styles.tcss"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "Run [bold]cf scan[/bold] then relaunch the dashboard.\n\n"
            "TUI dashboard is in Phase 5 — full implementation coming soon.",
            id="placeholder",
        )
        yield Footer()

    def action_refresh(self) -> None:
        self.notify("Re-scanning sessions...")
