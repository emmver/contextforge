"""Shared pytest fixtures — mocks tiktoken for offline test environments."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class _FakeEncoding:
    """Minimal tiktoken encoding stand-in using word-count approximation."""

    def encode(self, text: str) -> list[int]:
        return list(range(max(1, len(text.split()))))

    def decode(self, tokens: list[int]) -> str:
        # Best-effort: can't reconstruct text, but truncate_to_budget tests
        # only need a shorter string back.
        return " ".join(["x"] * len(tokens))


_FAKE_ENCODING = _FakeEncoding()


@pytest.fixture(autouse=True)
def mock_tiktoken():
    """Prevent tiktoken from downloading its BPE vocab file over the network.

    Patches _get_encoding() at the source so every importer of count_tokens
    gets the stub regardless of how they bound the function.
    """
    with patch("contextforge.utils.tokens._get_encoding", return_value=_FAKE_ENCODING):
        yield
