"""MCP server exposing the engine's fifteen tools.

The tool names, arguments, and semantics deliberately match the historical
``projectmem`` MCP surface (plus honest corrections: search covers
``git_commit`` too), so an agent's learned workflow and every AGENTS.md
instruction keep working after cutover — only the server command changes.
"""
from __future__ import annotations

import contextlib
import functools
import io
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from pydantic import Field

from pollux.constants import VERSION
from pollux.engine.commands import Memory
from pollux.engine.errors import EngineError
from pollux.engine.storage import (
    discover_mem_dir,
    read_events_lenient,
)

_MEM_DIR: Path | None = None


def _resolve_mem_dir() -> Path | None:
    if "--root" in sys.argv:
        index = sys.argv.index("--root")
        if index + 1 < len(sys.argv):
            return discover_mem_dir(Path(sys.argv[index + 1]).expanduser().resolve())
    env_root = os.environ.get("PROJECTMEM_ROOT")
    if env_root:
        return discover_mem_dir(Path(env_root).expanduser().resolve())
    return discover_mem_dir()


def _mem() -> Path:
    global _MEM_DIR
    if _MEM_DIR is None:
        _MEM_DIR = _resolve_mem_dir()
    if _MEM_DIR is None:
        raise EngineError(
            "No .projectmem directory found. Start the server from the project "
            "or pass --root / set PROJECTMEM_ROOT."
        )
    return _MEM_DIR


def _read_file(name: str) -> str:
    path = _mem() / name
    if not path.exists():
        raise EngineError(f"{name} not found in memory at {_mem()}")
    return path.read_text(encoding="utf-8")


@contextlib.contextmanager
def _suppress_stdout():
    """Keep JSON-RPC stdio clean if any engine path prints."""
    saved = sys.stdout
    sys.stdout = io.StringIO()
    try:
        yield
    finally:
        sys.stdout = saved


def safe_tool(fn: Callable) -> Callable:
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            with _suppress_stdout():
                return fn(*args, **kwargs)
        except Exception as exc:  # one tool failure must not kill the session
            return f"pollux tool error: {type(exc).__name__}: {exc}"

    return wrapper


try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - dependency guard
    raise SystemExit(
        "The 'mcp' package is required to run the server: pip install 'mcp>=1.2'"
    ) from exc

mcp = FastMCP("pollux")
# FastMCP (mcp 1.x) exposes no version parameter; stamp the lowlevel server so
# the handshake reports the application version, not the SDK's.
_inner = getattr(mcp, "_mcp_server", None)
if _inner is not None:
    _inner.version = VERSION


@mcp.tool()
@safe_tool
def get_instructions() -> str:
    """Load the project's mandatory AI workflow rules (AI_INSTRUCTIONS.md).

    Read-only; call at session start."""
    return _read_file("AI_INSTRUCTIONS.md")


@mcp.tool()
@safe_tool
def get_summary() -> str:
    """Read the distilled project memory (summary.md).

    Small memories return the full file; when the summary exceeds the MCP
    size budget (where client-side truncation would silently hide older
    entries), a bounded digest is returned instead with pointers to
    `get_issue`/`search_events`/`pollux show`."""
    from pollux.engine.summary import get_summary_view

    return get_summary_view(_mem())


@mcp.tool()
@safe_tool
def get_project_map() -> str:
    """Read the structural layout (PROJECT_MAP.md).

    Returns 'No project map found.' if not initialized. Read-only."""
    path = _mem() / "PROJECT_MAP.md"
    if not path.exists():
        return "No project map found."
    return path.read_text(encoding="utf-8")


@mcp.tool()
@safe_tool
def get_plan() -> str:
    """Read plan.md — the team's intent (ideas, active plans, next steps).

    Returns 'No plan found.' if not initialized. Read-only."""
    path = _mem() / "plan.md"
    if not path.exists():
        return "No plan found."
    return path.read_text(encoding="utf-8")


@mcp.tool()
@safe_tool
def precheck_file(
    file_path: Annotated[str, Field(description="Project-relative file path to check.")],
) -> str:
    """Check a file's memory before modifying it: open issues, failed and
    partial attempts, stale decisions, churn. Read-only."""
    from pollux.engine.precheck import precheck_files

    report = precheck_files(_mem(), [file_path.replace("\\", "/")])
    return report.render_text()


@mcp.tool()
@safe_tool
def get_issue(
    issue_id: Annotated[
        str, Field(description="Zero-padded 4-digit issue ID, e.g. '0042'.")
    ],
) -> str:
    """Read one issue's full history (token-efficient vs the whole summary).

    Read-only."""
    matches = sorted((_mem() / "issues").glob(f"{issue_id}-*.md"))
    if not matches:
        return f"No issue found with ID {issue_id}."
    return matches[0].read_text(encoding="utf-8")


@mcp.tool()
@safe_tool
def search_events(
    query: Annotated[
        str,
        Field(
            description="Case-insensitive substring. Matched against summary, "
            "notes, location, files, git_commit, and git_message."
        ),
    ],
    limit: Annotated[int, Field(description="Max events to return.")] = 10,
    include_archived: Annotated[
        bool, Field(description="Also search archived event files.")
    ] = False,
) -> str:
    """Search the event log, most relevant first (open issues, unresolved
    failures, recency, and file-path hits rank higher). Empty result
    returns a friendly message, not an error. Read-only."""
    from pollux.engine.search import format_result, search_events

    events = search_events(
        _mem(), query, include_archived=include_archived, ranked=True
    )
    if not events:
        return f"No events match '{query}'."
    lines = [format_result(event) for event in events[:limit]]
    total = len(events)
    lines.append(f"\n{total} match(es){'' if total <= limit else f', showing top {limit}'}")
    return "\n".join(lines)


@mcp.tool()
@safe_tool
def get_score() -> str:
    """Failure-prevention score with the formula disclosed.

    Read-only; computed from events.jsonl on each call."""
    events, _skipped = read_events_lenient(_mem())
    failed = sum(1 for e in events if e.type == "attempt" and e.outcome == "failed")
    decisions = sum(
        1 for e in events if e.type == "decision" and not e.supersedes
    )
    issue_ids = {e.issue_id for e in events if e.type == "issue"}
    fixed_ids = {e.issue_id for e in events if e.type == "fix" and e.issue_id}
    attempted = {e.issue_id for e in events if e.type == "attempt" and e.issue_id}
    fixes_with_context = len(fixed_ids & attempted)
    score = (
        min(40, len(issue_ids) * 2)
        + min(20, failed)
        + min(20, decisions)
        + min(20, fixes_with_context * 2)
    )
    grade = (
        "A+" if score >= 90 else "A" if score >= 80 else "B" if score >= 70
        else "C" if score >= 55 else "D" if score >= 40 else "F"
    )
    return (
        f"pollux Prevention Score: {grade} ({score}/100)\n"
        f"  Issues on record: {len(issue_ids)} ({len(fixed_ids)} fixed)\n"
        f"  Failed approaches on record: {failed}\n"
        f"  Decisions documented: {decisions}\n"
        f"  Fixes with attempt context: {fixes_with_context}\n"
        f"  Formula: 2/issue (max 40) + 1/failed attempt (max 20) + "
        f"1/decision (max 20) + 2/contextual fix (max 20)."
    )


@mcp.tool()
@safe_tool
def get_context(
    tokens: Annotated[int, Field(description="Approximate token budget.")] = 2000,
    focus: Annotated[
        str | None, Field(description="Focus area, e.g. 'src/auth/'.")
    ] = None,
    recent_days: Annotated[int, Field(description="Recent window in days.")] = 30,
) -> str:
    """Token-budgeted context block biased toward failures and decisions.

    Read-only."""
    from pollux.engine.context import generate_context

    return generate_context(
        _mem(), token_budget=tokens, focus=focus, recent_days=recent_days
    )["markdown"]


@mcp.tool()
@safe_tool
def get_global_gotchas(
    library: Annotated[
        str | None,
        Field(description="Optional library name to filter by (substring)."),
    ] = None,
) -> str:
    """Read cross-project gotchas from the shared global store.

    Read-only. Auto-promotion into this store stays OFF by default in
    pollux (conservative governance); entries come from explicit curation."""
    home = os.environ.get("PROJECTMEM_HOME")
    base = Path(home) if home else (Path.home() / ".projectmem")
    store = base / "global" / "gotchas.json"
    if not store.exists():
        return "No global gotchas store found."
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"Global gotchas store unreadable: {exc}"
    entries = data if isinstance(data, list) else data.get("gotchas", [])
    if library:
        needle = library.casefold()
        entries = [
            e
            for e in entries
            if needle in json.dumps(e, ensure_ascii=False).casefold()
        ]
    if not entries:
        return "No global gotchas match."
    return json.dumps(entries, indent=2, ensure_ascii=False)


@mcp.tool()
@safe_tool
def log_issue(
    summary: Annotated[str, Field(description="One-line bug description.")],
    location: Annotated[
        str | None, Field(description="file:line or component, e.g. 'src/auth.py:42'.")
    ] = None,
) -> str:
    """Open a new issue and mark it active. Call BEFORE writing fix code."""
    event = Memory(_mem()).log_issue(summary, location=location)
    return (
        f"Logged issue #{event.issue_id} ({event.id}).\n"
        f"Subsequent record_attempt calls attach to it until record_fix."
    )


@mcp.tool()
@safe_tool
def record_attempt(
    summary: Annotated[str, Field(description="What was tried and why.")],
    outcome: Annotated[
        str, Field(description="'worked' | 'failed' | 'partial'.")
    ] = "failed",
    issue_id: Annotated[
        str | None, Field(description="Attach to a specific issue, e.g. '0042'.")
    ] = None,
    location: Annotated[str | None, Field(description="file:line or component.")] = None,
) -> str:
    """Record a fix attempt IMMEDIATELY after it fails, partially works, or
    works. Each distinct approach is its own call — never batch them."""
    event = Memory(_mem()).record_attempt(
        summary, outcome=outcome, issue_id=issue_id, location=location, auto_issue=True
    )
    return f"Recorded {outcome} attempt ({event.id}) on issue #{event.issue_id}."


@mcp.tool()
@safe_tool
def record_fix(
    summary: Annotated[str, Field(description="What fixed it, and why that worked.")],
    location: Annotated[str | None, Field(description="file:line or component.")] = None,
    issue_id: Annotated[str | None, Field(description="Issue to close.")] = None,
) -> str:
    """Record the confirmed fix and close the issue. Only call with
    evidence (test passed, error gone, or user confirmed)."""
    event = Memory(_mem()).record_fix(summary, location=location, issue_id=issue_id)
    return f"Recorded fix ({event.id}); issue #{event.issue_id} closed."


@mcp.tool()
@safe_tool
def add_decision(
    summary: Annotated[str, Field(description="What was decided and WHY.")],
    location: Annotated[str | None, Field(description="file:line or component.")] = None,
    supersedes: Annotated[
        str | None,
        Field(description="Event id/prefix of a prior decision this retires."),
    ] = None,
) -> str:
    """Record an architectural/product decision with its rationale."""
    event = Memory(_mem()).add_decision(summary, location=location, supersedes=supersedes)
    suffix = f", superseding {event.supersedes}" if event.supersedes else ""
    return f"Recorded decision ({event.id}){suffix}."


@mcp.tool()
@safe_tool
def add_note(
    summary: Annotated[str, Field(description="The note content.")],
    location: Annotated[str | None, Field(description="file:line or component.")] = None,
) -> str:
    """Record a gotcha, setup detail, or durable context."""
    event = Memory(_mem()).add_note(summary, location=location)
    return f"Recorded note ({event.id})."


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
