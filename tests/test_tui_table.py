"""Regression tests for the SessionTable hierarchy: parent sessions first,
sub-agent rows nested under their expanded parent."""
from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import DataTable

from contextforge.core.db import get_db, upsert_session
from contextforge.models.session import Message, Session
from contextforge.tui.widgets.session_table import SessionTable

PARENT_ID = "1" * 32
CHILD_ID = "2" * 32
OTHER_ID = "3" * 32


def _seed(db_path: Path) -> None:
    now = datetime(2026, 1, 15, 12, 30, tzinfo=timezone.utc)
    db = get_db(db_path)
    upsert_session(db, Session(
        id=PARENT_ID, tool="pi", title="Parent chat", cwd="/proj",
        created_at=now, updated_at=now,
        messages=[Message(role="user", content="parent")],
    ))
    upsert_session(db, Session(
        id=CHILD_ID, tool="pi", title="Child worker run", cwd="/proj",
        created_at=now, updated_at=now,
        messages=[Message(role="user", content="child")],
        tags=["subagent", f"parent:{PARENT_ID}"],
    ))
    upsert_session(db, Session(
        id=OTHER_ID, tool="codex", title="Another parent chat", cwd="/proj2",
        created_at=now, updated_at=now,
        messages=[Message(role="user", content="other")],
    ))


class _HarnessApp(App):
    def __init__(self, db_path: Path) -> None:
        super().__init__()
        self.db_path = db_path

    def compose(self) -> ComposeResult:
        yield SessionTable()


def test_session_table_parents_first_and_expandable():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _seed(db_path)
            app = _HarnessApp(db_path)
            async with app.run_test() as pilot:
                table = app.query_one(SessionTable)
                await pilot.pause()
                dt = table.query_one(DataTable)

                # At first glance: parent sessions only, hidden children
                assert len(table._display_rows) == 2
                assert [r["id"][:1] for r in table._display_rows] == ["1", "3"]

                # Selecting (entering) the parent expands its children in place
                table.toggle_expansion(PARENT_ID)
                await pilot.pause()
                assert len(table._display_rows) == 3
                assert table._display_rows[0]["id"] == PARENT_ID
                assert table._display_rows[1]["id"] == CHILD_ID
                assert table._display_rows[1]["_is_child"] is True
                assert table._display_rows[2]["id"] == OTHER_ID

                # Toggling again collapses
                table.toggle_expansion(PARENT_ID)
                await pilot.pause()
                assert len(table._display_rows) == 2

    asyncio.run(scenario())


def test_session_table_child_match_surfaces_parent():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _seed(db_path)
            app = _HarnessApp(db_path)
            async with app.run_test() as pilot:
                table = app.query_one(SessionTable)
                await pilot.pause()

                # Text filter matching only the CHILD surfaces its parent
                table._filter_text = "worker"
                table._apply_filter()
                await pilot.pause()
                ids = [r["id"] for r in table._display_rows]
                assert ids == [PARENT_ID]

    asyncio.run(scenario())


def test_session_table_expansion_survives_filter_toggle():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _seed(db_path)
            app = _HarnessApp(db_path)
            async with app.run_test() as pilot:
                table = app.query_one(SessionTable)
                await pilot.pause()
                table.toggle_expansion(PARENT_ID)
                await pilot.pause()

                table._filter_tool = "codex"
                table._apply_filter()
                await pilot.pause()
                assert len(table._display_rows) == 1  # only the codex parent

                table._filter_tool = None
                table._apply_filter()
                await pilot.pause()
                # Expansion state persists across filter changes
                assert len(table._display_rows) == 3

    asyncio.run(scenario())