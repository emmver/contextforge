"""Session discovery and indexing across all registered adapters."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import sqlite_utils
from rich.progress import Progress, SpinnerColumn, TextColumn

from contextforge.adapters.registry import get_available_adapters
from contextforge.core.db import upsert_session
from contextforge.models.session import Session


@dataclass
class ScanResult:
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)
    sessions: list[Session] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.new + self.updated + self.unchanged


def scan(db: sqlite_utils.Database, quiet: bool = False) -> ScanResult:
    result = ScanResult()
    adapters = get_available_adapters()

    if not adapters:
        result.errors.append("No supported agentic tools found on this machine.")
        return result

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        disable=quiet,
    ) as progress:
        for adapter in adapters:
            task = progress.add_task(f"Scanning {adapter.tool_name}...", total=None)
            try:
                sessions = adapter.discover_sessions()
            except Exception as exc:
                result.errors.append(f"{adapter.tool_name}: {exc}")
                progress.remove_task(task)
                continue

            for session in sessions:
                try:
                    existing = list(
                        db["sessions"].rows_where("id = ?", [session.id])
                    )
                    before_count = len(existing)
                    upsert_session(db, session)
                    after = list(db["sessions"].rows_where("id = ?", [session.id]))

                    if before_count == 0:
                        result.new += 1
                    elif existing[0]["updated_at"] < int(session.updated_at.timestamp() * 1000):
                        result.updated += 1
                    else:
                        result.unchanged += 1
                    result.sessions.append(session)
                except Exception as exc:
                    result.errors.append(f"{session.id}: {exc}")

            progress.update(task, description=f"[green]{adapter.tool_name}: {len(sessions)} sessions")
            progress.remove_task(task)

    return result
