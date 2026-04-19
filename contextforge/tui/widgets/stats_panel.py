"""StatsPanel — analytics modal with time-window charts."""
from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Static

# ── Unicode rendering helpers ─────────────────────────────────────────────────

_BLOCKS = " ▏▎▍▌▋▊▉█"   # 9 levels: 0/8 → 8/8 of a cell
_SPARK = "▁▂▃▄▅▆▇█"      # 8 vertical height levels

TOOL_COLORS = {
    "claude_code": "cyan",
    "codex": "green",
    "altimate_code": "magenta",
    "claude_desktop": "yellow",
}
TOOL_LABELS = {
    "claude_code": "Claude Code",
    "codex": "Codex",
    "altimate_code": "altimate",
    "claude_desktop": "Desktop",
}

WINDOW_LABELS = {
    "7d": "7 days",
    "30d": "30 days",
    "6m": "6 months",
    "1y": "1 year",
}


def _unicode_bar(fraction: float, width: int = 32) -> str:
    if fraction <= 0:
        return " " * width
    total_eighths = round(fraction * width * 8)
    full_cells = min(total_eighths // 8, width)
    remainder = total_eighths % 8
    bar = "█" * full_cells
    if full_cells < width and remainder > 0:
        bar += _BLOCKS[remainder]
    return bar.ljust(width)


def _render_bar_line(
    label: str,
    value: int,
    max_value: int,
    color: str = "cyan",
    label_width: int = 14,
    bar_width: int = 32,
) -> str:
    fraction = value / max_value if max_value else 0
    bar = _unicode_bar(fraction, bar_width)
    val_str = _fmt_tokens(value) if value >= 1000 else str(value)
    return f"[dim]{label:<{label_width}}[/dim][{color}]{bar}[/{color}] [bold]{val_str}[/bold]"


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n // 1_000}k"
    return str(n)


def _spark_char(value: int, max_value: int) -> str:
    if not max_value or not value:
        return " "
    idx = max(0, min(7, int(value / max_value * 8) - 1))
    return _SPARK[idx]


def _render_sparkline(buckets: list, width: int = 52) -> str:
    """Return a two-line Rich markup string: bar row + sparse date labels."""
    if not buckets:
        return "[dim](no data)[/dim]"

    max_count = max(b.count for b in buckets) if buckets else 0

    # Downsample if too many buckets
    if len(buckets) > width:
        step = len(buckets) / width
        sampled = []
        for i in range(width):
            idx = min(int(i * step), len(buckets) - 1)
            sampled.append(buckets[idx])
        buckets = sampled

    # Compute quartiles for coloring
    counts = sorted(b.count for b in buckets if b.count > 0)
    q1 = counts[len(counts) // 4] if counts else 1
    q3 = counts[3 * len(counts) // 4] if counts else 1

    bar_chars: list[str] = []
    for b in buckets:
        ch = _spark_char(b.count, max_count)
        if b.count == 0:
            bar_chars.append(f"[dim]{ch}[/dim]")
        elif b.count >= q3:
            bar_chars.append(f"[yellow]{ch}[/yellow]")
        elif b.count >= q1:
            bar_chars.append(f"[green]{ch}[/green]")
        else:
            bar_chars.append(f"[dim]{ch}[/dim]")

    spark_line = "".join(bar_chars)

    # Build sparse date label row (first, middle, last)
    n = len(buckets)
    label_line = [" "] * n
    if n > 0:
        first_lbl = buckets[0].label[-5:]   # last 5 chars e.g. "04-12" or "W15"
        last_lbl = buckets[-1].label[-5:]
        mid_lbl = buckets[n // 2].label[-5:]

        # Place labels without overlap
        label_line[0] = first_lbl[0]
        for i, ch in enumerate(first_lbl):
            if i < n:
                label_line[i] = ch

        mid_start = n // 2
        for i, ch in enumerate(mid_lbl):
            pos = mid_start + i
            if pos < n:
                label_line[pos] = ch

        last_start = max(n - len(last_lbl), 0)
        for i, ch in enumerate(last_lbl):
            pos = last_start + i
            if pos < n:
                label_line[pos] = ch

    date_line = f"[dim]{''.join(label_line)}[/dim]"
    return spark_line + "\n" + date_line


# ── Modal screen ──────────────────────────────────────────────────────────────


class StatsPanel(ModalScreen):
    """Analytics overview modal. Open with 'a', close with ESC/q."""

    BINDINGS = [
        Binding("escape,q", "dismiss(None)", "Close"),
        Binding("w", "set_window_7d", "7d"),
        Binding("m", "set_window_30d", "30d"),
        Binding("h", "set_window_6m", "6m"),
        Binding("y", "set_window_1y", "1y"),
    ]

    DEFAULT_CSS = """
    StatsPanel {
        align: center middle;
    }
    StatsPanel > Vertical {
        width: 82%;
        max-width: 110;
        height: 88%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
        overflow-y: auto;
    }
    StatsPanel #stats-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    StatsPanel #stats-overview {
        color: $text-muted;
        margin-bottom: 1;
        border-bottom: solid $primary-background-lighten-1;
        padding-bottom: 1;
    }
    StatsPanel .chart-title {
        text-style: bold;
        color: $text;
        margin-top: 1;
    }
    StatsPanel #bar-sessions {
        margin-bottom: 1;
    }
    StatsPanel #bar-tokens {
        margin-bottom: 1;
    }
    StatsPanel #sparkline {
        margin-bottom: 1;
    }
    StatsPanel #projects {
        margin-bottom: 1;
    }
    StatsPanel #stats-footer {
        color: $text-disabled;
        text-align: center;
        margin-top: 1;
        border-top: solid $primary-background-lighten-1;
        padding-top: 1;
    }
    """

    window: reactive[str] = reactive("30d")

    def __init__(self) -> None:
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("", id="stats-title")
            yield Static("", id="stats-overview")
            yield Static("Sessions by tool", classes="chart-title")
            yield Static("", id="bar-sessions")
            yield Static("Token usage by tool", classes="chart-title")
            yield Static("", id="bar-tokens")
            yield Static("Activity over time", classes="chart-title")
            yield Static("", id="sparkline")
            yield Static("Top projects", classes="chart-title")
            yield Static("", id="projects")
            yield Static(
                "ESC/q — close  │  W=7d  M=30d  H=6m  Y=1y",
                id="stats-footer",
            )

    def on_mount(self) -> None:
        self._refresh_data()

    def watch_window(self, new_window: str) -> None:
        self._refresh_data()

    # ── Window actions ────────────────────────────────────────────────────────

    def action_set_window_7d(self) -> None:
        self.window = "7d"

    def action_set_window_30d(self) -> None:
        self.window = "30d"

    def action_set_window_6m(self) -> None:
        self.window = "6m"

    def action_set_window_1y(self) -> None:
        self.window = "1y"

    # ── Data loading ──────────────────────────────────────────────────────────

    def _refresh_data(self) -> None:
        db_path: Path | None = getattr(self.app, "db_path", None)
        if db_path is None:
            self.query_one("#stats-overview", Static).update("[red]No database path.[/red]")
            return

        from contextforge.core import analytics
        from contextforge.core.db import get_db

        db = get_db(db_path)
        overview = analytics.get_overview(db, self.window)
        activity = analytics.get_activity_over_time(db, self.window)
        projects = analytics.get_top_projects(db, self.window)

        self._render_all(overview, activity, projects)

    def _render_all(self, overview, activity: list, projects: list) -> None:
        w = self.window
        label = WINDOW_LABELS.get(w, w)

        # ── Title ──────────────────────────────────────────────────────────
        window_indicators = "  ".join(
            f"[bold cyan]▶ {k}[/bold cyan]" if k == w else f"[dim]{k.upper()[0]}[/dim]"
            for k in ("7d", "30d", "6m", "1y")
        )
        self.query_one("#stats-title", Static).update(
            f"[bold]Analytics[/bold]  {window_indicators}  [dim]— last {label}[/dim]"
        )

        # ── Overview ───────────────────────────────────────────────────────
        total_tok_str = _fmt_tokens(overview.total_tokens)
        date_str = ""
        if overview.date_range:
            earliest, latest = overview.date_range
            date_str = (
                f"  │  {earliest.strftime('%b %d')} → {latest.strftime('%b %d, %Y')}"
            )
        tools_str = ", ".join(
            TOOL_LABELS.get(t, t) for t in sorted(overview.active_tools)
        ) or "—"
        self.query_one("#stats-overview", Static).update(
            f"[bold]{overview.total_sessions:,}[/bold] sessions  │  "
            f"[bold]{total_tok_str}[/bold] tokens  │  "
            f"Tools: [dim]{tools_str}[/dim]"
            f"{date_str}"
        )

        # ── Sessions bar chart ─────────────────────────────────────────────
        tool_order = ["claude_code", "codex", "altimate_code", "claude_desktop"]
        max_sess = max(overview.session_count_by_tool.values(), default=1)
        sess_lines: list[str] = []
        for tool in tool_order:
            cnt = overview.session_count_by_tool.get(tool, 0)
            if cnt == 0:
                continue
            color = TOOL_COLORS.get(tool, "white")
            label_str = TOOL_LABELS.get(tool, tool)
            sess_lines.append(_render_bar_line(label_str, cnt, max_sess, color))
        self.query_one("#bar-sessions", Static).update(
            "\n".join(sess_lines) if sess_lines else "[dim](no data)[/dim]"
        )

        # ── Token bar chart ────────────────────────────────────────────────
        max_tok = max(overview.token_sum_by_tool.values(), default=1)
        tok_lines: list[str] = []
        for tool in tool_order:
            tok = overview.token_sum_by_tool.get(tool, 0)
            if tok == 0:
                continue
            color = TOOL_COLORS.get(tool, "white")
            label_str = TOOL_LABELS.get(tool, tool)
            tok_lines.append(_render_bar_line(label_str, tok, max_tok, color))
        self.query_one("#bar-tokens", Static).update(
            "\n".join(tok_lines) if tok_lines else "[dim](no data)[/dim]"
        )

        # ── Sparkline ──────────────────────────────────────────────────────
        self.query_one("#sparkline", Static).update(_render_sparkline(activity))

        # ── Top projects ───────────────────────────────────────────────────
        if projects:
            proj_lines = [
                f"[dim]{i + 1}.[/dim] [bold]{p.project}[/bold] [dim]({p.count})[/dim]"
                for i, p in enumerate(projects)
            ]
            self.query_one("#projects", Static).update("\n".join(proj_lines))
        else:
            self.query_one("#projects", Static).update("[dim](no data)[/dim]")
