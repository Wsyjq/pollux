# Changelog

All notable changes will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
intends to use [Semantic Versioning](https://semver.org/spec/v2.0.0.html) after the first stable
release.

## [Unreleased]

### Changed

- **Renamed to Pollux** (previously `agent-memory-guardrails`, CLI `amguard`).
  The distribution, module (`pollux`), commands (`pollux`, `pollux-mcp`), the
  MCP config key (`mcp_servers.pollux`), and the hook runtime variable
  (`POLLUX_BIN`) all follow the new name. MCP tool names and the on-disk
  `.projectmem/` format are unchanged. `pollux init` migrates older
  `agent-memory-guardrails` and `projectmem workflow` governance markers in
  place; markers from mixed generations fail closed. Naming: Pollux is
  Gemini's immortal twin — `events.jsonl` is mortal Castor, the distilled
  `summary.md` lives on.

### Added

- `pollux backup [--to DIR] [--verify ZIP]`: whole-memory snapshot under the
  write lock into one zip with an embedded manifest (event count + sha256),
  self-tested right after writing; runtime entries (lock, cache) are skipped.
  Why: the raw event log is irreplaceable and lives outside git in every
  profile — without this, months of memory had zero backups.
- Budgeted MCP `get_summary`: small memories return the full file; past the
  12k-char budget (where client-side truncation silently hid older entries)
  it returns a bounded digest — open issues up front, recent-closed sample,
  latest decisions/notes, pointers to `pollux show`/`get_issue`/`search`.
  Measured on a real 1,263-event memory: 162k chars → 12.9k.
- Decision lifecycle: `decisions_limit` config key caps the summary's
  Decisions section (with disclosure), and `pollux archive
  --decisions-before DATE` retires old decisions reversibly. Known limit:
  an archived decision cannot be superseded until restored.
- Ranked search: `search_events` (MCP) ranks by relevance — open-issue
  events, unresolved failures, recency, and file-path hits first; CLI gains
  `--ranked`. Default order stays log order for compatibility.

## [0.2.0a1] - 2026-08-15

### Changed

- **The engine is now self-owned.** The runtime dependency on third-party
  `projectmem` is removed (only `mcp>=1.2,<2` remains). The on-disk format is
  unchanged and verified byte-compatible: regenerating a real 1,263-event
  memory produces a byte-identical `summary.md`.
- `pollux init` bootstraps the memory in-process (idempotent skeleton,
  pollux-authored `AI_INSTRUCTIONS.md`) instead of shelling out upstream.
- `pollux render` points clients at the pollux MCP server (key `pollux`).
- Family mode now supports git hooks (our hooks resolve the memory by walking
  up from the Git root); the historical limitation no longer applies.
- Doctor: version findings replaced by engine presence checks, pollux hook
  audits, and legacy projectmem hook migration hints.

### Added

- Memory commands with upstream-compatible semantics: `log`, `attempt`,
  `fix`, `decision`, `note`, `show`, `regenerate`.
- Query layer: `search` (matches `git_commit`/`git_message`; `--all` covers
  archives), `precheck` (inverted index + single-pass batched git staleness
  and churn), `context` (token-budgeted).
- Cross-process write locking (atomic-mkdir directory lock with stale
  takeover) and walk-up root discovery everywhere, including capture.
- Incremental regeneration: only touched issue files are rewritten, orphans
  are cleaned, CJK summaries keep readable slugs, config keys are actually
  parsed (`recent_issues_limit` caps the summary with disclosure).
- Reversible event lifecycle: `pollux archive --before/--dry-run/--restore`
  with a byte-faithful archive and manifest audit trail.
- Bilingual auto-capture (`pollux capture`, `pollux hooks install`):
  English conventional and zh-CN subject conventions, dedup by commit hash,
  advisory pre-commit.
- `pollux dossier`: repository-agnostic per-file dossiers (responsibility
  cards + memory + Git evidence), generalized from an internal single-repo dossier
  tool.
- `pollux mcp` / `pollux-mcp`: fifteen-tool MCP server with the historical
  tool names, so existing agent instructions keep working.

### Verified

- 147 tests green; MCP server exercised through a real stdio client session.
- Real-memory copy (1,263 events): precheck cold 0.45s / warm 0.20s (the
  historical engine exceeded 120s), search 0.18s, single write 0.087s,
  archive 169 groups in 0.56s with byte-identical restore.

## [0.1.0a1] - 2026-08-11

- Initial local alpha baseline (governance companion). Not published.
- Cross-platform `pollux init`, `doctor`, and `render` commands; team,
  private, and family profiles; idempotent Agent workflow blocks; MCP
  rendering for five clients; privacy/hook/secret diagnostics; UTF-8-safe
  Windows handling; POSIX hook-mode preservation; strict sdist/wheel
  metadata.
