# Pollux

A complete, local-first project memory for AI coding agents: self-owned engine,
governance, diagnostics, and per-file dossiers in one package.

> **Status:** early alpha. The repository is not published to PyPI yet.
> **The name:** Pollux is the immortal twin of Castor in Gemini. Here
> `events.jsonl` is Castor — the mortal, append-only record that eventually
> archives away — while the distilled `summary.md` is Pollux, the memory that
> stays alive; one cannot exist without the other.
> **Relationship to projectmem:** the on-disk format (`.projectmem/` layout,
> six typed events, derived `summary.md`) is compatible with
> [projectmem](https://github.com/riponcm/projectmem) 0.2.x so existing memories
> carry over without migration, but this project no longer depends on or wraps
> that package — the engine is self-owned, fixing its architectural limits
> (unindexed precheck, full-rewrite-per-event, cwd-only capture, English-only
> classification, no archival, no locking).

[简体中文](./README.zh-CN.md)

## Why this exists

Memory tools can create files and expose MCP commands, but a reliable engineering workflow needs
more than storage:

- A clear boundary between append-only history and directly maintained current state.
- Conservative defaults instead of silently enabling hooks or global memory inheritance.
- Repeatable Agent instructions across OpenCode, ZCode, Claude, Cursor, and Codex.
- A doctor command that can prove the configured memory root, privacy profile, hook behavior, and
  accidental secret exposure.
- A real project-family mode: memory anchored at a parent of several repos, with
  walk-up discovery, cross-process locking, and parent-anchored auto-capture.
- Performance that holds as the memory grows: indexed precheck, incremental
  regeneration, and reversible archival.

## Features

- `pollux init`: idempotent memory bootstrap with conservative defaults.
- `pollux doctor`: structured configuration, privacy, hook, and secret audit.
- `pollux render`: client-specific MCP configuration for five AI clients.
- Memory commands with upstream-compatible semantics: `log`, `attempt`, `fix`,
  `decision`, `note`, `show`, `search`, `precheck`, `context`, `regenerate`.
- `pollux archive`: reversible lifecycle for closed, old issues and (opt-in)
  old decisions, dry-run first.
- `pollux backup`: verified whole-memory snapshots — the irreplaceable event
  log lives outside git, so it needs its own safety net.
- Budgeted MCP reads: `get_summary` returns a bounded digest when the full
  file would exceed the client's size budget (older entries used to be
  silently truncated away); `search_events` ranks by relevance.
- `pollux capture`: bilingual (English + zh-CN) commit/merge classification,
  anchored by walk-up discovery, deduplicated by commit hash.
- `pollux hooks`: managed, runtime-pinned, advisory git hooks.
- `pollux dossier`: repository-agnostic per-file engineering dossiers
  (responsibility cards + memory + Git evidence).
- `pollux mcp`: the fifteen-tool MCP server (same tool names as the historical
  surface, so existing agent workflows keep working).
- Team, private, and project-family profiles; atomic marked-block updates that
  preserve existing `AGENTS.md` and `CLAUDE.md` content.
- UTF-8 output for non-interactive Windows automation without overriding explicit user settings.
- Runtime dependencies: only `mcp>=1.2,<2`; the CLI itself is standard library.

## Installation

Until the first public release, install from a local checkout:

```bash
python -m pip install -e .
```

The package exposes the `pollux` (CLI) and `pollux-mcp` (server) commands.

## Quick start

### Team profile

The distilled map, plan, summary, issues, and instructions may be committed. Raw events and
runtime files are ignored.

```bash
pollux init /path/to/repo --profile team --client opencode
pollux doctor /path/to/repo
```

### Private profile

The complete `.projectmem/` directory is ignored.

```bash
pollux init /path/to/repo --profile private --client claude
```

### Project-family profile

Use only for tightly related repositories that intentionally share one project map and event
history. The memory root must be an ancestor of the project.

```bash
pollux init /workspace/repo-a \
  --profile family \
  --memory-root /workspace \
  --client codex
```

Hooks in family mode are supported: `pollux`'s own hooks resolve the memory
by walking up from the Git root, so `--enable-hooks` works with a parent
memory root.

## Conservative defaults

`pollux init` keeps automation opt-in: Git hooks require `--enable-hooks`
(and `pollux hooks install`). Global-memory auto-promotion stays off; the
global gotcha store is read-only by default.

This sequence is intentional: establish an accurate project map and verify active MCP writes
before adding automation.

## Commands

```text
pollux init [PATH] [--profile team|private|family] [--enable-hooks]
pollux doctor [PATH] [--profile auto|team|private|family] [--json]
pollux render opencode|zcode|claude|cursor|codex [PATH]
pollux log|attempt|fix|decision|note <text> [--at loc] [--root DIR]
pollux show|regenerate [--root DIR]
pollux search <query> [--regex] [--failed-only] [--all]
pollux precheck [files...] [--level info|warn|block] [--json]
pollux context [--tokens N] [--focus AREA]
pollux archive --before DATE [--decisions-before DATE] [--dry-run] [--status] [--restore]
pollux backup [--to DIR] [--verify ZIP]
pollux capture commit|merge [--repo-root DIR]
pollux hooks install|uninstall [--repo DIR]
pollux dossier <path> [--validate] [--emit-schema]
pollux mcp
```

Use `pollux <command> --help` for all options.

## What doctor checks

- The nearest initialized memory root and expected memory files.
- The effective team/private/family profile.
- Agent workflow markers and source-of-truth rules.
- Runtime and raw-event `.gitignore` policy.
- Whether raw `events.jsonl` is tracked by Git.
- Both project and shared-memory roots when using the family profile.
- Legacy projectmem hook blocks (migration hint) and pollux hook runtime drift.
- Windows hook paths and unresolved runtime pins.
- OpenCode MCP root mismatch when `opencode.json` exists.
- Common credential patterns without printing the matched value.

`doctor --json` provides machine-readable output suitable for CI.

## Source-of-truth policy

| Data | Owner | Direct editing |
|---|---|---:|
| `.projectmem/events.jsonl` | pollux engine append operations | No |
| `.projectmem/summary.md` | pollux engine regeneration | No |
| `.projectmem/issues/*.md` | pollux engine regeneration | No |
| `.projectmem/archive/` | `pollux archive` (reversible) | No |
| `.projectmem/PROJECT_MAP.md` | Contributors and Agents | Yes |
| `.projectmem/plan.md` | Contributors and Agents | Yes |

## Non-goals for v0.2

- Uploading memory to a hosted service.
- Automatically modifying global AI-client configuration.
- Treating heuristic Git backfill as verified design history.
- Guaranteeing that a regex secret scan replaces dedicated secret-scanning tools.

## Development

```bash
python -m unittest discover -s tests -v
python -m ruff check .
python -m build
python -m twine check --strict dist/*
```

See [CONTRIBUTING.md](./CONTRIBUTING.md), [architecture](./docs/architecture.md), and the
[threat model](./docs/threat-model.md).

## License

Pollux is released under the [MIT License](./LICENSE). Dependency attribution is
listed in [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).
