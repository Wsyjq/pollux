"""High-level memory operations shared by the CLI and the MCP server.

Every write follows the same locked sequence: read the log (to allocate ids
and resolve attachment), append the event, regenerate derived files, update
the active-issue marker. Holding one lock across the whole sequence is what
makes concurrent worktrees safe — two agents can never observe the same
"next issue id" or interleave a half-regenerated summary.
"""
from __future__ import annotations

from pathlib import Path

from pollux.engine.errors import EngineError
from pollux.engine.locking import DirLock
from pollux.engine.models import Event, resolve_event_ref
from pollux.engine.storage import (
    append_event,
    clear_current_issue,
    discover_mem_dir,
    get_git_commit,
    latest_open_issue_within,
    next_issue_id,
    read_current_issue,
    read_events,
    require_mem_dir,
    write_current_issue,
)
from pollux.engine.summary import regenerate_summary


class Memory:
    """Entry point for memory operations against one ``.projectmem/``."""

    def __init__(self, mem_dir: Path) -> None:
        self.mem_dir = mem_dir

    @classmethod
    def discover(cls, start: Path | None = None) -> Memory:
        found = discover_mem_dir(start)
        if found is None:
            raise EngineError(
                f"No .projectmem directory found in {(start or Path.cwd()).resolve()} "
                f"or any parent. Run pollux init first."
            )
        return cls(found)

    @classmethod
    def at(cls, root: Path) -> Memory:
        return cls(require_mem_dir(root))

    @property
    def lock_path(self) -> Path:
        return self.mem_dir / "write.lock"

    def _lock(self) -> DirLock:
        return DirLock(self.lock_path)

    def write_lock(self) -> DirLock:
        """Public lock for callers assembling their own append+regen flow
        (auto-capture does this without touching the active-issue marker)."""
        return self._lock()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def log_issue(self, summary: str, location: str | None = None) -> Event:
        """Open a new issue and mark it active."""
        with self._lock():
            events = read_events(self.mem_dir)
            issue_id = next_issue_id(events)
            event = Event(
                type="issue",
                summary=summary,
                issue_id=issue_id,
                location=location,
                git_commit=get_git_commit(self.mem_dir.parent),
            )
            append_event(event, self.mem_dir)
            regenerate_summary(self.mem_dir, events=events + [event])
            write_current_issue(issue_id, self.mem_dir)
            return event

    def record_attempt(
        self,
        summary: str,
        outcome: str,
        location: str | None = None,
        issue_id: str | None = None,
        auto_issue: bool = False,
    ) -> Event:
        """Record a fix attempt, attached via the historical precedence order:
        explicit id → active-issue marker → open issue opened ≤5 minutes ago →
        (with ``auto_issue``) a fresh parent issue."""
        if outcome not in ("worked", "failed", "partial"):
            raise EngineError("Attempt outcome must be one of worked/failed/partial.")
        with self._lock():
            events = read_events(self.mem_dir)
            attached = issue_id or read_current_issue(self.mem_dir)
            if attached is None:
                attached = latest_open_issue_within(events)
            if attached is None:
                if not auto_issue:
                    raise EngineError(
                        "No active issue. Pass --issue, run pollux log first, or use "
                        "--auto-issue to open a parent issue from this attempt."
                    )
                attached = next_issue_id(events)
                parent = Event(
                    type="issue",
                    summary=summary,
                    issue_id=attached,
                    location=location,
                    git_commit=get_git_commit(self.mem_dir.parent),
                )
                append_event(parent, self.mem_dir)
                events.append(parent)
                write_current_issue(attached, self.mem_dir)
            elif not any(
                event.issue_id == attached for event in events
            ):
                raise EngineError(f"Issue #{attached} does not exist in this memory.")
            event = Event(
                type="attempt",
                summary=summary,
                issue_id=attached,
                outcome=outcome,
                location=location,
                git_commit=get_git_commit(self.mem_dir.parent),
            )
            append_event(event, self.mem_dir)
            regenerate_summary(self.mem_dir, events=events + [event])
            return event

    def record_fix(
        self, summary: str, location: str | None = None, issue_id: str | None = None
    ) -> Event:
        """Record a fix and close the issue (clearing the active marker)."""
        with self._lock():
            events = read_events(self.mem_dir)
            attached = issue_id or read_current_issue(self.mem_dir)
            if attached is None:
                raise EngineError(
                    "No active issue to fix. Pass --issue or run pollux log first."
                )
            if not any(event.issue_id == attached for event in events):
                raise EngineError(f"Issue #{attached} does not exist in this memory.")
            event = Event(
                type="fix",
                summary=summary,
                issue_id=attached,
                location=location,
                git_commit=get_git_commit(self.mem_dir.parent),
            )
            append_event(event, self.mem_dir)
            regenerate_summary(self.mem_dir, events=events + [event])
            clear_current_issue(self.mem_dir)
            return event

    def add_decision(
        self, summary: str, location: str | None = None, supersedes: str | None = None
    ) -> Event:
        """Record a decision, optionally retiring a prior one by id/prefix."""
        with self._lock():
            events = read_events(self.mem_dir)
            supersedes_id = None
            if supersedes:
                target = resolve_event_ref(events, supersedes)
                if target.type != "decision":
                    raise EngineError(
                        f"Event {target.id} is a {target.type}, not a decision; "
                        f"only decisions can be superseded."
                    )
                supersedes_id = target.id
            event = Event(
                type="decision",
                summary=summary,
                location=location,
                supersedes=supersedes_id,
                git_commit=get_git_commit(self.mem_dir.parent),
            )
            append_event(event, self.mem_dir)
            regenerate_summary(self.mem_dir, events=events + [event])
            return event

    def add_note(self, summary: str, location: str | None = None) -> Event:
        """Record a free-form note."""
        with self._lock():
            events = read_events(self.mem_dir)
            event = Event(
                type="note",
                summary=summary,
                location=location,
                git_commit=get_git_commit(self.mem_dir.parent),
            )
            append_event(event, self.mem_dir)
            regenerate_summary(self.mem_dir, events=events + [event])
            return event

    # ------------------------------------------------------------------
    # Read / maintain operations
    # ------------------------------------------------------------------

    def regenerate(self):
        with self._lock():
            return regenerate_summary(self.mem_dir)
