"""Tests for utils/display.py — escaping, title cleaning, sub-agent helpers."""
from __future__ import annotations

import json

from rich.console import Console
from rich.markup import escape as _escape  # noqa: F401  (import sanity)

from contextforge.utils.display import (
    _clean_title,
    is_subagent,
    parent_id_of,
    row_tags,
    sessions_table,
)

# The exact crash payload class from the codex session: bracket sequences that
# Rich would otherwise parse as markup ([/ηλικίας/...] closed a fictional tag).
HOSTILE_TITLE = (
    "Είσαι [/ηλικίας/γεωγραφίας. [bold]ερέυνα[/bold] και άμεσες[/ηλικίας] εργασίες."
)


def test_clean_title_first_line_and_truncation() -> None:
    multi = "first line\nsecond [bold] line\nthird"
    out = _clean_title(multi, max_len=20)
    assert out == "first line"
    assert "[" not in out  # brackets from later lines are cut, not interpreted
    long = "x" * 100
    assert _clean_title(long, max_len=10) == "x" * 9 + "…"


def test_row_tags_parses_and_survives_corruption() -> None:
    assert row_tags({"tags": json.dumps(["subagent", "parent:a1"])}) == ["subagent", "parent:a1"]
    assert row_tags({"tags": "not json at all"}) == []
    assert row_tags({}) == []
    assert row_tags({"tags": json.dumps("scalar")}) == []


def test_is_subagent_and_parent_id() -> None:
    sub = {"tags": json.dumps(["subagent", "parent:01a11552-b234"])}
    task = {"tags": json.dumps(["subagent", "workspace:111111111111"])}
    main = {"tags": json.dumps([])}
    assert is_subagent(sub) and is_subagent(task)
    assert not is_subagent(main)
    assert parent_id_of(sub) == "01a11552-b234"
    assert parent_id_of(task) == "111111111111"
    assert parent_id_of(main) is None


def test_sessions_table_escapes_hostile_titles() -> None:
    """Rendering a table with markup-like titles must not raise MarkupError."""
    rows = [
        {"id": "s1" * 8, "tool": "pi", "title": HOSTILE_TITLE, "cwd": "/proj",
         "token_count": 10, "updated_at": "2026-01-15T12:30:00+00:00",
         "tags": "[]", "summary": "Says [bold]hi[/bold] with [/weird/brackets."},
    ]
    table = sessions_table(rows)
    console = Console(record=True, width=200)
    console.print(table)  # must not raise MarkupError
    exported = console.export_text()
    assert "❰/ηλικίας" in exported  # brackets transliterated, shown literally
    assert "Says ❰bold❱hi❰/bold❱ with ❰/weird/brackets." in exported


def test_sessions_table_marks_subagent_rows() -> None:
    rows = [
        {"id": "a" * 32, "tool": "pi", "title": "Child run", "cwd": "/proj",
         "token_count": 5, "updated_at": "2026-01-15T12:31:00+00:00",
         "tags": json.dumps(["subagent", "parent:01a11552"]), "summary": ""},
        {"id": "b" * 32, "tool": "pi", "title": "Parent chat", "cwd": "/proj",
         "token_count": 9, "updated_at": "2026-01-15T12:30:00+00:00",
         "tags": "[]", "summary": ""},
    ]
    table = sessions_table(rows)
    console = Console(record=True, width=200)
    console.print(table)
    exported = console.export_text()
    assert "↳ Child run" in exported
    assert "Parent chat" in exported
    assert "↳ Parent chat" not in exported  # parent rows unmarked