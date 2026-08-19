"""In-process memory bootstrap — what ``amguard init`` uses instead of
shelling out to an upstream engine.

Creates the ``.projectmem/`` skeleton idempotently: never overwrites an
existing file, so re-running init is always safe. The placeholder texts keep
the phrases the summary regenerator recognizes as "still unset", so the
setup-mode workflow (populate PROJECT_MAP.md and plan.md first) survives the
engine change unchanged.
"""
from __future__ import annotations

from pathlib import Path

from agent_memory_guardrails.engine.storage import (
    AI_INSTRUCTIONS_FILE,
    CONFIG_FILE,
    EVENTS_FILE,
    ISSUES_DIR,
    MEM_DIR,
    PLAN_FILE,
    PROJECT_MAP_FILE,
)
from agent_memory_guardrails.engine.summary import regenerate_summary
from agent_memory_guardrails.files import write_text_atomic

CONFIG_DEFAULTS = (
    "summary_size_limit_kb = 20\n"
    "recent_days = 30\n"
    'project_description = ""\n'
    "recent_issues_limit = 0\n"
)

INITIAL_PROJECT_MAP = (
    "# Project Map - {name}\n\n"
    "Status: not created yet. Fill the sections below with the project's real "
    "structure and key relationships — the first AI session can do it.\n\n"
    "## Project purpose\n"
    "Not described yet.\n\n"
    "## Structure\n\n"
    "## Relationships\n"
)

INITIAL_PLAN = (
    "# {name} — plan\n\n"
    "> Editable **intent** file: ideas + plans — what we *mean* to do.\n"
    "> This is NOT the event log. `events.jsonl` -> `summary.md` records what\n"
    "> *happened*; this file records what we *intend*. The AI reads it at\n"
    "> session start and edits it directly (like `PROJECT_MAP.md`): add ideas\n"
    "> and plans, check items off, move finished plans down to Shipped. Plans\n"
    "> are never logged as events.\n\n"
    "## Ideas\n"
    "_Loose thoughts, not yet committed to._\n\n"
    "## Active plans\n"
    "_What we're working toward now. Use `- [ ]` / `- [x]` checklists._\n\n"
    "## Next\n"
    "_Queued, but not started._\n\n"
    "## Someday / maybe\n\n"
    "## Shipped\n"
    "_Move completed plans here so the top stays about the future._\n"
)

AI_INSTRUCTIONS = """\
# amguard AI Instructions

These instructions are MANDATORY for all AI coding agents working in this
project. Failure to follow them means your work is incomplete and the audit
trail is corrupted.

## Start of every session

**Step 1 — Identify your mode by reading `summary.md` and `PROJECT_MAP.md`.**

- **Setup Mode** — still containing placeholder text ("Replace this
  placeholder", "None logged yet.", "Status: not created yet"). Your FIRST
  response is the memory-population pass: read the manifest, entry points,
  and architecture files; call `add_decision` for each architectural choice
  and `add_note` for each gotcha; then edit `PROJECT_MAP.md` directly
  (purpose / structure / relationships as a navigable path index).
- **Maintenance Mode** — real content. STOP analyzing structure; trust the
  memory and focus on the task.

**Step 2 — Read the four context files** via MCP tools
(`get_instructions`, `get_summary`, `get_project_map`, `get_plan`) or the
CLI (`amguard show`, `amguard context`).

**Step 3 — Check `.projectmem/issues/` only when relevant** via
`get_issue(id)`.

## Working on a file

1. Locate it in `PROJECT_MAP.md`; call `precheck_file(path)` (MCP) or
   `amguard precheck <path>` (CLI) BEFORE proposing any change.
2. Then read ONLY that file — not the whole codebase.

## MANDATORY triggers

| Trigger | MCP tool | CLI |
| --- | --- | --- |
| Bug/unexpected behavior | `log_issue` | `amguard log "<text>" --at "<file:line>"` |
| Attempt FAILED / PARTIAL / WORKED | `record_attempt` | `amguard attempt "<text>"` |
  (add `--failed` / `--partial` / `--worked`) |
| Fix confirmed with evidence | `record_fix` | `amguard fix "<text>"` |
| Architectural decision | `add_decision` | `amguard decision "<text>"` |
| Gotcha / setup detail | `add_note` | `amguard note "<text>"` |

- **Log BEFORE you fix.** Record each distinct attempt immediately; never
  batch attempts into one entry.
- **Close with `record_fix` only after evidence** (test passes, error gone,
  user confirms).
- **Never skip logging because it feels minor.**
- Write WHY in every entry: decision rationale, failure cause, rejected
  alternatives. "Changed X" without a reason is not memory.

## Data ownership

- NEVER hand-edit `events.jsonl`, `summary.md`, or `issues/*.md` — they are
  derived/append-only; use the tools above (or `amguard regenerate` to
  rebuild derived files from the log).
- `PROJECT_MAP.md` and `plan.md` are edited DIRECTLY by contributors.
- Old closed issues may be moved to `.projectmem/archive/` via
  `amguard archive`; every archive is reversible (`--restore`) and
  recorded in the archive manifest.

## Git hooks

Hooks installed by `amguard hooks install` are advisory automation: commits
with recognizable subjects (English conventional prefixes or zh-CN
conventions like 修复/新增/重构/回滚) are auto-captured, and merges are
always captured. Verify a capture with `search_events("<hash>")` — search
matches the `git_commit` field directly. When in doubt, record explicitly.

## Rules summary

- MANDATORY: log before you exit; record failed and partial attempts.
- Keep entries specific (paths, error names, test names) and include why.
- Do not claim something is fixed until tests or reproduction support it.
- `events.jsonl` is append-only; `summary.md` is derived from it.
- If MCP is unavailable use the CLI; if neither, tell the user what should
  have been recorded.
"""


def initialize_memory(memory_root: Path) -> Path:
    """Create or complete the ``.projectmem/`` skeleton; return its path.

    Idempotent: existing files are never rewritten.
    """
    mem = memory_root / MEM_DIR
    (mem / ISSUES_DIR).mkdir(parents=True, exist_ok=True)

    def ensure(name: str, content: str) -> None:
        path = mem / name
        if not path.exists():
            write_text_atomic(path, content, newline=None)

    ensure(EVENTS_FILE, "")
    ensure(CONFIG_FILE, CONFIG_DEFAULTS)
    ensure(PROJECT_MAP_FILE, INITIAL_PROJECT_MAP.format(name=memory_root.name))
    ensure(PLAN_FILE, INITIAL_PLAN.format(name=memory_root.name))
    ensure(AI_INSTRUCTIONS_FILE, AI_INSTRUCTIONS)

    summary = mem / "summary.md"
    if not summary.exists():
        regenerate_summary(mem)

    return mem
