from __future__ import annotations

VERSION = "0.2.0a1"

PROFILES = ("team", "private", "family")
CLIENTS = ("opencode", "zcode", "claude", "cursor", "codex", "dsh")

AGENT_MARKER_START = "<!-- >>> pollux >>> -->"
AGENT_MARKER_END = "<!-- <<< pollux <<< -->"
# Older governance-marker generations that init migrates in place. Seeing
# markers from two generations in one file is an error, never auto-merged.
LEGACY_AGENT_MARKER_START = "<!-- >>> agent-memory-guardrails >>> -->"
LEGACY_AGENT_MARKER_END = "<!-- <<< agent-memory-guardrails <<< -->"
LEGACY_AGENT_MARKER2_START = "<!-- >>> projectmem workflow >>> -->"
LEGACY_AGENT_MARKER2_END = "<!-- <<< projectmem workflow <<< -->"
CLAUDE_MARKER_START = "<!-- >>> projectmem bridge >>> -->"
CLAUDE_MARKER_END = "<!-- <<< projectmem bridge <<< -->"

RUNTIME_IGNORE_ENTRIES = (
    ".projectmem/events.jsonl",
    ".projectmem/watch.pid",
    ".projectmem/watch.log",
    ".projectmem/structure.json",
    ".projectmem/.current_issue",
    ".projectmem/precheck.snooze",
    ".projectmem/viz.html",
    ".projectmem/write.lock/",
    ".projectmem/cache/",
    ".projectmem/archive/",
)

EXPECTED_MEMORY_FILES = (
    "config.toml",
    "events.jsonl",
    "summary.md",
    "PROJECT_MAP.md",
    "plan.md",
    "AI_INSTRUCTIONS.md",
)

AGENT_BLOCK = f"""{AGENT_MARKER_START}
## Pollux Project Memory (MANDATORY)

This project uses pollux as its persistent engineering memory (own engine,
on-disk format compatible with the historical projectmem layout).

At session start, call these MCP tools in order:
1. `get_instructions()`
2. `get_summary()`
3. `get_project_map()` when structure matters
4. `get_plan()` for current intent and parallel work

Before modifying a file, call `precheck_file(path)`.

During work:
- Log a bug with `log_issue` before attempting a fix.
- Record every distinct attempt with `record_attempt` and its real outcome.
- Call `record_fix` only after verification.
- Record durable choices with `add_decision` and gotchas with `add_note`.
- Include why the choice was made, rejected alternatives, and evidence.

Source-of-truth boundary:
- Never hand-edit `events.jsonl`, `summary.md`, or generated `issues/*.md`.
- Maintain `PROJECT_MAP.md` directly when structure or relationships change.
- Maintain `plan.md` directly for ideas, active plans, and shipped work.
- Before finishing, call `get_summary()` and confirm the session is recorded.

Git hooks (if installed) are advisory automation; commits with recognizable
subjects are auto-captured. Verify a capture with `search_events("<hash>")`
— search matches the `git_commit` field. When in doubt, record explicitly.
{AGENT_MARKER_END}
"""

CLAUDE_BLOCK = f"""{CLAUDE_MARKER_START}
## project memory (MANDATORY)

Use pollux as this project's persistent engineering memory.

At session start call `get_instructions()`, then `get_summary()`, then
`get_project_map()` when structure matters, and `get_plan()` for current intent.
Call `precheck_file(path)` before modifying a file.

Log bugs before fixing, record every attempt and its outcome, close issues only
after verification, and record decisions/gotchas with their reasons and evidence.

Do not hand-edit `events.jsonl`, `summary.md`, or generated `issues/*.md`.
`PROJECT_MAP.md` and `plan.md` are maintained directly when current structure or
intent changes. Confirm the session is recorded with `get_summary()` before exit.
{CLAUDE_MARKER_END}
"""
