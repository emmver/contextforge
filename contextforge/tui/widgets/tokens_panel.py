"""TokensPanel — modal screen showing per-turn token analysis for a session."""
from __future__ import annotations

import json
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static, TextArea

from contextforge.models.session import Message
from contextforge.tui.clipboard import copy as clipboard_copy

# ── Constants ─────────────────────────────────────────────────────────────────

_CONTEXT_WINDOW = 200_000   # reference context window (Claude Sonnet)
_EIGHTHS = " ▏▎▍▌▋▊▉█"

_TOOL_LABELS = {
    "claude_code":    "◆ Claude Code",
    "codex":          "⬡ Codex",
    "altimate_code":  "⚡ Altimate",
    "claude_desktop": "◇ Claude Desktop",
    "gemini":         "✦ Gemini",
}


def _frac_bar(fraction: float, width: int = 22) -> str:
    """Smooth fractional progress bar using Unicode block elements."""
    fraction = max(0.0, min(1.0, fraction))
    total_eighths = round(fraction * width * 8)
    full = min(total_eighths // 8, width)
    remainder = total_eighths % 8
    bar = "█" * full
    if full < width and remainder:
        bar += _EIGHTHS[remainder]
    return bar.ljust(width, "░")


def _fmt(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n // 1_000}k"
    return str(n)


# ── TurnDetailPanel ───────────────────────────────────────────────────────────


class TurnDetailPanel(ModalScreen):
    """Modal showing the full content of a single turn."""

    BINDINGS = [
        Binding("escape,q", "dismiss(None)", "Close"),
        Binding("y", "copy_content", "📋 Copy"),
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
    TurnDetailPanel TextArea {
        height: 1fr;
        border: none;
        background: transparent;
        color: $text;
        padding: 0 1;
    }
    TurnDetailPanel TextArea:focus {
        border: none;
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
        self._plain_text = ""

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("", id="turn-header")
            yield TextArea("", id="turn-body", read_only=True)
            yield Static("[dim]ESC / q — close   select & Ctrl+C[/dim]", id="turn-footer")

    def on_mount(self) -> None:
        msg = self._msg
        role_color = "cyan" if msg.role == "user" else "green"
        role_icon  = "👤" if msg.role == "user" else "🤖"

        # Context pressure for this single turn
        pct = int((msg.token_count or 0) / _CONTEXT_WINDOW * 100)
        pressure_color = "red" if pct >= 80 else ("yellow" if pct >= 40 else "green")

        self.query_one("#turn-header", Static).update(
            f"[bold]Turn #{self._turn_num}[/bold]  "
            f"[{role_color}]{role_icon} {msg.role}[/{role_color}]  "
            f"[bold]{(msg.token_count or 0):,}[/bold] [dim]tokens[/dim]  "
            f"[{pressure_color}]{pct}% of context[/{pressure_color}]"
            + (f"   [yellow]⚙ {len(msg.tool_calls)} call{'s' if len(msg.tool_calls) != 1 else ''}[/yellow]"
               if msg.tool_calls else "")
            + (f"  [magenta]⇥ {len(msg.tool_results)} result{'s' if len(msg.tool_results) != 1 else ''}[/magenta]"
               if msg.tool_results else "")
        )

        plain_parts = []

        if msg.content:
            plain_parts.append("── Content ──\n")
            plain_parts.append(msg.content + "\n")

        if msg.tool_calls:
            plain_parts.append("\n── Tool Calls ──\n")
            for i, tc in enumerate(msg.tool_calls, 1):
                name = tc.get("name", "?")
                raw_input = tc.get("input", "")
                try:
                    formatted = json.dumps(json.loads(raw_input), indent=2)
                except (json.JSONDecodeError, TypeError):
                    formatted = raw_input if isinstance(raw_input, str) else str(raw_input)
                plain_parts.append(f"⚙ {i}. {name}\n")
                plain_parts.append(formatted + "\n")

        if msg.tool_results:
            plain_parts.append("\n── Tool Results ──\n")
            for i, tr in enumerate(msg.tool_results, 1):
                output = tr.get("output", "")
                if not isinstance(output, str):
                    output = str(output)
                plain_parts.append(f"⇥ Result {i}\n")
                plain_parts.append(output + "\n")

        self._plain_text = "".join(plain_parts)
        self.query_one("#turn-body", TextArea).load_text(self._plain_text)

    def action_copy_content(self) -> None:
        """Copy the full turn content to the clipboard."""
        if clipboard_copy(self._plain_text):
            self.notify(f"Copied turn #{self._turn_num} content", title="Copy")
        else:
            self.notify("Failed to copy to clipboard.", severity="error")


# ── TokensPanel ───────────────────────────────────────────────────────────────


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
        width: 92%;
        height: 88%;
        border: thick $accent;
        background: $surface;
        padding: 0 1;
    }
    TokensPanel #tokens-header {
        height: auto;
        padding: 1 1 0 1;
        color: $text;
        border-bottom: solid $primary-background-lighten-1;
        margin-bottom: 1;
    }
    TokensPanel #tokens-summary {
        height: auto;
        padding: 0 1;
        color: $text-muted;
        margin-bottom: 0;
    }
    TokensPanel #tokens-insights {
        height: auto;
        padding: 0 1 1 1;
        margin-bottom: 1;
        border-bottom: solid $primary-background-lighten-1;
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

    _BAR_WIDTH = 20

    def __init__(self, session_id: str, db_path: Path) -> None:
        super().__init__()
        self._session_id = session_id
        self._db_path = db_path
        self._messages: list[Message] = []

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("", id="tokens-header")
            yield Static("", id="tokens-summary")
            yield Static("", id="tokens-insights")
            yield DataTable(id="tokens-table", show_cursor=True, zebra_stripes=True, cursor_type="row")
            yield Static("[dim]ESC/q[/dim] close   [dim]Enter[/dim] view turn", id="tokens-footer")

    def on_mount(self) -> None:
        self._load()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row_idx = event.cursor_row
        if 0 <= row_idx < len(self._messages):
            self.app.push_screen(TurnDetailPanel(row_idx + 1, self._messages[row_idx]))

    def _load(self) -> None:
        from contextforge.adapters.registry import get_adapter
        from contextforge.core.db import get_db, get_session
        from contextforge.core.token_analyzer import analyze_tokens

        db      = get_db(self._db_path)
        report  = analyze_tokens(db, self._session_id)

        row = get_session(db, self._session_id)
        if row is not None:
            try:
                adapter = get_adapter(row["tool"])
                self._messages = adapter.load_messages(self._session_id)
            except Exception:
                self._messages = []

        header   = self.query_one("#tokens-header",   Static)
        summary  = self.query_one("#tokens-summary",  Static)
        insights = self.query_one("#tokens-insights", Static)
        table    = self.query_one("#tokens-table",    DataTable)

        if report is None or not report.turns:
            header.update("[bold red]No token data available.[/bold red]")
            return

        # ── Header ────────────────────────────────────────────────────────
        tool_label = _TOOL_LABELS.get(report.tool, report.tool)
        header.update(
            f"[bold]# Token Analysis[/bold]   "
            f"[dim]{report.title[:55]}[/dim]   "
            f"[dim]{tool_label}[/dim]"
        )

        # ── Summary ───────────────────────────────────────────────────────
        max_t = report.max_turn
        line1 = (
            f"  [bold]{report.total:,}[/bold] total   "
            f"[cyan]{report.user_total:,}[/cyan] user (avg [cyan]{report.avg_user:,.0f}[/cyan])   "
            f"[green]{report.assistant_total:,}[/green] asst (avg [green]{report.avg_assistant:,.0f}[/green])   "
            f"[dim]{report.turn_count} turns[/dim]"
        )
        line2_parts = []
        if report.has_tool_data:
            line2_parts.append(
                f"  [yellow]⚙ calls {report.tool_call_total:,}[/yellow]   "
                f"[magenta]⇥ results {report.tool_result_total:,}[/magenta]"
            )
        if max_t:
            line2_parts.append(
                f"   [dim]heaviest: turn [/dim][bold]#{max_t.turn}[/bold]"
                f"[dim] ({max_t.tokens:,} tok, {max_t.role})[/dim]"
            )
        summary.update(line1 + ("\n" + "".join(line2_parts) if line2_parts else ""))

        # ── Insights ──────────────────────────────────────────────────────
        insight_lines: list[str] = []

        # Context pressure gauge
        ctx_pct  = report.total / _CONTEXT_WINDOW
        ctx_bar  = _frac_bar(ctx_pct, 22)
        ctx_val  = int(ctx_pct * 100)
        ctx_col  = "red" if ctx_val >= 80 else ("yellow" if ctx_val >= 40 else "green")
        insight_lines.append(
            f"  [dim]Context   [/dim][{ctx_col}]{ctx_bar}[/{ctx_col}]"
            f"  [{ctx_col}]{ctx_val}%[/{ctx_col}]  [dim]of ~200k window[/dim]"
        )

        # User / Assistant split bar
        if report.total > 0:
            u_frac   = report.user_total / report.total
            a_frac   = 1.0 - u_frac
            u_chars  = round(u_frac * 22)
            a_chars  = 22 - u_chars
            split_bar = f"[cyan]{'█' * u_chars}[/cyan][green]{'█' * a_chars}[/green]"
            insight_lines.append(
                f"  [dim]U/A split [/dim]{split_bar}"
                f"  [cyan]{int(u_frac * 100)}% user[/cyan]  "
                f"[green]{int(a_frac * 100)}% asst[/green]"
            )

        # Tool overhead
        if report.has_tool_data and report.total > 0:
            tool_total = report.tool_call_total + report.tool_result_total
            t_frac = tool_total / report.total
            t_bar  = _frac_bar(t_frac, 22)
            t_col  = "yellow" if t_frac >= 0.5 else "dim"
            insight_lines.append(
                f"  [dim]Tool load [/dim][{t_col}]{t_bar}[/{t_col}]"
                f"  [{t_col}]{int(t_frac * 100)}%[/{t_col}]  [dim]of tokens in tool calls/results[/dim]"
            )

        # Conversation shape: are later turns heavier than earlier?
        if len(report.turns) >= 4:
            half = len(report.turns) // 2
            first_avg = sum(t.tokens for t in report.turns[:half]) / half
            last_avg  = sum(t.tokens for t in report.turns[half:]) / half
            if first_avg > 0:
                ratio = last_avg / first_avg
                if ratio >= 1.3:
                    shape = "[yellow]↗ growing[/yellow]  [dim](context is building up)[/dim]"
                elif ratio <= 0.7:
                    shape = "[green]↘ tapering[/green]  [dim](turns getting shorter)[/dim]"
                else:
                    shape = "[dim]→ uniform  (turns are evenly sized)[/dim]"
                insight_lines.append(f"  [dim]Shape     [/dim]{shape}")

        insights.update("\n".join(insight_lines))

        # ── Table ─────────────────────────────────────────────────────────
        if report.has_tool_data:
            table.add_columns("#", "Role", "Tokens", "Text", "Calls", "Results", "Cumul.", "Bar", "Preview")
        else:
            table.add_columns("#", "Role", "Tokens", "Cumul.", "Bar", "Preview")

        max_tokens = max(t.tokens for t in report.turns)

        for t in report.turns:
            bar_str = _frac_bar(t.tokens / max_tokens if max_tokens else 0, self._BAR_WIDTH)

            if t.role == "user":
                role_str = "[cyan]👤 user[/cyan]"
                bar_str  = f"[cyan]{bar_str}[/cyan]"
            elif t.role == "assistant":
                role_str = "[green]🤖 asst[/green]"
                bar_str  = f"[green]{bar_str}[/green]"
            else:
                role_str = f"[dim]{t.role}[/dim]"
                bar_str  = f"[dim]{bar_str}[/dim]"

            tok_str = f"[bold]{t.tokens:,}[/bold]" if max_tokens and t.tokens == max_tokens else f"{t.tokens:,}"

            if report.has_tool_data:
                calls_str   = f"[yellow]{t.tool_call_tokens:,}[/yellow]"   if t.tool_call_tokens   else "[dim]—[/dim]"
                results_str = f"[magenta]{t.tool_result_tokens:,}[/magenta]" if t.tool_result_tokens else "[dim]—[/dim]"
                table.add_row(
                    str(t.turn), role_str, tok_str,
                    f"{t.text_tokens:,}", calls_str, results_str,
                    f"{t.cumulative:,}", bar_str, t.content_preview,
                )
            else:
                table.add_row(
                    str(t.turn), role_str, tok_str,
                    f"{t.cumulative:,}", bar_str, t.content_preview,
                )

        if max_t:
            table.move_cursor(row=max_t.turn - 1)
        table.focus()
