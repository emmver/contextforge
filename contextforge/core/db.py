"""SQLite schema, migrations, and CRUD helpers for ContextForge."""
from __future__ import annotations

import json
import time
from pathlib import Path

import sqlite_utils

from contextforge.models.session import ContextBundle, Session

_SCHEMA_VERSION = 1


def get_db(db_path: Path) -> sqlite_utils.Database:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite_utils.Database(db_path)
    _migrate(db)
    return db


def _migrate(db: sqlite_utils.Database) -> None:
    if "schema_version" not in db.table_names():
        db["schema_version"].insert({"version": 0})

    row = next(db["schema_version"].rows)
    version = row["version"]

    if version < 1:
        db["sessions"].create(
            {
                "id": str,
                "tool": str,
                "title": str,
                "cwd": str,
                "created_at": int,
                "updated_at": int,
                "first_message": str,
                "token_count": int,
                "raw_path": str,
                "status": str,
                "summary": str,
                "summary_updated_at": int,
                "tags": str,
            },
            pk="id",
            if_not_exists=True,
        )
        db["context_bundles"].create(
            {
                "id": int,
                "name": str,
                "created_at": int,
                "source_sessions": str,
                "compacted_text": str,
                "token_count": int,
                "strategy": str,
                "target_tool": str,
            },
            pk="id",
            if_not_exists=True,
        )
        db["transfers"].create(
            {
                "id": int,
                "bundle_id": int,
                "target_tool": str,
                "target_session_id": str,
                "injected_at": int,
                "method": str,
                "command_used": str,
            },
            pk="id",
            if_not_exists=True,
        )
        db["schema_version"].update(1, {"version": 1})


def upsert_session(db: sqlite_utils.Database, session: Session) -> None:
    existing = list(db["sessions"].rows_where("id = ?", [session.id]))
    if existing:
        existing_updated = existing[0]["updated_at"]
        new_updated = int(session.updated_at.timestamp() * 1000)
        if existing_updated >= new_updated:
            return

    first_msg = ""
    if session.messages:
        for m in session.messages:
            if m.role == "user":
                first_msg = m.content[:200]
                break

    db["sessions"].upsert(
        {
            "id": session.id,
            "tool": session.tool,
            "title": session.title,
            "cwd": session.cwd,
            "created_at": int(session.created_at.timestamp() * 1000),
            "updated_at": int(session.updated_at.timestamp() * 1000),
            "first_message": first_msg,
            "token_count": session.token_count,
            "raw_path": session.raw_path,
            "status": session.status,
            "summary": session.summary,
            "summary_updated_at": None,
            "tags": json.dumps(session.tags),
        },
        pk="id",
    )


def get_sessions(
    db: sqlite_utils.Database,
    tool: str | None = None,
    limit: int = 200,
    offset: int = 0,
    since_ms: int | None = None,
) -> list[dict]:
    clauses: list[str] = []
    params: list = []
    if tool:
        clauses.append("tool = ?")
        params.append(tool)
    if since_ms is not None:
        clauses.append("updated_at >= ?")
        params.append(since_ms)
    where = " AND ".join(clauses) if clauses else None
    rows = db["sessions"].rows_where(
        where,
        params,
        order_by="updated_at desc",
        limit=limit,
        offset=offset,
    )
    return list(rows)


def get_session(db: sqlite_utils.Database, session_id: str) -> dict | None:
    rows = list(db["sessions"].rows_where("id = ?", [session_id]))
    return rows[0] if rows else None


def save_bundle(db: sqlite_utils.Database, bundle: ContextBundle) -> int:
    result = db["context_bundles"].insert(
        {
            "name": bundle.name,
            "created_at": int(time.time() * 1000),
            "source_sessions": json.dumps(bundle.source_sessions),
            "compacted_text": bundle.compacted_text,
            "token_count": bundle.token_count,
            "strategy": bundle.strategy,
            "target_tool": bundle.target_tool,
        }
    )
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def get_bundle(db: sqlite_utils.Database, bundle_id: int) -> dict | None:
    rows = list(db["context_bundles"].rows_where("id = ?", [bundle_id]))
    return rows[0] if rows else None


def save_transfer(
    db: sqlite_utils.Database,
    bundle_id: int,
    target_tool: str,
    method: str,
    command_used: str,
    target_session_id: str | None = None,
) -> None:
    db["transfers"].insert(
        {
            "bundle_id": bundle_id,
            "target_tool": target_tool,
            "target_session_id": target_session_id,
            "injected_at": int(time.time() * 1000),
            "method": method,
            "command_used": command_used,
        }
    )


def update_summary(
    db: sqlite_utils.Database, session_id: str, summary: str
) -> None:
    db["sessions"].update(
        session_id,
        {"summary": summary, "summary_updated_at": int(time.time() * 1000)},
    )
