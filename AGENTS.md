# AGENTS.md

<!-- >>> pollux >>> -->
## Pollux Project Memory (MANDATORY)

This project uses projectmem as its persistent engineering memory.

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

Git hooks are advisory. Search by commit title and inspect the event's
`git_commit` field before claiming auto-capture worked.
<!-- <<< pollux <<< -->
