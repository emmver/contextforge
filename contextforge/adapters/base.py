"""Abstract base class for all tool adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from contextforge.models.session import Message, Session
from contextforge.utils.tokens import count_tokens


class ToolAdapter(ABC):
    tool_name: str
    default_paths: list[Path]

    @abstractmethod
    def discover_sessions(self) -> list[Session]:
        """Return a list of sessions discovered for this tool."""
        ...

    @abstractmethod
    def load_messages(self, session_id: str) -> list[Message]:
        """Return the full message list for a given session ID."""
        ...

    @abstractmethod
    def build_inject_command(
        self,
        context: str,
        target_session_id: str | None = None,
        cwd: str | None = None,
        method: str = "system_prompt",
    ) -> str:
        """Return the shell command to start/continue a session with context injected.

        This method MUST NOT execute anything — it only returns the command string.
        """
        ...

    def get_rollup_summary(self, session_id: str) -> str | None:
        """Return a pre-computed summary if the tool stores one natively, else None."""
        return None

    def is_available(self) -> bool:
        """Return True if this tool is installed and its data paths exist."""
        return any(p.exists() for p in self.default_paths)

    def _count_session_tokens(self, session_id: str | Path) -> int:
        """Count total tokens in a session by loading and summing message tokens.

        This is the default implementation that all adapters can use.
        Adapters may override this with more efficient implementations.
        """
        if isinstance(session_id, Path):
            session_id = session_id.stem
        messages = self.load_messages(str(session_id))
        total = 0
        for msg in messages:
            total += msg.token_count or count_tokens(msg.content)
        return total
