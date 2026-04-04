# ContextForge — Development Plan

Last updated: 2026-04-04
Status: Phase 3 Complete / Phase 4 Ready

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
- [x] Tests: Claude Code adapter, db, compactor (13 passing → 22 with Phase 2)
- [x] README.md, AGENTS.md, PLAN.md

---

## Phase 2: Summarizer [COMPLETE]

- [x] Wire summarizer into `cf scan` via `--summarize` flag (auto-summarizes new sessions)
- [x] `cf summarize <id>` and `cf summarize --all` commands with rich progress bar
- [x] Summarizer tests with mocked Anthropic client (`tests/core/test_summarizer.py` — 9 tests)
- [x] Codex pre-existing `stage1_outputs.rollout_summary` shortcut (zero LLM cost)
- [x] `batch_summarize()` helper with `on_progress` callback
- [x] Update README.md with summarizer section

---

## Phase 3: Compaction + Transfer [COMPLETE]

- [x] `cf compact` with `--save` (persists bundle to DB) and `--format json`
- [x] `cf transfer` cross-tool: Claude Code → Codex, Codex → altimate-code, altimate → Claude Code
- [x] Multi-session cross-tool bundle transfer tested
- [x] Large-context file injection (`CONTEXT.md` strategy auto-selected when >4k tokens)
- [x] `--format json` on both `cf compact` and `cf transfer`
- [x] 23 injector tests — per-tool commands, file injection, DB recording, cross-tool scenarios (45 total passing)

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
