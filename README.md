# ContextForge

**Session manager and context bridge for agentic CLI tools.**

ContextForge (`cf`) tracks sessions from Claude Code, Codex, altimate-code, and other
agentic CLI tools. It generates plain-English summaries of each session and can compact
and transfer context from one or more sessions into a new session — within the same tool
or across different tools.

## What it does

- **Discovers sessions** from Claude Code (`~/.claude/projects/`), Codex (`~/.codex/state_5.sqlite`), and altimate-code (`~/.local/share/altimate-code/`)
- **Generates summaries** of what each session accomplished (with optional Claude API integration)
- **Compacts context** intelligently, reducing multi-turn conversations to a token-efficient ContextBundle
- **Transfers context** across tools and sessions (Claude Code → Codex, Codex → altimate-code, etc.)
- **Provides a TUI dashboard** to browse and manage sessions interactively

## Prerequisites

- **Python 3.13+** (ContextForge requires Python 3.13 or later)
- **uv** or **pipx** (for installation as a tool)
- At least one of: Claude Code, Codex, or altimate-code installed and with session history

## Install

### Using `uv` (recommended)
```bash
uv tool install context-forge-cli
```

### Using `pipx`
```bash
pipx install context-forge-cli
```

### From source (development)
```bash
git clone https://github.com/emmver/contextforge.git
cd contextforge
uv sync
uv run cf --help
```

### Verify installation
```bash
cf --help      # Should show the CLI with all commands
cf config      # Show or edit configuration
```

## Quick Start

### 1. Scan for sessions
```bash
cf scan              # discover and index sessions from all installed tools
```
This reads tool-native storage and builds a local SQLite index at `~/.contextforge/contextforge.db`.
First run typically takes a few seconds depending on session count.

### 2. List and explore sessions
```bash
cf ls                # list all recent sessions (with summaries if available)
cf ls --format json  # machine-readable output
cf show <id>         # show full detail + summary for a specific session
```

### 3. Generate summaries (optional)
```bash
cf summarize --all                 # generate summaries for all unsummarized sessions
cf summarize <id>                  # refresh summary for a single session
cf summarize --all --force         # regenerate all summaries (e.g., after config change)
```
Summaries require an Anthropic API key (see [Configuration](#configuration) below).

### 4. Launch the dashboard
```bash
cf dashboard         # interactive TUI with live session browser
```

### 5. Transfer context to a new session
```bash
# Preview the context bundle
cf compact <session-id>

# Inject context into a new Claude Code session
cf transfer <session-id> --to claude_code --execute

# Inject into Codex with richer context
cf transfer <session-id> --to codex --strategy key_messages --execute
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
| Claude Code (CLI) | `~/.claude/projects/` JSONL | `--system-prompt` / `--resume` |
| Claude Desktop | `~/Library/Application Support/Claude/local-agent-mode-sessions/` | `--system-prompt` |
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

Config file: `~/.contextforge/config.toml` (created on first run)

### Manage config from CLI
```bash
cf config show         # display current config
cf config set llm.api_key sk-ant-...
cf config set compactor.default_strategy key_messages
```

### Full reference
```toml
[llm]
api_key = "sk-ant-..."                    # optional; enables LLM summarization
model = "claude-haiku-4-5-20251001"       # or any Claude model

[compactor]
default_strategy = "summary_only"         # summary_only | key_messages | full_recent
default_token_budget = 4096               # max tokens per compacted session

[scanner]
max_sessions_per_tool = 200               # limit sessions indexed per tool
```

### Notes on configuration

- **Without API key**: Summaries fall back to showing the first user message (no LLM cost)
- **With API key**: Summaries use Claude Haiku via the Anthropic API (~0.30¢ per summary)
- **Token budget**: Controls how aggressively context is compacted; higher = richer context
- **Strategies**: See [Compaction Strategies](#compaction-strategies) for details on each

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

## TUI Dashboard

```bash
cf dashboard
```

A full-screen Textual dashboard with live session browsing, filtering, and analytics.

```
┌ Sessions ────────────────┬ Detail ─────────────────────────┐
│ 🔵 CC    utastar   1.2M  │ utastar_thesis                  │
│ 🟢 Codex dataset    45k  │                                 │
│ 🟣 Alt   BigQuery   12k  │ Tool      Claude Code           │
│ 🔵 CC    jira-mcp  890k  │ CWD       ~/Github/utastar_...  │
│ ...                      │ Tokens    1.2M                  │
│                          │ Created   2026-03-10 09:14 UTC  │
│                          │ Updated   2026-04-03 22:41 UTC  │
│                          │                                 │
│                          │ ── Summary ──                   │
│                          │                                 │
│                          │ Worked on UTASTAR ensemble      │
│                          │ pruning framework...            │
└──────────────────────────┴─────────────────────────────────┘
 Sessions — CC:12 │ Codex:4 │ Alt:3 │ Total:19 │ 129M tok   14:32:01
```

### Key bindings

| Key | Action |
|---|---|
| `a` | Analytics dashboard (tool charts, activity sparkline, top projects) |
| `x` | Per-turn token breakdown for selected session |
| `/` | Toggle live filter bar (text search + tool buttons) |
| `r` | Rescan all tools |
| `s` | Summarize selected session |
| `t` | Open transfer modal (choose tool + strategy) |
| `c` | Compact selected session and preview bundle |
| `q` | Quit |
| `↑↓` | Navigate sessions |

### Analytics modal (`a`)

Shows aggregate stats with configurable time windows:

- **W** = last 7 days, **M** = 30 days, **H** = 6 months, **Y** = 1 year
- Sessions and token usage per tool (unicode bar charts)
- Activity sparkline over time
- Top 5 projects by session count

### Live filter (`/`)

Press `/` to open the filter bar above the session list:
- Type to filter by title or project path (instant, no DB re-query)
- Click **All / CC / Codex / Alt** to filter by tool
- Combine text + tool filters; match count shown in the status bar
- Press `ESC` to clear and close

### Transfer modal (`t`)

Press `t` on any session to open the transfer panel. Choose:
- **Target tool** — Claude Code, Codex, or altimate-code
- **Strategy** — `summary_only`, `key_messages`, or `full_recent`
- **Preview** — shows the exact shell command (no side effects)
- **Execute** — builds the bundle and launches the target tool

## Troubleshooting

### `command not found: cf`
- **Cause**: Tool not in PATH after installation
- **Fix**: Restart your shell, or reinstall: `uv tool install contextforge --force`

### `RuntimeError: no sessions found` or empty `cf ls`
- **Cause**: No sessions discovered from installed tools
- **Fix**: Run `cf scan` first; check that you have Claude Code/Codex/altimate-code with session history

### `ANTHROPIC_API_KEY not set` warning
- **Cause**: No API key configured for summaries
- **Fix**: Set it via `cf config set llm.api_key sk-ant-...` or set `ANTHROPIC_API_KEY` env var
- **Note**: Summaries are optional; you can still use ContextForge without API keys

### Session not appearing after tool update
- **Cause**: Tool storage location changed or was not yet indexed
- **Fix**: Run `cf scan` again to reindex all tools

### `cf dashboard` crashes or displays incorrectly
- **Cause**: Terminal size too small or unsupported terminal type
- **Fix**: Resize terminal to at least 80x24; try a different terminal emulator

### Database locked / concurrent access error
- **Cause**: Two `cf` commands running at once
- **Fix**: Wait for the first command to finish, or delete `~/.contextforge/contextforge.db` and run `cf scan` again

## Development

```bash
uv sync              # install all dependencies including dev
uv run pytest        # run tests
uv run cf --help     # run CLI locally
uv tool install . --force  # test local installation
```
