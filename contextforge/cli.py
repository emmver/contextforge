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
from contextforge.core.summarizer import summarize_session
from contextforge.models.config import ForgeConfig
from contextforge.utils.display import console, err_console, sessions_table

app = typer.Typer(
    name="cf",
    help="ContextForge — session manager and context bridge for agentic CLI tools.",
    add_completion=False,
    no_args_is_help=True,
)

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
):
    """Discover and index sessions from all installed agentic tools."""
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
        rows = db_module.get_sessions(database, limit=1000)
        pending = [r for r in rows if not r.get("summary") or force]
        console.print(f"Summarizing {len(pending)} sessions...")
        for i, row in enumerate(pending, 1):
            sid = row["id"]
            summary = summarize_session(database, sid, cfg, force=force)
            status = "[green]ok[/green]" if summary else "[yellow]skipped[/yellow]"
            console.print(f"  [{i}/{len(pending)}] {sid[:16]} {status}")
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

    console.print(Panel(
        f"Target tool:  {target_tool}\n"
        f"Method:       {actual_method}\n"
        f"Bundle tokens: {bundle.token_count}\n\n"
        f"[bold]Command:[/bold]\n[cyan]{cmd}[/cyan]",
        title="Transfer Preview",
    ))

    if execute:
        bundle_id = db_module.save_bundle(database, bundle)
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
    else:
        console.print("[dim]Dry run. Pass [bold]--execute[/bold] to launch.[/dim]")


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
# cf dashboard
# ---------------------------------------------------------------------------

@app.command()
def dashboard():
    """Launch the Textual TUI dashboard."""
    try:
        from contextforge.tui.app import ContextForgeApp
        ContextForgeApp().run()
    except ImportError:
        err_console.print(
            "[red]Textual is required for the dashboard.[/red] "
            "Install it with: [bold]uv add textual[/bold]"
        )
        raise typer.Exit(1)
