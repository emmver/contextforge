"""Token counting utilities using tiktoken."""
from __future__ import annotations

import tiktoken

_ENCODING = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_ENCODING.encode(text))


def truncate_to_budget(text: str, budget: int) -> str:
    tokens = _ENCODING.encode(text)
    if len(tokens) <= budget:
        return text
    return _ENCODING.decode(tokens[:budget])
