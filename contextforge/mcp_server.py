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
        "and their token usage. Start with list_sessions to discover what's available, "
        "then use get_session_tokens for per-turn breakdown or get_token_analytics "
        "for aggregated stats across all tools."
    ),
)


def _db():
    cfg = ForgeConfig()
    return db_module.get_db(cfg.db_path)


@mcp.tool()
def list_sessions(tool: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """List indexed sessions with basic token summaries.

    Args:
        tool: Filter by tool name (claude_code, codex, gemini, etc.). Omit for all tools.
        limit: Maximum sessions to return (default 50, max 200).
    """
    db = _db()
    rows = db_module.get_sessions(db, tool=tool, limit=min(limit, 200))
    return [
        {
            "id": r["id"],
            "tool": r["tool"],
            "title": r.get("title") or "",
            "cwd": r.get("cwd") or "",
            "token_count": r.get("token_count") or 0,
            "updated_at": r.get("updated_at"),
            "status": r.get("status") or "",
            "summary": r.get("summary") or "",
        }
        for r in rows
    ]


@mcp.tool()
def get_session(session_id: str) -> dict[str, Any] | None:
    """Get full details for a single session by ID.

    Args:
        session_id: The exact session ID from list_sessions.
    """
    db = _db()
    row = db_module.get_session(db, session_id)
    return dict(row) if row else None


@mcp.tool()
def get_session_tokens(session_id: str, top: int = 0) -> dict[str, Any] | None:
    """Get per-turn token breakdown for a session.

    Returns total tokens, per-role totals and averages, and a list of turns
    with individual counts for text, tool calls, and tool results.

    Args:
        session_id: The session ID.
        top: If > 0, return only the N heaviest turns (sorted by turn number).
    """
    db = _db()
    report = analyze_tokens(db, session_id)
    if report is None:
        return None

    turns = report.turns
    if top > 0:
        turns = sorted(turns, key=lambda t: t.tokens, reverse=True)[:top]
        turns = sorted(turns, key=lambda t: t.turn)

    return {
        "session_id": report.session_id,
        "tool": report.tool,
        "title": report.title,
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
                "preview": report.max_turn.content_preview,
            }
            if report.max_turn
            else None
        ),
        "turns": [
            {
                "turn": t.turn,
                "role": t.role,
                "tokens": t.tokens,
                "text_tokens": t.text_tokens,
                "tool_call_tokens": t.tool_call_tokens,
                "tool_result_tokens": t.tool_result_tokens,
                "cumulative": t.cumulative,
                "preview": t.content_preview,
            }
            for t in turns
        ],
    }


@mcp.tool()
def get_token_analytics(window: str = "30d") -> dict[str, Any]:
    """Get aggregated token usage statistics across all sessions.

    Args:
        window: Time window — one of "7d", "30d", "6m", "1y" (default "30d").
    """
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
    db = _db()
    projects = analytics_module.get_top_projects(db, window=window, top_n=top_n)
    return [{"project": p.project, "session_count": p.count} for p in projects]


@mcp.tool()
def compare_sessions(session_ids: list[str]) -> list[dict[str, Any]]:
    """Compare token usage across multiple sessions side-by-side.

    Returns one entry per session with key metrics, sorted by total tokens descending.

    Args:
        session_ids: List of session IDs to compare (2–10 sessions recommended).
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
            "title": report.title,
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
