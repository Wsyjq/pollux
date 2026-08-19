# Architecture

## Purpose

Pollux is evolving from a governance layer around the third-party
`projectmem` engine into a complete, self-owned project-level memory system. The engine
under `src/pollux/engine/` keeps the on-disk format contract of
`.projectmem/` (append-only `events.jsonl` with six typed events, derived `summary.md`
and `issues/`, hand-maintained `PROJECT_MAP.md` / `plan.md`), so existing memories —
including a 1,259-event family root — carry over without migration.

Why own the engine: precheck timeouts on large event stores, auto-capture that ignores
parent-anchored family roots and Chinese commit subjects, the lack of archival and of
any cross-process lock are all engine-layer defects that a subprocess companion cannot
fix. Why not fork upstream: those defects are architectural (O(N) full rewrites,
unindexed scans), so repairing them equals rewriting the core while inheriting the
maintenance burden of a divergent fork.

## Components (0.2.x — engine self-owned, upstream dependency removed)

```text
pollux CLI
  |-- init ------> engine bootstrap (in-process, idempotent skeleton)
  |
  |-- log/attempt/fix/decision/note ----> engine.commands (Memory)
  |                                        |-- engine.locking (cross-process write lock)
  |                                        `-- engine.storage (walk-up root discovery)
  |
  |-- show/search/context/precheck ------> engine.index (inverted file->events map)
  |                                        `-- engine.gitmeta (single-pass git log)
  |
  |-- archive ---------------------------> engine.archive (reversible lifecycle)
  |
  |-- backup ----------------------------> engine.backup (locked snapshot + manifest)
  |
  |-- capture ---------------------------> engine.capture (CN+EN classifier, hash dedup)
  |                                        `-- engine.hooks (managed blocks, pinned runtime)
  |
  |-- dossier ---------------------------> engine.dossier (repository-agnostic file dossiers)
  |
  |-- mcp -------------------------------> engine.mcp_server (15 same-named tools)
  |
  |-- render ----> client config renderer (points at the pollux MCP entry)
  |
  `-- doctor ----> policy checks + engine health (legacy-hook hints, runtime drift)
```

## Module map

| Module | Responsibility |
|---|---|
| `cli.py` | Argument parsing and command orchestration |
| `runtime.py` | Interpreter resolution, root discovery, profile/root contract |
| `engine/bootstrap.py` | Idempotent memory skeleton + pollux AI instructions |
| `engine/models.py` | Event model; byte-compatible serialization; CJK slugs |
| `engine/storage.py` | Root discovery, locked appends, issue ids, config parsing |
| `engine/locking.py` | Atomic-mkdir cross-process lock with stale takeover |
| `engine/summary.py` | Derived summary/issues regeneration (incremental, atomic) |
| `engine/redaction.py` | Secret scrubbing on the write path |
| `engine/index.py` | In-memory inverted index over the event log |
| `engine/gitmeta.py` | Batched git metadata (one git-log pass per run) |
| `engine/precheck.py` | Pre-change file analysis with batched staleness/churn |
| `engine/search.py` | Substring/regex search incl. git_commit/git_message |
| `engine/context.py` | Token-budgeted context generation |
| `engine/archive.py` | Reversible event lifecycle with manifest audit |
| `engine/backup.py` | Verified whole-memory zip snapshots under the write lock |
| `engine/capture.py` | Bilingual commit/merge auto-capture |
| `engine/hooks.py` | Managed hook blocks, runtime pinning, install/uninstall |
| `engine/dossier.py` | Per-file dossiers (cards + memory + Git evidence) |
| `engine/mcp_server.py` | FastMCP server with the historical 15-tool surface |
| `files.py` | Atomic, idempotent marked-block and ignore-file updates |
| `render.py` | Client-specific MCP configuration output |
| `doctor.py` | Findings and policy evaluation |
| `secrets.py` | Bounded, non-echoing credential pattern scan |
| `models.py` | Stable doctor result model |
| `constants.py` | Version, templates, profiles, and policy constants |

## Initialization sequence

1. Resolve roots and verify the selected Python environment has a supported projectmem version.
2. Install the managed Agent blocks before projectmem scans the tree, so the first derived
   structure cache is complete.
3. Run projectmem with conservative feature flags, then restore the stricter managed
   `CLAUDE.md` block that upstream refreshes.
4. Apply the profile's ignore policy and pin installed hooks to the projectmem entry point beside
   the selected Python runtime.
5. Run doctor and return a non-zero status if the resulting policy is unsafe.

Non-interactive stdout and stderr default to UTF-8 for stable automation. Interactive terminals
and an explicit `PYTHONIOENCODING` setting retain their chosen encoding.

## Data ownership

The memory engine owns append-only events and derived summaries/issues (upstream
`projectmem` until M6, the own `engine/` package afterwards — same format either way).
Contributors own the current project map and plan. `pollux` owns only its marked
instruction blocks and recommended ignore entries.

## Profiles

### Team

Memory and Git share a root. Distilled knowledge can be committed; raw and runtime files are
ignored.

### Private

Memory and Git share a root, but the complete `.projectmem/` directory is ignored.

### Family

Several related repositories share an ancestor memory root. `pollux` writes Agent instructions
to both roots, rejects unsupported hooks, and keeps one event history by explicit choice.

## Failure behavior

- Partial or reversed markers fail closed.
- Existing non-managed file content is preserved.
- Managed writes use same-directory temporary files and `os.replace`.
- Atomic replacement preserves an existing file's mode so POSIX Git hooks remain executable.
- Unsupported engine versions fail before initialization.
- Agent workflow files are mandatory; initialization has no bypass that would make doctor fail by
  construction.
- Hook runtime drift is reported instead of silently invoking a different projectmem install.
- Hook rewrites are confined to one complete projectmem marker block; surrounding user hook code
  is never treated as managed content.
- Doctor applies the same profile/root contract as init and audits both roots in family mode.
- Client configuration is rendered, never silently merged into global config.
