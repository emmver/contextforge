# ContextForge

**Session manager and context bridge for agentic CLI tools.**

ContextForge (`cf`) tracks sessions from Claude Code, Codex, altimate-code, and other
agentic CLI tools. It generates plain-English summaries of each session and can compact
and transfer context from one or more sessions into a new session — within the same tool
or across different tools.

## Install

```bash
uv tool install contextforge
# or
pipx install contextforge
```

## Quick Start

```bash
cf scan              # discover and index sessions from all installed tools
cf ls                # list all recent sessions
cf show <id>         # show detail + summary for a session
cf summarize --all   # generate summaries for all unsummarized sessions
cf dashboard         # launch TUI dashboard (Phase 5)
```

## Context Transfer

```bash
# Preview: compact a session into a ContextBundle and display it
cf compact <session-id>

# Preview: show the exact command that would inject context into a new Codex session
cf transfer <session-id> --to codex

# Execute: launch a new Codex session with context injected
cf transfer <session-id> --to codex --execute

# Multi-session bundle → altimate-code, richer key_messages strategy
cf transfer <id1> <id2> --to altimate-code --strategy key_messages --execute
```

## Compaction Strategies

| Strategy | Tokens | Best for |
|---|---|---|
| `summary_only` | ~100–300 per session | Default; maximum token efficiency |
| `key_messages` | 1k–8k | Richer context; scores messages by importance |
| `full_recent` | Up to budget | Same-tool transfers; preserves recent conversation |

## Supported Tools

| Tool | Discovery | Inject method |
|---|---|---|
| Claude Code | `~/.claude/projects/` JSONL | `--system-prompt` / `--resume` |
| Codex | `~/.codex/state_5.sqlite` | `resume` / `fork` |
| altimate-code | `~/.local/share/altimate-code/opencode.db` | `run -s` / `import` |

## Configuration

Config file: `~/.contextforge/config.toml`

```toml
[llm]
api_key = "sk-ant-..."          # optional; enables LLM summarization
model = "claude-haiku-4-5-20251001"

[compactor]
default_strategy = "summary_only"
default_token_budget = 4096

[scanner]
max_sessions_per_tool = 200
```

If no API key is configured, ContextForge falls back to showing the first user message
as a session preview (no LLM call required).

## Storage

ContextForge stores its index at `~/.contextforge/contextforge.db` (SQLite).
It **never modifies** tool-native storage. All source files are read-only.

## Agent / Machine Usage

All structured commands support `--format json`:

```bash
cf ls --format json
cf show <id> --format json
cf compact <id> --format json
```

See [AGENTS.md](AGENTS.md) for contribution guidelines and [PLAN.md](PLAN.md) for development status.

## Development

```bash
uv sync              # install all dependencies including dev
uv run pytest        # run tests
uv run cf --help     # run CLI locally
```
