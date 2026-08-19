# CLAUDE.md

<!-- >>> projectmem bridge >>> -->
## projectmem (MANDATORY)

Use projectmem as this project's persistent engineering memory.

At session start call `get_instructions()`, then `get_summary()`, then
`get_project_map()` when structure matters, and `get_plan()` for current intent.
Call `precheck_file(path)` before modifying a file.

Log bugs before fixing, record every attempt and its outcome, close issues only
after verification, and record decisions/gotchas with their reasons and evidence.

Do not hand-edit `events.jsonl`, `summary.md`, or generated `issues/*.md`.
`PROJECT_MAP.md` and `plan.md` are maintained directly when current structure or
intent changes. Confirm the session is recorded with `get_summary()` before exit.
<!-- <<< projectmem bridge <<< -->
