# ContextForge — Development Plan

Last updated: 2026-04-04
Status: Phase 1 Complete / Phase 2 Ready

## How to use this file

Agents and contributors: update this file as you work.
- Mark completed items `[x]`
- Update `Status:` at the top when starting/completing a phase
- Update `Last updated:` to today's date when you make changes
- When completing a phase, also update `README.md` to reflect new capabilities

---

## Phase 1: Foundation [COMPLETE]

- [x] Project scaffold (`uv init`, deps, `pyproject.toml` entry point `cf`)
- [x] Data models (`models/session.py` — Session, Message, ContextBundle)
- [x] Config models (`models/config.py` — ForgeConfig)
- [x] SQLite schema + CRUD (`core/db.py`)
- [x] Abstract adapter base (`adapters/base.py`)
- [x] Claude Code adapter (`adapters/claude_code.py`)
- [x] Codex adapter (`adapters/codex.py`)
- [x] altimate-code adapter (`adapters/altimate_code.py`)
- [x] Adapter registry (`adapters/registry.py`)
- [x] Scanner (`core/scanner.py`)
- [x] Token utilities (`utils/tokens.py`)
- [x] Display helpers (`utils/display.py`)
- [x] CLI: `cf scan`, `cf ls`, `cf show`, `cf tag`, `cf config` (`cli.py`)
- [x] Tests: Claude Code adapter, db, compactor (13 passing)
- [x] README.md, AGENTS.md, PLAN.md

---

## Phase 2: Summarizer [ ]

- [ ] Wire summarizer into `cf scan` (auto-summarize new sessions if API key set)
- [ ] `cf summarize <id>` and `cf summarize --all` commands (already scaffolded in cli.py)
- [ ] Summarizer tests with mocked Anthropic client (`tests/core/test_summarizer.py`)
- [ ] Codex pre-existing `stage1_outputs.rollout_summary` shortcut (zero LLM cost)
- [ ] Update README.md with summarizer section

---

## Phase 3: Compaction + Transfer [ ]

- [ ] `cf compact` command with `--save` to persist bundle to DB
- [ ] `cf transfer` cross-tool end-to-end test (Claude Code → Codex)
- [ ] `cf transfer` cross-tool end-to-end test (Codex → altimate-code)
- [ ] Large-context file injection (`CONTEXT.md` strategy, >4k tokens)
- [ ] `--format json` on `cf compact` and `cf transfer`
- [ ] Tests for injector (`tests/core/test_injector.py`)

---

## Phase 4: TUI Dashboard [ ]

- [ ] `textual` moved to runtime deps (currently dev)
- [ ] `SessionTable` widget — DataTable sorted by `updated_at`, colored by tool
- [ ] `SessionDetail` widget — reactive right panel with summary + metadata
- [ ] `TransferPanel` widget — modal/overlay for target tool selection
- [ ] `StatusBar` widget — last scan time, session counts
- [ ] `tui/styles.tcss` — per-tool accent colors
- [ ] `cf dashboard` command fully wired
- [ ] Update README.md with dashboard screenshot

---

## Phase 5: Polish [ ]

- [ ] `cf config set` persists to `~/.contextforge/config.toml`
- [ ] Graceful degradation: missing tools silently skipped with `--verbose` warning
- [ ] `--format json` on all remaining commands
- [ ] Install docs: `uv tool install contextforge`
- [ ] GitHub Actions CI: `uv run pytest --cov=contextforge`
- [ ] `open-claw` adapter (if/when CLI interface is documented)
- [ ] Session search: `cf ls --search <query>`
- [ ] Session archiving: `cf archive <id>`

---

## Architecture Reference

```
contextforge/
├── cli.py                    ← Typer root; all commands
├── tui/
│   ├── app.py                ← Textual Application
│   └── widgets/
├── adapters/
│   ├── base.py               ← Abstract ToolAdapter
│   ├── claude_code.py        ← Claude Code (JSONL)
│   ├── codex.py              ← Codex (SQLite + JSONL)
│   ├── altimate_code.py      ← altimate-code (SQLite)
│   └── registry.py           ← ADAPTERS dict + helpers
├── core/
│   ├── db.py                 ← SQLite schema + CRUD
│   ├── scanner.py            ← multi-adapter discovery
│   ├── summarizer.py         ← LLM summarization + caching
│   ├── compactor.py          ← 3-strategy context compaction
│   └── injector.py           ← shell command builder + executor
├── models/
│   ├── session.py            ← Session, Message, ContextBundle
│   └── config.py             ← ForgeConfig
└── utils/
    ├── tokens.py             ← tiktoken helpers
    └── display.py            ← rich helpers
```

## Key Design Decisions

- **Adapters are always read-only** — ContextForge never writes to tool-native storage
- **`build_inject_command()` returns a string, never executes** — execution only in `injector.py` with `--execute`
- **Token budget is a hard cap** — `compactor.py` always truncates to stay within budget
- **SQLite for index only** — messages re-read from source on demand (no duplication)
- **LLM API key is optional** — graceful degradation to first-message preview
