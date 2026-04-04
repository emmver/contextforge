"""ContextForge TUI — full Textual dashboard."""
from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header

from contextforge.tui.widgets.session_detail import SessionDetail
from contextforge.tui.widgets.session_table import SessionTable
from contextforge.tui.widgets.status_bar import StatusBar
from contextforge.tui.widgets.tokens_panel import TokensPanel
from contextforge.tui.widgets.transfer_panel import TransferPanel


class ContextForgeApp(App):
    """ContextForge dashboard — manage agentic CLI sessions."""

    TITLE = "ContextForge"
    CSS_PATH = "styles.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "rescan", "Rescan"),
        Binding("s", "summarize", "Summarize"),
        Binding("t", "transfer", "Transfer"),
        Binding("c", "compact", "Compact"),
        Binding("x", "tokens", "Tokens"),
        Binding("/", "filter", "Filter"),
    ]

    def __init__(self, db_path: Path | None = None) -> None:
        super().__init__()
        from contextforge.models.config import ForgeConfig
        cfg = ForgeConfig()
        self.db_path: Path = db_path or cfg.db_path

    # ── Layout ────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-horizontal"):
            yield SessionTable(id="session-table")
            yield SessionDetail(id="session-detail")
        yield StatusBar(id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(StatusBar).refresh_stats()

    # ── Session selection ──────────────────────────────────────────────────────

    def on_session_table_row_selected(self, message: SessionTable.RowSelected) -> None:
        self.query_one(SessionDetail).load(message.session_id)
        self._current_session_id = message.session_id
        self._current_tool = message.tool

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_rescan(self) -> None:
        """Re-scan all tools and refresh the table."""
        from contextforge.core.db import get_db
        from contextforge.core.scanner import scan

        db = get_db(self.db_path)
        result = scan(db, quiet=True)
        self.query_one(SessionTable).reload()
        self.query_one(StatusBar).refresh_stats()
        n = result.new + result.updated
        self.notify(
            f"Scan complete: {result.new} new, {result.updated} updated",
            title="Rescan",
            severity="information",
        )

    def action_summarize(self) -> None:
        """Generate/refresh summary for the selected session."""
        sid = getattr(self, "_current_session_id", None)
        if sid is None:
            self.notify("Select a session first.", severity="warning")
            return

        from contextforge.core.db import get_db
        from contextforge.core.summarizer import summarize_session
        from contextforge.models.config import ForgeConfig

        db = get_db(self.db_path)
        summary = summarize_session(db, sid, ForgeConfig())
        if summary:
            self.query_one(SessionDetail)._current_id = None  # bust cache
            self.query_one(SessionDetail).load(sid)
            self.notify("Summary generated.", title="Summarize")
        else:
            self.notify(
                "Could not generate summary (no messages or no API key).",
                severity="warning",
            )

    def action_transfer(self) -> None:
        """Open the transfer panel for the selected session."""
        sid = getattr(self, "_current_session_id", None)
        if sid is None:
            self.notify("Select a session first.", severity="warning")
            return

        from contextforge.core.db import get_db, get_session
        db = get_db(self.db_path)
        row = get_session(db, sid)
        title = (row.get("title") or sid[:16]) if row else sid[:16]

        def on_dismiss(result) -> None:
            if result is None:
                return
            self._run_transfer(
                session_id=sid,
                target_tool=result["tool"],
                strategy=result["strategy"],
                execute=result["execute"],
            )

        self.push_screen(TransferPanel(sid, title), callback=on_dismiss)

    def _run_transfer(
        self,
        session_id: str,
        target_tool: str,
        strategy: str,
        execute: bool,
    ) -> None:
        from contextforge.core.compactor import compact
        from contextforge.core.db import get_db, save_bundle
        from contextforge.core.injector import build_inject_command, execute_transfer

        db = get_db(self.db_path)
        bundle = compact(
            db=db,
            session_ids=[session_id],
            strategy=strategy,
            token_budget=4096,
            target_tool=target_tool,
        )
        cmd, method = build_inject_command(bundle, target_tool)

        if execute:
            bundle_id = save_bundle(db, bundle)
            execute_transfer(
                db=db,
                bundle=bundle,
                bundle_id=bundle_id,
                target_tool=target_tool,
            )
            self.notify(
                f"Launched {target_tool} with {bundle.token_count} tokens of context.",
                title="Transfer executed",
            )
        else:
            # Show the command in a notification (copy-paste friendly)
            self.notify(
                f"[cyan]{cmd}[/cyan]",
                title=f"Preview → {target_tool} ({method})",
                timeout=12,
            )

    def action_compact(self) -> None:
        """Compact the selected session and show the bundle."""
        sid = getattr(self, "_current_session_id", None)
        if sid is None:
            self.notify("Select a session first.", severity="warning")
            return

        from contextforge.core.compactor import compact
        from contextforge.core.db import get_db

        db = get_db(self.db_path)
        bundle = compact(db=db, session_ids=[sid], strategy="summary_only", token_budget=4096)
        self.notify(
            f"Bundle: {bundle.token_count} tokens ({bundle.strategy})\n"
            f"{bundle.compacted_text[:200]}…",
            title="Compact",
            timeout=10,
        )

    def action_tokens(self) -> None:
        """Open the token analysis modal for the selected session."""
        sid = getattr(self, "_current_session_id", None)
        if sid is None:
            self.notify("Select a session first.", severity="warning")
            return
        self.push_screen(TokensPanel(session_id=sid, db_path=self.db_path))

    def action_filter(self) -> None:
        """Focus the table (placeholder for future filter input)."""
        self.query_one(SessionTable).query_one("DataTable").focus()
        self.notify("Use arrow keys to navigate. Filter coming in Phase 5.", timeout=3)
