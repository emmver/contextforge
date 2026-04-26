"""Token counting utilities using tiktoken."""
from __future__ import annotations

import tiktoken

_ENCODING = None


def _get_encoding() -> tiktoken.Encoding:
    global _ENCODING
    if _ENCODING is None:
        _ENCODING = tiktoken.get_encoding("cl100k_base")
    return _ENCODING


def count_tokens(text) -> int:
    if not isinstance(text, str):
        if isinstance(text, list):
            text = " ".join(
                block.get("text", "")
                for block in text
                if isinstance(block, dict) and block.get("type") in ("text", "input_text")
            )
        else:
            text = str(text) if text is not None else ""
    return len(_get_encoding().encode(text, disallowed_special=()))


def truncate_to_budget(text: str, budget: int) -> str:
    enc = _get_encoding()
    tokens = enc.encode(text, disallowed_special=())
    if len(tokens) <= budget:
        return text
    return enc.decode(tokens[:budget])
