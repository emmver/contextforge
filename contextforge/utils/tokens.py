"""Token counting utilities using tiktoken."""
from __future__ import annotations

import tiktoken

_ENCODING = None


def _get_encoding() -> tiktoken.Encoding:
    global _ENCODING
    if _ENCODING is None:
        _ENCODING = tiktoken.get_encoding("cl100k_base")
    return _ENCODING


def count_tokens(text: str) -> int:
    return len(_get_encoding().encode(text))


def truncate_to_budget(text: str, budget: int) -> str:
    enc = _get_encoding()
    tokens = enc.encode(text)
    if len(tokens) <= budget:
        return text
    return enc.decode(tokens[:budget])
