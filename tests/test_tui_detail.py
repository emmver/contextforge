"""Regression tests for the TUI session-detail rendering.

Covers the MarkupError crash when a session title (untrusted content, e.g. a
codex session whose title is a whole raw JSON payload) contains Rich-style
bracket sequences.  Uses Textual's pilot harness against a real DB.
"""
from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static

from contextforge.core.db import get_db, upsert_session
from contextforge.models.session import Message, Session
from contextforge.tui.widgets.session_detail import SessionDetail

HOSTILE_TITLE = (
    "Είσαι ο βοηθός [/ηλικίας/γεωγραφίας. Περιλαμβάνει [bold]πρόθεση[/bold], "
    "[dim]εκτίμηση[/] με εύρη, εύρος και συσπειρώσεις."
)

SUBAGENT_ID = "0" * 32
PARENT_ID = "1" * 32


def _seed(db_path: Path) -> None:
    now = datetime(2026, 1, 15, 12, 30, tzinfo=timezone.utc)
    db = get_db(db_path)
    upsert_session(
        db,
        Session(
            id=PARENT_ID,
            tool="codex",
            title=HOSTILE_TITLE,
            cwd="/home/mver/Documents/Github/datatzis",
            created_at=now,
            updated_at=now,
            messages=[Message(role="user", content="hello")],
            status="unknown",
        ),
    )
    upsert_session(
        db,
        Session(
            id=SUBAGENT_ID,
            tool="pi",
            title="↳-safe plain child title",
            cwd="/home/mver/Documents/Github/datatzis",
            created_at=now,
            updated_at=now,
            messages=[Message(role="user", content="child")],
            status="unknown",
            tags=["subagent", f"parent:{PARENT_ID}"],
        ),
    )


class _HarnessApp(App):
    def __init__(self, db_path: Path) -> None:
        super().__init__()
        self.db_path = db_path  # SessionDetail.load passes this to get_db()

    def compose(self) -> ComposeResult:
        yield SessionDetail(id="session-detail")


def _run(fn) -> None:
    asyncio.run(fn())


def test_detail_load_hostile_title_does_not_crash():
    """The exact codex-session case: markup-like title must render escaped."""

    async def scenario():
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _seed(db_path)
            app = _HarnessApp(db_path)
            async with app.run_test() as pilot:
                detail = app.query_one(SessionDetail)
                detail.load(PARENT_ID)
                await pilot.pause()
                title_widget = detail.query_one("#detail-title", Static)
                plain = title_widget.render().plain  # visible text after sanitizing
                # Brackets transliterated; markup engine has nothing to consume.
                assert "❰/ηλικίας" in plain
                assert "❰bold❱πρόθεση❰/bold❱" in plain
                detail.load(SUBAGENT_ID)
                await pilot.pause()
                parent_widget = detail.query_one("#detail-parent", Static)
                assert parent_widget.display is True
                assert PARENT_ID in parent_widget.render().plain

    _run(scenario)


def test_detail_parent_hidden_for_main_sessions():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _seed(db_path)
            app = _HarnessApp(db_path)
            async with app.run_test() as pilot:
                detail = app.query_one(SessionDetail)
                detail.load(PARENT_ID)  # main codex session — no subagent tags
                await pilot.pause()
                parent_widget = detail.query_one("#detail-parent", Static)
                assert parent_widget.display is False
                detail.clear()
                await pilot.pause()
                assert parent_widget.display is False

    _run(scenario)