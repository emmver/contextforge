"""ContextForge MCP server — exposes token analysis and session tools to LLM agents."""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from contextforge.core import analytics as analytics_module
from contextforge.core import db as db_module
from contextforge.core.token_analyzer import analyze_tokens
from contextforge.models.config import ForgeConfig

mcp = FastMCP(
    "contextforge",
    instructions=(
        "ContextForge provides read-only access to indexed agentic-tool sessions "
        "and their token usage. Start with list_sessions to discover what's available "
        "(supports pagination via offset/limit and date filtering via window). "
        "Use get_session_tokens for token stats — pass include_turns=True with "
        "turns_limit/turns_offset to page through per-turn data without blowing up "
        "the context window. Use get_token_analytics for aggregated stats across all tools."
    ),
)

_VALID_WINDOWS = {"7d", "30d", "6m", "1y"}


def _db():
    cfg = ForgeConfig()
    return db_module.get_db(cfg.db_path)


def _short_cwd(cwd: str) -> str:
    """Return a short project label from a full cwd path.

    Returns the last path component, or '~' for the user home directory,
    or '' for empty paths.
    """
    if not cwd:
        return ""
    parts = [p for p in cwd.replace("\\", "/").split("/") if p]
    if not parts:
        return ""
    # Detect home directory: last part is a username-like segment with no parent project
    # If the path ends at /Users/<name> or /home/<name>, show '~'
    if len(parts) >= 2 and parts[-2] in ("Users", "home"):
        return "~"
    return parts[-1]


def _validate_window(window: str) -> str:
    if window not in _VALID_WINDOWS:
        raise ValueError(
            f"Invalid window {window!r}. Must be one of: {', '.join(sorted(_VALID_WINDOWS))}"
        )
    return window


@mcp.tool()
def list_sessions(
    tool: str | None = None,
    limit: int = 50,
    offset: int = 0,
    window: str | None = None,
) -> list[dict[str, Any]]:
    """List indexed sessions with basic token summaries.

    Supports pagination via offset/limit and optional date filtering via window.

    Args:
        tool: Filter by tool name (claude_code, codex, gemini, etc.). Omit for all tools.
        limit: Page size (default 50, max 200).
        offset: Number of sessions to skip for pagination (default 0).
        window: Optional time window to filter by recency — one of "7d", "30d", "6m", "1y".
    """
    since_ms: int | None = None
    if window is not None:
        _validate_window(window)
        since_ms = analytics_module._window_start_ms(window)

    db = _db()
    rows = db_module.get_sessions(
        db, tool=tool, limit=min(limit, 200), offset=offset, since_ms=since_ms
    )
    return [
        {
            "id": r["id"],
            "tool": r["tool"],
            "title": (r.get("title") or "")[:100],
            "cwd": _short_cwd(r.get("cwd") or ""),
            "token_count": r.get("token_count") or 0,
            "updated_at": r.get("updated_at"),
            "status": r.get("status") or "",
        }
        for r in rows
    ]


@mcp.tool()
def get_session(session_id: str) -> dict[str, Any] | None:
    """Get details for a single session by ID.

    Args:
        session_id: The exact session ID from list_sessions.
    """
    db = _db()
    row = db_module.get_session(db, session_id)
    if row is None:
        return None
    return {
        "id": row["id"],
        "tool": row["tool"],
        "title": (row.get("title") or "")[:100],
        "cwd": row.get("cwd") or "",
        "project": _short_cwd(row.get("cwd") or ""),
        "token_count": row.get("token_count") or 0,
        "status": row.get("status") or "",
        "summary": (row.get("summary") or "")[:300],
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "tags": row.get("tags") or "[]",
    }


@mcp.tool()
def get_session_tokens(
    session_id: str,
    include_turns: bool = False,
    turns_limit: int = 100,
    turns_offset: int = 0,
    top: int = 0,
) -> dict[str, Any] | None:
    """Get token breakdown for a session.

    By default returns only summary statistics (cheap). Set include_turns=True
    to also page through per-turn data using turns_limit and turns_offset.

    Args:
        session_id: The session ID.
        include_turns: If True, include per-turn token counts. Default False.
        turns_limit: Max turns to return per page when include_turns=True (default 100).
        turns_offset: Turn index to start from for pagination (default 0).
        top: If > 0, return the N heaviest turns instead of sequential pagination.
            Overrides turns_offset when set.
    """
    db = _db()
    report = analyze_tokens(db, session_id)
    if report is None:
        return None

    result: dict[str, Any] = {
        "session_id": report.session_id,
        "tool": report.tool,
        "title": report.title[:100],
        "total_tokens": report.total,
        "user_tokens": report.user_total,
        "assistant_tokens": report.assistant_total,
        "tool_call_tokens": report.tool_call_total,
        "tool_result_tokens": report.tool_result_total,
        "turn_count": report.turn_count,
        "avg_user_tokens": round(report.avg_user, 1),
        "avg_assistant_tokens": round(report.avg_assistant, 1),
        "has_tool_data": report.has_tool_data,
        "heaviest_turn": (
            {
                "turn": report.max_turn.turn,
                "role": report.max_turn.role,
                "tokens": report.max_turn.tokens,
            }
            if report.max_turn
            else None
        ),
    }

    if include_turns:
        turns = report.turns
        if top > 0:
            turns = sorted(turns, key=lambda t: t.tokens, reverse=True)[:top]
            turns = sorted(turns, key=lambda t: t.turn)
        else:
            turns = turns[turns_offset: turns_offset + turns_limit]
        result["turns"] = [
            {
                "turn": t.turn,
                "role": t.role,
                "tokens": t.tokens,
                "text_tokens": t.text_tokens,
                "tool_call_tokens": t.tool_call_tokens,
                "tool_result_tokens": t.tool_result_tokens,
            }
            for t in turns
        ]
        if top > 0:
            result["turns_pagination"] = {
                "mode": "top",
                "n": top,
                "total": report.turn_count,
            }
        else:
            result["turns_pagination"] = {
                "mode": "sequential",
                "offset": turns_offset,
                "limit": turns_limit,
                "total": report.turn_count,
                "has_more": (turns_offset + turns_limit) < report.turn_count,
            }

    return result


@mcp.tool()
def get_token_analytics(window: str = "30d") -> dict[str, Any]:
    """Get aggregated token usage statistics across all sessions.

    Args:
        window: Time window — one of "7d", "30d", "6m", "1y" (default "30d").
    """
    _validate_window(window)
    db = _db()
    overview = analytics_module.get_overview(db, window=window)
    return {
        "window": window,
        "total_sessions": overview.total_sessions,
        "total_tokens": overview.total_tokens,
        "active_tools": overview.active_tools,
        "session_count_by_tool": overview.session_count_by_tool,
        "token_sum_by_tool": overview.token_sum_by_tool,
        "date_range": {
            "start": overview.date_range[0].isoformat() if overview.date_range else None,
            "end": overview.date_range[1].isoformat() if overview.date_range else None,
        },
    }


@mcp.tool()
def get_activity_timeline(window: str = "30d") -> list[dict[str, Any]]:
    """Get session count and token usage bucketed over time.

    Returns one bucket per day (7d/30d), per week (6m), or per month (1y).

    Args:
        window: Time window — one of "7d", "30d", "6m", "1y" (default "30d").
    """
    _validate_window(window)
    db = _db()
    buckets = analytics_module.get_activity_over_time(db, window=window)
    return [
        {"label": b.label, "sessions": b.count, "tokens": b.tokens}
        for b in buckets
    ]


@mcp.tool()
def get_top_projects(window: str = "30d", top_n: int = 5) -> list[dict[str, Any]]:
    """Get top projects by session count within a time window.

    Args:
        window: Time window — one of "7d", "30d", "6m", "1y" (default "30d").
        top_n: Number of top projects to return (default 5).
    """
    _validate_window(window)
    db = _db()
    projects = analytics_module.get_top_projects(db, window=window, top_n=top_n)
    return [{"project": p.project, "session_count": p.count} for p in projects]


@mcp.tool()
def compare_sessions(session_ids: list[str]) -> list[dict[str, Any]]:
    """Compare token usage across multiple sessions side-by-side.

    Returns one entry per session with key metrics, sorted by total tokens descending.

    Args:
        session_ids: List of session IDs to compare.
    """
    db = _db()
    results: list[dict[str, Any]] = []
    for sid in session_ids:
        report = analyze_tokens(db, sid)
        if report is None:
            results.append({"session_id": sid, "error": "not found"})
            continue
        results.append({
            "session_id": sid,
            "tool": report.tool,
            "title": report.title[:100],
            "total_tokens": report.total,
            "turn_count": report.turn_count,
            "user_tokens": report.user_total,
            "assistant_tokens": report.assistant_total,
            "tool_call_tokens": report.tool_call_total,
            "tool_result_tokens": report.tool_result_total,
            "avg_tokens_per_turn": (
                round(report.total / report.turn_count, 1) if report.turn_count else 0
            ),
            "heaviest_turn_tokens": report.max_turn.tokens if report.max_turn else 0,
        })
    results.sort(key=lambda r: r.get("total_tokens", 0), reverse=True)
    return results


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
