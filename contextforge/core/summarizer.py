"""LLM-based session summarization with caching."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import sqlite_utils

from contextforge.adapters.registry import get_adapter
from contextforge.core.db import get_session, get_sessions, update_summary
from contextforge.models.config import ForgeConfig
from contextforge.utils.tokens import count_tokens, truncate_to_budget

_SUMMARY_PROMPT = """\
You are summarizing a coding session for a developer. Read the conversation below and
write a 3–5 sentence summary covering:
- What was being built or investigated
- Key decisions made or problems solved
- Files and components touched
- Current state / what remains to be done

Be concise. Under 150 words. Plain prose, no bullet points.

--- SESSION ---
{transcript}
--- END SESSION ---

Summary:"""


@dataclass
class BatchSummaryResult:
    summarized: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def summarize_session(
    db: sqlite_utils.Database,
    session_id: str,
    config: ForgeConfig,
    force: bool = False,
) -> str | None:
    """Generate (or return cached) summary for a single session.

    Priority order:
    1. Cached summary in DB (unless force=True)
    2. Tool-native rollup summary (e.g. Codex stage1_outputs) — zero LLM cost
    3. LLM-generated summary (requires ANTHROPIC_API_KEY)
    4. Graceful degradation: first user message preview
    """
    row = get_session(db, session_id)
    if row is None:
        return None

    # Return cached summary unless force-refresh
    if row.get("summary") and not force:
        return row["summary"]

    # Check for tool-native rollup summary first (e.g. Codex pre-computed)
    try:
        adapter = get_adapter(row["tool"])
        rollup = adapter.get_rollup_summary(session_id)
        if rollup:
            update_summary(db, session_id, rollup)
            return rollup
    except Exception:
        pass

    # Load messages for LLM path
    try:
        messages = adapter.load_messages(session_id)
    except Exception:
        messages = []

    if not messages:
        return None

    # Build transcript
    lines = []
    for m in messages:
        prefix = "User" if m.role == "user" else "Assistant"
        lines.append(f"{prefix}: {m.content}")
    transcript = "\n\n".join(lines)

    # Respect input token budget
    max_input = config.llm.max_input_tokens
    prompt_overhead = count_tokens(_SUMMARY_PROMPT.format(transcript=""))
    transcript = truncate_to_budget(transcript, max_input - prompt_overhead - 200)

    api_key = config.llm.api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # Graceful degradation: return first user message as preview
        for m in messages:
            if m.role == "user":
                preview = m.content[:200]
                update_summary(db, session_id, preview)
                return preview
        return None

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        prompt = _SUMMARY_PROMPT.format(transcript=transcript)
        response = client.messages.create(
            model=config.llm.model,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        summary = response.content[0].text.strip()
    except Exception:
        return None

    update_summary(db, session_id, summary)
    return summary


def batch_summarize(
    db: sqlite_utils.Database,
    config: ForgeConfig,
    session_ids: list[str] | None = None,
    force: bool = False,
    on_progress: "Callable[[str, str | None], None] | None" = None,
) -> BatchSummaryResult:
    """Summarize multiple sessions.

    If session_ids is None, summarizes all sessions without a cached summary
    (or all sessions if force=True).

    on_progress(session_id, summary_or_None) is called after each session.
    """
    result = BatchSummaryResult()

    if session_ids is None:
        rows = get_sessions(db, limit=10_000)
        if not force:
            rows = [r for r in rows if not r.get("summary")]
        session_ids = [r["id"] for r in rows]

    for sid in session_ids:
        try:
            summary = summarize_session(db, sid, config, force=force)
            if summary:
                result.summarized += 1
            else:
                result.skipped += 1
            if on_progress:
                on_progress(sid, summary)
        except Exception as exc:
            result.errors.append(f"{sid}: {exc}")
            if on_progress:
                on_progress(sid, None)

    return result
