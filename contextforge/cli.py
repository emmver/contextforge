"""ContextForge CLI — cf command."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel

from contextforge.core import db as db_module
from contextforge.core.compactor import compact
from contextforge.core.injector import build_inject_command, execute_transfer
from contextforge.core.scanner import scan
from contextforge.core.summarizer import batch_summarize, summarize_session
from contextforge.models.config import ForgeConfig
from contextforge.utils.display import console, err_console, sessions_table

def _version_callback(value: bool) -> None:
    if value:
        from importlib.metadata import version
        typer.echo(f"context-forge-cli {version('context-forge-cli')}")
        raise typer.Exit()


app = typer.Typer(
    name="cf",
    help="ContextForge — session manager and context bridge for agentic CLI tools.",
    add_completion=False,
    no_args_is_help=True,
)


@app.callback()
def main(
    version: Annotated[
        Optional[bool],
        typer.Option("--version", "-V", callback=_version_callback, is_eager=True, help="Show version and exit."),
    ] = None,
) -> None:
    pass

config_app = typer.Typer(name="config", help="Manage ContextForge configuration.")
app.add_typer(config_app, name="config")


def _get_config() -> ForgeConfig:
    return ForgeConfig()


def _get_db(cfg: ForgeConfig):
    return db_module.get_db(cfg.db_path)


# ---------------------------------------------------------------------------
# cf scan
# ---------------------------------------------------------------------------

@app.command(name="scan")
def scan_cmd(
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
    summarize: Annotated[bool, typer.Option("--summarize", "-s")] = False,
):
    """Discover and index sessions from all installed agentic tools.

    Use --summarize to also generate summaries for new sessions after scanning.
    Requires ANTHROPIC_API_KEY (or api_key in config) for LLM summaries;
    falls back to first-message preview if no key is set.
    """
    cfg = _get_config()
    database = _get_db(cfg)
    result = scan(database, quiet=quiet)

    if not quiet:
        console.print(
            f"[green]Scan complete:[/green] "
            f"{result.new} new, {result.updated} updated, {result.unchanged} unchanged"
        )
        for err in result.errors:
            err_console.print(f"[yellow]Warning:[/yellow] {err}")

    if summarize and result.sessions:
        new_ids = [s.id for s in result.sessions]
        if not quiet:
            console.print(f"[dim]Summarizing {len(new_ids)} sessions...[/dim]")

        from rich.progress import Progress, SpinnerColumn, TextColumn
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            disable=quiet,
        ) as progress:
            task = progress.add_task("Summarizing...", total=len(new_ids))

            def on_prog(sid: str, summary: str | None) -> None:
                status = "[green]ok[/green]" if summary else "[dim]skipped[/dim]"
                progress.advance(task)
                progress.update(task, description=f"Summarized {sid[:12]} {status}")

            summary_result = batch_summarize(
                db=database,
                config=cfg,
                session_ids=new_ids,
                on_progress=on_prog,
            )

        if not quiet:
            console.print(
                f"[green]Summaries:[/green] "
                f"{summary_result.summarized} generated, {summary_result.skipped} skipped"
            )

    sys.exit(0 if not result.errors or result.total > 0 else 1)


# ---------------------------------------------------------------------------
# cf ls
# ---------------------------------------------------------------------------

@app.command(name="ls")
def list_sessions(
    tool: Annotated[Optional[str], typer.Option("--tool", "-t")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 50,
    fmt: Annotated[str, typer.Option("--format", "-f")] = "table",
):
    """List sessions."""
    cfg = _get_config()
    database = _get_db(cfg)
    rows = db_module.get_sessions(database, tool=tool, limit=limit)

    if fmt == "json":
        console.print_json(json.dumps(rows))
        return

    if not rows:
        console.print("[dim]No sessions found. Run [bold]cf scan[/bold] first.[/dim]")
        return

    table = sessions_table(rows)
    console.print(table)
    console.print(f"[dim]{len(rows)} sessions[/dim]")


# ---------------------------------------------------------------------------
# cf show
# ---------------------------------------------------------------------------

@app.command()
def show(
    session_id: str,
    fmt: Annotated[str, typer.Option("--format", "-f")] = "rich",
):
    """Show detail and summary for a session."""
    cfg = _get_config()
    database = _get_db(cfg)
    row = db_module.get_session(database, session_id)

    if row is None:
        err_console.print(f"[red]Session not found:[/red] {session_id}")
        raise typer.Exit(1)

    if fmt == "json":
        console.print_json(json.dumps(row))
        return

    summary = row.get("summary") or "[dim]No summary yet — run [bold]cf summarize[/bold][/dim]"
    panel = Panel(
        f"[bold]{row.get('title') or '(no title)'}[/bold]\n\n"
        f"Tool:     {row.get('tool')}\n"
        f"CWD:      {row.get('cwd') or '?'}\n"
        f"Tokens:   {row.get('token_count') or '?'}\n"
        f"Status:   {row.get('status')}\n\n"
        f"[bold]Summary:[/bold]\n{summary}",
        title=f"Session {session_id[:16]}",
    )
    console.print(panel)


# ---------------------------------------------------------------------------
# cf summarize
# ---------------------------------------------------------------------------

@app.command()
def summarize(
    session_id: Annotated[Optional[str], typer.Argument()] = None,
    all_sessions: Annotated[bool, typer.Option("--all")] = False,
    force: Annotated[bool, typer.Option("--force")] = False,
):
    """Generate or refresh a summary for one or all sessions."""
    cfg = _get_config()
    database = _get_db(cfg)

    if all_sessions:
        from rich.progress import Progress, SpinnerColumn, TextColumn

        rows = db_module.get_sessions(database, limit=10_000)
        pending = [r for r in rows if not r.get("summary") or force]
        console.print(f"Summarizing {len(pending)} sessions...")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
        ) as progress:
            task = progress.add_task("Summarizing...", total=len(pending))

            def on_prog(sid: str, summary: str | None) -> None:
                status = "[green]ok[/green]" if summary else "[dim]skipped[/dim]"
                progress.advance(task)
                progress.update(task, description=f"{sid[:16]} {status}")

            result = batch_summarize(
                db=database,
                config=cfg,
                session_ids=[r["id"] for r in pending],
                force=force,
                on_progress=on_prog,
            )

        console.print(
            f"[green]Done:[/green] {result.summarized} generated, "
            f"{result.skipped} skipped"
            + (f", {len(result.errors)} errors" if result.errors else "")
        )
        return

    if session_id is None:
        err_console.print("[red]Provide a session ID or use --all[/red]")
        raise typer.Exit(1)

    summary = summarize_session(database, session_id, cfg, force=force)
    if summary:
        console.print(Panel(summary, title="Summary"))
    else:
        err_console.print("[yellow]Could not generate summary (no messages or no API key)[/yellow]")


# ---------------------------------------------------------------------------
# cf compact
# ---------------------------------------------------------------------------

@app.command(name="compact")
def compact_cmd(
    session_ids: Annotated[list[str], typer.Argument()],
    strategy: Annotated[str, typer.Option("--strategy", "-s")] = "summary_only",
    budget: Annotated[int, typer.Option("--budget", "-b")] = 4096,
    name: Annotated[Optional[str], typer.Option("--name")] = None,
    target_tool: Annotated[Optional[str], typer.Option("--to")] = None,
    fmt: Annotated[str, typer.Option("--format", "-f")] = "rich",
    save: Annotated[bool, typer.Option("--save")] = False,
):
    """Create a ContextBundle from one or more sessions."""
    cfg = _get_config()
    database = _get_db(cfg)

    bundle = compact(
        db=database,
        session_ids=session_ids,
        strategy=strategy,
        token_budget=budget,
        name=name,
        target_tool=target_tool,
    )

    if fmt == "json":
        console.print_json(bundle.model_dump_json())
        return

    console.print(Panel(
        f"Strategy:  {bundle.strategy}\n"
        f"Sessions:  {', '.join(bundle.source_sessions)}\n"
        f"Tokens:    {bundle.token_count} / {budget}\n\n"
        f"[bold]Context:[/bold]\n{bundle.compacted_text[:1000]}"
        + ("[dim]…[/dim]" if len(bundle.compacted_text) > 1000 else ""),
        title=f"Bundle: {bundle.name}",
    ))

    if save:
        bundle_id = db_module.save_bundle(database, bundle)
        console.print(f"[green]Saved bundle ID:[/green] {bundle_id}")


# ---------------------------------------------------------------------------
# cf transfer
# ---------------------------------------------------------------------------

@app.command()
def transfer(
    session_ids: Annotated[list[str], typer.Argument()],
    target_tool: Annotated[str, typer.Option("--to")] = ...,
    strategy: Annotated[str, typer.Option("--strategy", "-s")] = "summary_only",
    budget: Annotated[int, typer.Option("--budget", "-b")] = 4096,
    target_session: Annotated[Optional[str], typer.Option("--session")] = None,
    cwd: Annotated[Optional[str], typer.Option("--cwd")] = None,
    method: Annotated[Optional[str], typer.Option("--method")] = None,
    execute: Annotated[bool, typer.Option("--execute")] = False,
    fmt: Annotated[str, typer.Option("--format", "-f")] = "rich",
):
    """Compact context from sessions and inject into a new/existing session.

    By default this is a dry run — prints the command without executing.
    Use --execute to actually launch the target tool.
    """
    cfg = _get_config()
    database = _get_db(cfg)

    bundle = compact(
        db=database,
        session_ids=session_ids,
        strategy=strategy,
        token_budget=budget,
        target_tool=target_tool,
    )

    cmd, actual_method = build_inject_command(
        bundle=bundle,
        target_tool=target_tool,
        target_session_id=target_session,
        cwd=cwd,
        method=method,
    )

    if fmt == "json":
        output = {
            "target_tool": target_tool,
            "method": actual_method,
            "bundle_tokens": bundle.token_count,
            "bundle_strategy": bundle.strategy,
            "source_sessions": bundle.source_sessions,
            "command": cmd,
            "dry_run": not execute,
            "context_file_needed": actual_method == "file",
        }
        console.print_json(json.dumps(output))
    else:
        file_note = (
            "\n[dim]Note: CONTEXT.md will be written to the target directory.[/dim]"
            if actual_method == "file"
            else ""
        )
        console.print(Panel(
            f"Target tool:   {target_tool}\n"
            f"Method:        {actual_method}\n"
            f"Bundle tokens: {bundle.token_count} / {budget}\n"
            f"Strategy:      {bundle.strategy}\n\n"
            f"[bold]Command:[/bold]\n[cyan]{cmd}[/cyan]{file_note}",
            title="Transfer Preview",
        ))

    if execute:
        bundle_id = db_module.save_bundle(database, bundle)
        if fmt != "json":
            console.print("[yellow]Executing...[/yellow]")
        execute_transfer(
            db=database,
            bundle=bundle,
            bundle_id=bundle_id,
            target_tool=target_tool,
            target_session_id=target_session,
            cwd=cwd,
            method=method,
        )
    elif fmt != "json":
        console.print("[dim]Dry run. Pass [bold]--execute[/bold] to launch.[/dim]")


# ---------------------------------------------------------------------------
# cf tokens
# ---------------------------------------------------------------------------

@app.command()
def tokens(
    session_id: str,
    top: Annotated[int, typer.Option("--top", "-n", help="Show only the N heaviest turns")] = 0,
    fmt: Annotated[str, typer.Option("--format", "-f")] = "rich",
):
    """Show per-turn token breakdown for a session.

    Counts tokens in every message, shows role totals, averages, and
    a bar chart of consumption per turn.
    """
    from contextforge.core.token_analyzer import analyze_tokens
    from rich.table import Table
    from rich.text import Text

    cfg = _get_config()
    database = _get_db(cfg)

    report = analyze_tokens(database, session_id)
    if report is None:
        err_console.print(f"[red]Session not found:[/red] {session_id}")
        raise typer.Exit(1)

    if not report.turns:
        err_console.print("[yellow]No messages found for this session.[/yellow]")
        raise typer.Exit(0)

    if fmt == "json":
        out = {
            "session_id": report.session_id,
            "tool": report.tool,
            "title": report.title,
            "total_tokens": report.total,
            "user_tokens": report.user_total,
            "assistant_tokens": report.assistant_total,
            "turn_count": report.turn_count,
            "avg_user_tokens": round(report.avg_user, 1),
            "avg_assistant_tokens": round(report.avg_assistant, 1),
            "turns": [
                {
                    "turn": t.turn,
                    "role": t.role,
                    "tokens": t.tokens,
                    "cumulative": t.cumulative,
                    "preview": t.content_preview,
                }
                for t in report.turns
            ],
        }
        console.print_json(json.dumps(out))
        return

    # ── Summary panel ──────────────────────────────────────────────────────
    max_t = report.max_turn
    console.print(Panel(
        f"[bold]{report.title}[/bold]  [{report.tool}]\n\n"
        f"Total tokens:   [bold]{report.total:,}[/bold]\n"
        f"Turns:          {report.turn_count}  "
        f"([cyan]user {report.turn_count // 2 or len([t for t in report.turns if t.role == 'user'])}[/cyan] / "
        f"[green]asst {len([t for t in report.turns if t.role == 'assistant'])}[/green])\n"
        f"User total:     [cyan]{report.user_total:,}[/cyan]  "
        f"(avg {report.avg_user:,.0f}/turn)\n"
        f"Assistant total:[green]{report.assistant_total:,}[/green]  "
        f"(avg {report.avg_assistant:,.0f}/turn)\n"
        + (f"Heaviest turn:  #{max_t.turn} [{max_t.role}] — "
           f"[bold]{max_t.tokens:,}[/bold] tokens" if max_t else ""),
        title=f"Token Analysis · {session_id[:16]}",
    ))

    # ── Per-turn table ──────────────────────────────────────────────────────
    BAR_MAX = 30
    turns_to_show = report.turns
    if top:
        turns_to_show = sorted(report.turns, key=lambda t: t.tokens, reverse=True)[:top]
        turns_to_show = sorted(turns_to_show, key=lambda t: t.turn)

    max_tokens = max((t.tokens for t in report.turns), default=1)

    table = Table(show_header=True, header_style="bold", show_lines=False, expand=True)
    table.add_column("#", justify="right", width=4, no_wrap=True)
    table.add_column("Role", width=8, no_wrap=True)
    table.add_column("Tokens", justify="right", width=8, no_wrap=True)
    table.add_column("Cumul.", justify="right", width=9, no_wrap=True)
    table.add_column("Bar", min_width=BAR_MAX, no_wrap=True)
    table.add_column("Preview", no_wrap=True)

    for t in turns_to_show:
        bar_len = max(1, int(t.tokens / max_tokens * BAR_MAX))
        if t.role == "user":
            color = "cyan"
        elif t.role == "assistant":
            color = "green"
        else:
            color = "dim"

        bar = Text("█" * bar_len, style=color)
        table.add_row(
            str(t.turn),
            f"[{color}]{t.role}[/{color}]",
            f"{t.tokens:,}",
            f"{t.cumulative:,}",
            bar,
            Text(t.content_preview, overflow="ellipsis"),
        )

    console.print(table)
    if top:
        console.print(f"[dim]Showing top {top} heaviest turns. Omit --top to see all.[/dim]")


# ---------------------------------------------------------------------------
# cf tag
# ---------------------------------------------------------------------------

@app.command()
def tag(
    session_id: str,
    tag_name: str,
):
    """Add a tag to a session."""
    cfg = _get_config()
    database = _get_db(cfg)
    row = db_module.get_session(database, session_id)
    if row is None:
        err_console.print(f"[red]Session not found:[/red] {session_id}")
        raise typer.Exit(1)
    existing = json.loads(row.get("tags") or "[]")
    if tag_name not in existing:
        existing.append(tag_name)
        database["sessions"].update(session_id, {"tags": json.dumps(existing)})
    console.print(f"[green]Tagged[/green] {session_id[:16]} with [bold]{tag_name}[/bold]")


# ---------------------------------------------------------------------------
# cf config
# ---------------------------------------------------------------------------

@config_app.command(name="show")
def config_show():
    """Print active configuration."""
    cfg = _get_config()
    console.print_json(cfg.model_dump_json(indent=2))


@config_app.command(name="set")
def config_set(key: str, value: str):
    """Set a configuration value (not yet persisted — edit ~/.contextforge/config.toml)."""
    err_console.print(
        "[yellow]Tip:[/yellow] Edit [bold]~/.contextforge/config.toml[/bold] directly to persist settings."
    )
    console.print(f"Would set [bold]{key}[/bold] = [bold]{value}[/bold]")


# ---------------------------------------------------------------------------
# cf refresh
# ---------------------------------------------------------------------------

@app.command()
def refresh(
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
):
    """Refresh token counts and metadata for all sessions.

    Recalculates token_count for every session from source data.
    Useful after adapter updates or schema changes.
    """
    cfg = _get_config()
    database = _get_db(cfg)

    from contextforge.adapters.registry import get_adapter

    # Get all sessions grouped by tool
    sessions_by_tool = {}
    for row in database.execute("SELECT DISTINCT tool FROM sessions ORDER BY tool").fetchall():
        tool = row[0]
        sessions_by_tool[tool] = []
        for s in database.execute("SELECT id, title FROM sessions WHERE tool = ? ORDER BY id", [tool]).fetchall():
            sessions_by_tool[tool].append((s[0], s[1]))

    from rich.progress import Progress, SpinnerColumn, TextColumn

    total_sessions = sum(len(sessions) for sessions in sessions_by_tool.values())
    updated_count = 0
    error_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        disable=quiet,
    ) as progress:
        task = progress.add_task("Refreshing...", total=total_sessions)

        for tool, sessions in sessions_by_tool.items():
            if not sessions:
                continue

            try:
                adapter = get_adapter(tool)
            except Exception as e:
                if not quiet:
                    err_console.print(f"[yellow]Warning:[/yellow] Skipping {tool}: {e}")
                error_count += len(sessions)
                progress.advance(task, len(sessions))
                continue

            for session_id, title in sessions:
                try:
                    token_count = adapter._count_session_tokens(session_id)
                    database["sessions"].update(session_id, {"token_count": token_count})
                    updated_count += 1
                    if not quiet:
                        progress.update(task, description=f"Refreshed {session_id[:12]} ({token_count:,} tokens)")
                except Exception as e:
                    error_count += 1
                    if not quiet:
                        err_console.print(f"[yellow]Warning:[/yellow] {session_id}: {e}")
                finally:
                    progress.advance(task)

    if not quiet:
        console.print(
            f"[green]Refresh complete:[/green] "
            f"{updated_count} updated" + (f", {error_count} errors" if error_count else "")
        )


# ---------------------------------------------------------------------------
# cf mcp
# ---------------------------------------------------------------------------

@app.command(name="mcp")
def mcp_server():
    """Start the ContextForge MCP server (stdio transport).

    Exposes session data and token analysis as MCP tools so LLM agents
    can query token usage, list sessions, and pull analytics directly.

    Add to your Claude Code MCP config:
      {
        "mcpServers": {
          "contextforge": {
            "command": "cf",
            "args": ["mcp"]
          }
        }
      }
    """
    from contextforge.mcp_server import main as _run_mcp
    _run_mcp()


# ---------------------------------------------------------------------------
# cf dashboard
# ---------------------------------------------------------------------------

@app.command()
def dashboard():
    """Launch the Textual TUI dashboard.

    Key bindings:
      r  — rescan sessions       s  — summarize selected
      t  — transfer (modal)      c  — compact selected
      x  — token analysis        q  — quit
    """
    cfg = _get_config()
    from contextforge.tui.app import ContextForgeApp
    ContextForgeApp(db_path=cfg.db_path).run()
