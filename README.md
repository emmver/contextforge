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

# Save a bundle to the DB for later reuse
cf compact <session-id> --save

# Preview: show the exact command that would inject context into a new Codex session
cf transfer <session-id> --to codex

# Execute: launch a new Codex session with context injected
cf transfer <session-id> --to codex --execute

# Multi-session bundle → altimate-code, richer key_messages strategy
cf transfer <id1> <id2> --to altimate-code --strategy key_messages --execute

# Resume an existing session with injected context
cf transfer <id> --to claude_code --session <existing-session-id> --execute

# Machine-readable output
cf transfer <id> --to codex --format json
```

### Injection methods

| Method | When used | How |
|---|---|---|
| `system_prompt` | New session, small context (≤4k tokens) | Passed via `--system-prompt` flag |
| `resume` | Continuing an existing session | `--resume` / `resume` / `run -s` |
| `fork` | Branch from existing session | `fork` / `--fork` |
| `file` | Any context >4k tokens | Writes `CONTEXT.md` to target dir; system prompt references it |

The method is chosen automatically based on token count and whether a target session is specified. Override with `--method`.

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

## Session Summaries

ContextForge generates plain-English summaries of what each session accomplished.

```bash
cf summarize <id>        # generate or refresh summary for one session
cf summarize --all       # bulk-generate summaries for all sessions
cf summarize --all --force  # regenerate even existing ones

# Scan + summarize in one step
cf scan --summarize
```

**How summaries work:**
1. **Codex** — reuses pre-computed `stage1_outputs.rollout_summary` when available (zero LLM cost)
2. **All tools** — calls the Claude API (Haiku model) to produce a 3–5 sentence summary
3. **No API key** — falls back to showing the first user message as a preview

Summaries are cached in the local SQLite index and shown in `cf ls` and `cf show`.

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
