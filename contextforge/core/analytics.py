"""Analytics aggregations for the ContextForge TUI dashboard."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite_utils


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class OverviewStats:
    total_sessions: int
    total_tokens: int
    active_tools: list[str]
    date_range: tuple[datetime, datetime] | None
    session_count_by_tool: dict[str, int]
    token_sum_by_tool: dict[str, int]


@dataclass
class ActivityBucket:
    label: str
    count: int
    tokens: int


@dataclass
class ProjectStats:
    project: str
    count: int


# ── Internal helpers ──────────────────────────────────────────────────────────


def _window_start_ms(window: str) -> int:
    """Return epoch-ms for the start of *window* relative to now."""
    now = datetime.now(timezone.utc)
    if window == "7d":
        delta = timedelta(days=7)
    elif window == "30d":
        delta = timedelta(days=30)
    elif window == "6m":
        # Approximate 6 months as 183 days
        delta = timedelta(days=183)
    elif window == "1y":
        delta = timedelta(days=365)
    else:
        delta = timedelta(days=30)
    start = now - delta
    return int(start.timestamp() * 1000)


def _bucket_key(updated_at_ms: int, window: str) -> str:
    """Return the period label for a given ms timestamp and window."""
    try:
        dt = datetime.fromtimestamp(updated_at_ms / 1000, tz=timezone.utc)
    except Exception:
        return "?"
    if window in ("7d", "30d"):
        return dt.strftime("%Y-%m-%d")
    elif window == "6m":
        return dt.strftime("%Y-W%W")
    else:  # 1y
        return dt.strftime("%Y-%m")


def _all_bucket_labels(window: str) -> list[str]:
    """Return the ordered list of all period labels for the window (including zeros)."""
    now = datetime.now(timezone.utc)
    labels: list[str] = []

    if window == "7d":
        for i in range(6, -1, -1):
            d = now - timedelta(days=i)
            labels.append(d.strftime("%Y-%m-%d"))
    elif window == "30d":
        for i in range(29, -1, -1):
            d = now - timedelta(days=i)
            labels.append(d.strftime("%Y-%m-%d"))
    elif window == "6m":
        # ~26 ISO weeks
        for i in range(25, -1, -1):
            d = now - timedelta(weeks=i)
            labels.append(d.strftime("%Y-W%W"))
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for lbl in labels:
            if lbl not in seen:
                seen.add(lbl)
                unique.append(lbl)
        labels = unique
    else:  # 1y
        # 12 months backwards
        for i in range(11, -1, -1):
            # Subtract i months
            month = now.month - i
            year = now.year
            while month <= 0:
                month += 12
                year -= 1
            labels.append(f"{year:04d}-{month:02d}")

    return labels


# ── Public API ────────────────────────────────────────────────────────────────


def get_overview(
    db: "sqlite_utils.Database",
    window: str = "30d",
) -> OverviewStats:
    """Return high-level aggregates for the given time window."""
    start_ms = _window_start_ms(window)

    rows = list(db.execute(
        "SELECT tool, COUNT(*) as cnt, COALESCE(SUM(token_count), 0) as tok "
        "FROM sessions WHERE updated_at >= ? GROUP BY tool",
        [start_ms],
    ).fetchall())

    session_count_by_tool: dict[str, int] = {}
    token_sum_by_tool: dict[str, int] = {}
    total_sessions = 0
    total_tokens = 0

    for tool, cnt, tok in rows:
        session_count_by_tool[tool] = cnt
        token_sum_by_tool[tool] = tok
        total_sessions += cnt
        total_tokens += tok

    active_tools = [t for t, c in session_count_by_tool.items() if c > 0]

    # Date range
    range_row = db.execute(
        "SELECT MIN(updated_at), MAX(updated_at) FROM sessions WHERE updated_at >= ?",
        [start_ms],
    ).fetchone()
    date_range: tuple[datetime, datetime] | None = None
    if range_row and range_row[0] and range_row[1]:
        try:
            earliest = datetime.fromtimestamp(range_row[0] / 1000, tz=timezone.utc)
            latest = datetime.fromtimestamp(range_row[1] / 1000, tz=timezone.utc)
            date_range = (earliest, latest)
        except Exception:
            pass

    return OverviewStats(
        total_sessions=total_sessions,
        total_tokens=total_tokens,
        active_tools=active_tools,
        date_range=date_range,
        session_count_by_tool=session_count_by_tool,
        token_sum_by_tool=token_sum_by_tool,
    )


def get_activity_over_time(
    db: "sqlite_utils.Database",
    window: str = "30d",
) -> list[ActivityBucket]:
    """Return ordered activity buckets for the sparkline. Zero-count buckets included."""
    start_ms = _window_start_ms(window)

    data_rows = list(db.execute(
        "SELECT updated_at, COALESCE(token_count, 0) FROM sessions WHERE updated_at >= ?",
        [start_ms],
    ).fetchall())

    # Aggregate into buckets
    bucket_counts: dict[str, int] = {}
    bucket_tokens: dict[str, int] = {}
    for updated_ms, tok in data_rows:
        key = _bucket_key(updated_ms, window)
        bucket_counts[key] = bucket_counts.get(key, 0) + 1
        bucket_tokens[key] = bucket_tokens.get(key, 0) + tok

    # Fill all period labels (including zeros)
    labels = _all_bucket_labels(window)
    return [
        ActivityBucket(
            label=lbl,
            count=bucket_counts.get(lbl, 0),
            tokens=bucket_tokens.get(lbl, 0),
        )
        for lbl in labels
    ]


def get_top_projects(
    db: "sqlite_utils.Database",
    window: str = "30d",
    top_n: int = 5,
) -> list[ProjectStats]:
    """Return top N projects by session count within the window."""
    start_ms = _window_start_ms(window)

    rows = list(db.execute(
        "SELECT cwd, COUNT(*) as cnt FROM sessions "
        "WHERE updated_at >= ? AND cwd IS NOT NULL AND cwd != '' "
        "GROUP BY cwd ORDER BY cnt DESC LIMIT ?",
        [start_ms, top_n],
    ).fetchall())

    result: list[ProjectStats] = []
    for cwd, cnt in rows:
        # Use last 2 path components as display name
        parts = [p for p in cwd.replace("\\", "/").split("/") if p]
        if len(parts) >= 2:
            project = "/".join(parts[-2:])
        elif parts:
            project = parts[-1]
        else:
            project = cwd
        result.append(ProjectStats(project=project, count=cnt))

    return result
