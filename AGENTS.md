# AGENTS.md — Guidelines for Agents Working on ContextForge

This file tells automated agents (Claude Code, Codex, altimate-code, etc.) how to work
safely and effectively on this repository.

## Project Overview

ContextForge is a Python CLI/TUI tool. Entry point: `contextforge/cli.py` (command: `cf`).
Core modules: `adapters/`, `core/`, `models/`, `tui/`. Tests: `tests/`.

## Setup

```bash
uv sync              # install all dependencies
uv run pytest        # run the test suite
uv run cf --help     # run the CLI locally
```

## Hard Invariants — Never Violate These

1. **Adapters are read-only.** No adapter may write to tool-native storage
   (`~/.claude/`, `~/.codex/`, `~/.local/share/altimate-code/`). This is a hard safety boundary.

2. **`build_inject_command()` returns a string; it does NOT execute it.**
   Execution happens only in `core/injector.py` when the user passes `--execute`.

3. **Pydantic models in `models/` are the canonical data contract.**
   Adapters must return valid `Session` and `Message` objects. Never pass raw dicts
   across module boundaries.

4. **SQLite schema changes require a migration function in `core/db.py`.**
   Do not ALTER TABLE directly. Add a new migration in `_migrate()` keyed by version integer.

5. **Token budget is a hard cap, not a guideline.**
   `compactor.py` must never return a bundle exceeding `token_budget`.
   Use `utils/tokens.py` to verify before returning.

## Adding a New Tool Adapter

1. Create `contextforge/adapters/<tool_name>.py`, subclass `ToolAdapter` from `adapters/base.py`
2. Implement `discover_sessions()`, `load_messages()`, `build_inject_command()`
3. Register in `adapters/registry.py`: `ADAPTERS["<tool_name>"] = MyAdapterClass`
4. Add fixture session data to `tests/fixtures/`
5. Add tests in `tests/adapters/test_<tool_name>.py`
6. Update the Supported Tools table in `README.md`
7. Mark the relevant item `[x]` in `PLAN.md`

## Updating the Plan

- As you complete items, mark them `[x]` in `PLAN.md`
- When starting a new Phase, update the `Status:` line at the top of `PLAN.md`
- When completing a phase, update `README.md` to reflect new capabilities

## CLI Conventions

- Every command that produces structured data must support `--format json`
- Long-running operations (scan, summarize --all) must show a `rich.progress` bar
- Errors must be printed to stderr; exit code 1 on failure

## Testing

- All adapter tests must work offline (no live tool required). Use fixtures.
- Summarizer tests must mock the Anthropic client.
- Run `uv run pytest tests/` before committing.
- Target >80% coverage on `core/` and `adapters/`.

## Commit Style

```
feat(adapters): add opencode standalone adapter
fix(compactor): respect token budget when combining sessions
docs(readme): add troubleshooting section for Codex SQLite path
test(core): add scanner integration tests
```

## Module Ownership

| Area | Module |
|---|---|
| Tool discovery | `adapters/<tool>.py` |
| Session indexing | `core/scanner.py` |
| DB schema + CRUD | `core/db.py` |
| Summarization | `core/summarizer.py` |
| Context compaction | `core/compactor.py` |
| Shell command building | `core/injector.py` |
| CLI commands | `contextforge/cli.py` |
| TUI layout | `tui/app.py` |

## Do Not Touch

- `~/.claude/`, `~/.codex/`, `~/.local/share/altimate-code/` — tool-native storage
- `uv.lock` — only `uv` should modify this
- `~/.contextforge/contextforge.db` — only `core/db.py` should modify this
