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

_EIGHTHS = " ▏▎▍▌▋▊▉█"   # 9 levels for smooth bars
_SPARK = "▁▂▃▄▅▆▇█"      # 8 vertical height levels

TOOL_COLORS = {
    "claude_code":    "cyan",
    "codex":          "green",
    "altimate_code":  "magenta",
    "claude_desktop": "yellow",
    "gemini":         "blue",
}
TOOL_LABELS = {
    "claude_code":    "◆ Claude Code",
    "codex":          "⬡ Codex",
    "altimate_code":  "⚡ Altimate",
    "claude_desktop": "◇ Desktop",
    "gemini":         "✦ Gemini",
}

_TOOL_ORDER = ["claude_code", "codex", "gemini", "altimate_code", "claude_desktop"]

WINDOW_LABELS = {
    "7d":  "7 days",
    "30d": "30 days",
    "6m":  "6 months",
    "1y":  "1 year",
}


def _unicode_bar(fraction: float, width: int = 30) -> str:
    if fraction <= 0:
        return "░" * width
    fraction = min(fraction, 1.0)
    total_eighths = round(fraction * width * 8)
    full_cells = min(total_eighths // 8, width)
    remainder = total_eighths % 8
    bar = "█" * full_cells
    if full_cells < width and remainder > 0:
        bar += _EIGHTHS[remainder]
    return bar.ljust(width, "░")


def _render_bar_line(
    label: str,
    value: int,
    max_value: int,
    color: str = "cyan",
    label_width: int = 16,
    bar_width: int = 30,
) -> str:
    fraction = value / max_value if max_value else 0
    bar = _unicode_bar(fraction, bar_width)
    val_str = _fmt_tokens(value) if value >= 1000 else str(value)
    return (
        f"  [dim]{label:<{label_width}}[/dim]"
        f"[{color}]{bar}[/{color}]"
        f"  [bold]{val_str}[/bold]"
    )


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

    spark_line = "  " + "".join(bar_chars)

    # Build sparse date label row (first, middle, last)
    n = len(buckets)
    label_line = [" "] * n
    if n > 0:
        first_lbl = buckets[0].label[-5:]
        last_lbl  = buckets[-1].label[-5:]
        mid_lbl   = buckets[n // 2].label[-5:]
        for i, ch in enumerate(first_lbl):
            if i < n:
                label_line[i] = ch
        mid_start = n // 2
        for i, ch in enumerate(mid_lbl):
            if mid_start + i < n:
                label_line[mid_start + i] = ch
        last_start = max(n - len(last_lbl), 0)
        for i, ch in enumerate(last_lbl):
            if last_start + i < n:
                label_line[last_start + i] = ch

    date_line = "  [dim]" + "".join(label_line) + "[/dim]"
    return spark_line + "\n" + date_line


def _section(title: str, icon: str = "▤") -> str:
    return f"[bold]{icon} {title}[/bold]"


# ── Modal screen ──────────────────────────────────────────────────────────────


class StatsPanel(ModalScreen):
    """Analytics overview modal. Open with 'a', close with ESC/q."""

    BINDINGS = [
        Binding("escape,q", "dismiss(None)", "Close"),
        Binding("w", "set_window_7d",  "7d"),
        Binding("m", "set_window_30d", "30d"),
        Binding("h", "set_window_6m",  "6m"),
        Binding("y", "set_window_1y",  "1y"),
    ]

    DEFAULT_CSS = """
    StatsPanel {
        align: center middle;
    }
    StatsPanel > Vertical {
        width: 84%;
        max-width: 114;
        height: 90%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
        overflow-y: auto;
    }
    StatsPanel #stats-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
        padding-bottom: 1;
        border-bottom: solid $primary-background-lighten-1;
    }
    StatsPanel #stats-overview {
        margin-bottom: 1;
    }
    StatsPanel #stats-insights {
        margin-bottom: 1;
        padding: 0 0 1 0;
        border-bottom: solid $primary-background-lighten-1;
    }
    StatsPanel .chart-section {
        text-style: bold;
        color: $accent;
        margin-top: 1;
        margin-bottom: 0;
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
            yield Static("", id="stats-insights")
            yield Static(_section("Sessions by tool", "▤"), classes="chart-section")
            yield Static("", id="bar-sessions")
            yield Static(_section("Token usage by tool", "◈"), classes="chart-section")
            yield Static("", id="bar-tokens")
            yield Static(_section("Activity over time", "⌁"), classes="chart-section")
            yield Static("", id="sparkline")
            yield Static(_section("Top projects", "⌂"), classes="chart-section")
            yield Static("", id="projects")
            yield Static(
                "  [dim]ESC/q[/dim] close   [dim]W[/dim] 7d   [dim]M[/dim] 30d   [dim]H[/dim] 6m   [dim]Y[/dim] 1y",
                id="stats-footer",
            )

    def on_mount(self) -> None:
        self._refresh_data()

    def watch_window(self, new_window: str) -> None:
        self._refresh_data()

    # ── Window actions ────────────────────────────────────────────────────────

    def action_set_window_7d(self)  -> None: self.window = "7d"
    def action_set_window_30d(self) -> None: self.window = "30d"
    def action_set_window_6m(self)  -> None: self.window = "6m"
    def action_set_window_1y(self)  -> None: self.window = "1y"

    # ── Data loading ──────────────────────────────────────────────────────────

    def _refresh_data(self) -> None:
        db_path: Path | None = getattr(self.app, "db_path", None)
        if db_path is None:
            self.query_one("#stats-overview", Static).update("[red]No database path.[/red]")
            return

        from contextforge.core import analytics
        from contextforge.core.db import get_db

        db = get_db(db_path)
        overview  = analytics.get_overview(db, self.window)
        activity  = analytics.get_activity_over_time(db, self.window)
        projects  = analytics.get_top_projects(db, self.window)

        # Extra: heavy session count (>100k tokens)
        from contextforge.core import analytics as _a
        start_ms = _a._window_start_ms(self.window)
        try:
            heavy_count = db.execute(
                "SELECT COUNT(*) FROM sessions WHERE updated_at >= ? AND token_count > 100000",
                [start_ms],
            ).fetchone()[0]
        except Exception:
            heavy_count = 0

        self._render_all(overview, activity, projects, heavy_count)

    def _render_all(self, overview, activity: list, projects: list, heavy_count: int) -> None:
        w     = self.window
        label = WINDOW_LABELS.get(w, w)

        # ── Title / window selector ───────────────────────────────────────
        windows = ["7d", "30d", "6m", "1y"]
        keys    = ["W", "M", "H", "Y"]
        pills = "  ".join(
            f"[bold reverse] {k}={ww} [/bold reverse]" if ww == w
            else f"[dim] {k}={ww} [/dim]"
            for ww, k in zip(windows, keys)
        )
        self.query_one("#stats-title", Static).update(
            f"[bold]▤ Analytics[/bold]   {pills}   [dim]last {label}[/dim]"
        )

        # ── Overview card ─────────────────────────────────────────────────
        total_tok_str = _fmt_tokens(overview.total_tokens)
        avg_tok = overview.total_tokens // overview.total_sessions if overview.total_sessions else 0
        avg_str = _fmt_tokens(avg_tok) if avg_tok else "—"

        date_str = ""
        if overview.date_range:
            earliest, latest = overview.date_range
            date_str = f"  [dim]{earliest.strftime('%b %d')} → {latest.strftime('%b %d, %Y')}[/dim]"

        self.query_one("#stats-overview", Static).update(
            f"  [bold]{overview.total_sessions:,}[/bold] sessions   "
            f"[bold]{total_tok_str}[/bold] tokens   "
            f"[dim]avg {avg_str}/session[/dim]"
            f"{date_str}"
        )

        # ── Insights ──────────────────────────────────────────────────────
        insight_lines: list[str] = []

        # Dominant tool
        if overview.session_count_by_tool:
            dominant = max(overview.session_count_by_tool, key=lambda t: overview.session_count_by_tool[t])
            dom_pct = int(
                overview.session_count_by_tool[dominant] / overview.total_sessions * 100
            ) if overview.total_sessions else 0
            dom_label = TOOL_LABELS.get(dominant, dominant)
            dom_color = TOOL_COLORS.get(dominant, "white")
            insight_lines.append(
                f"  [dim]Dominant tool  [/dim][{dom_color}]{dom_label}[/{dom_color}]"
                f"  [dim]{dom_pct}% of sessions[/dim]"
            )

        # Heavy sessions
        if heavy_count:
            heavy_pct = int(heavy_count / overview.total_sessions * 100) if overview.total_sessions else 0
            insight_lines.append(
                f"  [dim]Context pressure[/dim]  "
                f"[yellow]{heavy_count}[/yellow] [dim]sessions >100k tokens ({heavy_pct}%)[/dim]"
            )
        else:
            insight_lines.append(
                f"  [dim]Context pressure[/dim]  [green]none[/green] [dim](no sessions >100k)[/dim]"
            )

        # Activity trend (compare first vs last third of buckets)
        trend_str = ""
        if len(activity) >= 6:
            third = max(len(activity) // 3, 1)
            first_avg = sum(b.count for b in activity[:third]) / third
            last_avg  = sum(b.count for b in activity[-third:]) / third
            if first_avg > 0:
                ratio = last_avg / first_avg
                if ratio >= 1.2:
                    trend_str = f"[green]↗ growing[/green]  [dim](+{int((ratio-1)*100)}%)[/dim]"
                elif ratio <= 0.8:
                    trend_str = f"[red]↘ declining[/red]  [dim]({int((ratio-1)*100)}%)[/dim]"
                else:
                    trend_str = "[dim]→ stable[/dim]"
            else:
                trend_str = "[dim]→ stable[/dim]" if last_avg > 0 else "[dim]— no prior activity[/dim]"
            insight_lines.append(f"  [dim]Trend          [/dim]{trend_str}")

        # Peak activity bucket
        if activity:
            peak = max(activity, key=lambda b: b.count)
            if peak.count > 0:
                insight_lines.append(
                    f"  [dim]Peak period    [/dim][bold]{peak.label}[/bold]"
                    f"  [dim]{peak.count} session{'s' if peak.count != 1 else ''}[/dim]"
                )

        self.query_one("#stats-insights", Static).update("\n".join(insight_lines))

        # ── Sessions bar chart ────────────────────────────────────────────
        max_sess = max(overview.session_count_by_tool.values(), default=1)
        sess_lines: list[str] = []
        for tool in _TOOL_ORDER:
            cnt = overview.session_count_by_tool.get(tool, 0)
            if cnt == 0:
                continue
            color     = TOOL_COLORS.get(tool, "white")
            label_str = TOOL_LABELS.get(tool, tool)
            sess_lines.append(_render_bar_line(label_str, cnt, max_sess, color))
        self.query_one("#bar-sessions", Static).update(
            "\n".join(sess_lines) if sess_lines else "[dim]  (no data)[/dim]"
        )

        # ── Token bar chart ───────────────────────────────────────────────
        max_tok = max(overview.token_sum_by_tool.values(), default=1)
        tok_lines: list[str] = []
        for tool in _TOOL_ORDER:
            tok = overview.token_sum_by_tool.get(tool, 0)
            if tok == 0:
                continue
            color     = TOOL_COLORS.get(tool, "white")
            label_str = TOOL_LABELS.get(tool, tool)
            tok_lines.append(_render_bar_line(label_str, tok, max_tok, color))
        self.query_one("#bar-tokens", Static).update(
            "\n".join(tok_lines) if tok_lines else "[dim]  (no data)[/dim]"
        )

        # ── Sparkline ─────────────────────────────────────────────────────
        self.query_one("#sparkline", Static).update(_render_sparkline(activity))

        # ── Top projects ──────────────────────────────────────────────────
        if projects:
            max_proj = max(p.count for p in projects)
            proj_lines: list[str] = []
            for i, p in enumerate(projects):
                bar = _unicode_bar(p.count / max_proj, 20)
                proj_lines.append(
                    f"  [dim]{i + 1:>2}.[/dim]  [bold]{p.project}[/bold]"
                    f"  [cyan]{bar}[/cyan]  [dim]{p.count}[/dim]"
                )
            self.query_one("#projects", Static).update("\n".join(proj_lines))
        else:
            self.query_one("#projects", Static).update("[dim]  (no data)[/dim]")
