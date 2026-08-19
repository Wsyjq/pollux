# Compatibility

## Verified contract

| Component | v0.2 status |
|---|---|
| Python 3.10-3.12 | CI target |
| mcp `>=1.2,<2` | Runtime dependency for the MCP server (v2 dropped `mcp.server.fastmcp`) |
| Windows PowerShell / Git Bash hooks | UTF-8 automation output and runtime pinning included |
| Linux | CI target; hook executable-mode assertion included |
| macOS | CI target; hook executable-mode assertion included |
| OpenCode | JSON renderer and root audit |
| ZCode | JSON renderer |
| Claude Desktop | `mcpServers` JSON renderer |
| Cursor | `mcpServers` JSON renderer |
| Codex | TOML renderer |

## On-disk format compatibility (the contract that matters)

Since 0.2.0 the engine is self-owned; no third-party engine runs at all. The
compatibility surface is the **format**, kept identical to the historical
`projectmem` 0.2.x layout so memories move between engines freely:

- `events.jsonl`: six typed events, sorted-key JSON lines, ASCII-escaped,
  platform-default line endings (CRLF on Windows) — regenerated lines are
  byte-identical to the historical writer.
- `summary.md`: section structure, per-issue lines, attempt bullets, and the
  `# projectmem - <root>` header reproduce the historical generator; a real
  1,263-event memory regenerates byte-identically (default config).
- `issues/NNNN-slug.md`: same content format; slugs now preserve CJK
  characters instead of collapsing every Chinese summary to `NNNN-issue.md`.
- `PROJECT_MAP.md` / `plan.md` / `config.toml` / `AI_INSTRUCTIONS.md`:
  hand-maintained or tool-authored in place; unknown config keys are ignored.

Differences are deliberate and disclosed: search matches `git_commit` and
`git_message`; `recent_issues_limit` (config) can cap the summary with an
explicit disclosure line; archive moves closed-old issue events to
`.projectmem/archive/` reversibly.

## Historical boundaries, now fixed in-engine

These upstream `projectmem` 0.2.x limitations no longer apply:

- Post hooks required `.projectmem/` at the Git root — family mode now
  captures via walk-up discovery from the Git root.
- Auto-capture recognized only English conventional prefixes — zh-CN subject
  conventions (修复/新增/重构/回滚/不兼容/文档/测试) are first-class.
- `pjm search` did not search `git_commit` — it is a matched field now.
- Generated instructions contained contradictory `PROJECT_MAP.md` wording —
  pollux's `AI_INSTRUCTIONS.md` states the direct-maintenance rule plainly.
- No archival, no cross-process locking, per-event full rewrites — replaced
  by the archive lifecycle, the directory write lock, and incremental
  regeneration respectively.

## Version policy

The `mcp` dependency is pinned to `>=1.2,<2` until the v2 server API is
vetted; the CLI itself depends only on the standard library.
