"""LLM-based session summarization with caching."""
from __future__ import annotations

import os

import sqlite_utils

from contextforge.adapters.registry import get_adapter
from contextforge.core.db import get_session, update_summary
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


def summarize_session(
    db: sqlite_utils.Database,
    session_id: str,
    config: ForgeConfig,
    force: bool = False,
) -> str | None:
    row = get_session(db, session_id)
    if row is None:
        return None

    # Return cached summary unless force-refresh
    if row.get("summary") and not force:
        return row["summary"]

    adapter = get_adapter(row["tool"])
    messages = adapter.load_messages(session_id)
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
    except Exception as exc:
        return None

    update_summary(db, session_id, summary)
    return summary
