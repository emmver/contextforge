from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    api_key: str | None = None
    model: str = "claude-haiku-4-5-20251001"
    max_input_tokens: int = 60_000


class ScannerConfig(BaseModel):
    max_sessions_per_tool: int = 200


class CompactorConfig(BaseModel):
    default_strategy: str = "summary_only"
    default_token_budget: int = 4096
    file_injection_token_budget: int = 32_768


class ToolConfig(BaseModel):
    enabled: bool = True
    custom_path: str | None = None


class ForgeConfig(BaseModel):
    db_path: Path = Field(default_factory=lambda: Path.home() / ".contextforge" / "contextforge.db")
    llm: LLMConfig = Field(default_factory=LLMConfig)
    scanner: ScannerConfig = Field(default_factory=ScannerConfig)
    compactor: CompactorConfig = Field(default_factory=CompactorConfig)
    tools: dict[str, ToolConfig] = Field(default_factory=dict)
