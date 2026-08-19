"""Event lifecycle: archive closed-and-old issues out of the active log.

The active log is what every session reads at startup; without lifecycle
management it grows without bound and drags summary regeneration, search,
and context along with it. Archiving moves the events of issues that were
*closed* (have a fix) before a cutoff date into
``.projectmem/archive/events-<stamp>.jsonl``, verbatim, and rewrites the
active log without them — the only place the "append-only" log is ever
rewritten, done under the write lock with every moved line preserved byte
for byte in the archive file.

Decisions and unattached notes are never archived: they are the long-lived
memory. Only issue-attached event groups (an issue that is closed and old)
move. Everything is reversible via restore, which merges archived lines back
in stable timestamp order and regenerates.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from pollux.engine.errors import EngineError
from pollux.engine.locking import DirLock
from pollux.engine.models import Event
from pollux.engine.storage import (
    ARCHIVE_DIR,
    EVENTS_FILE,
    read_events,
    read_events_lenient,
    serialize_event,
)
from pollux.engine.summary import group_issue_events, regenerate_summary
from pollux.files import write_text_atomic


def archive_dir(mem: Path) -> Path:
    return mem / ARCHIVE_DIR


def manifest_path(mem: Path) -> Path:
    return archive_dir(mem) / "manifest.jsonl"


def archive_files(mem: Path) -> list[Path]:
    directory = archive_dir(mem)
    if not directory.is_dir():
        return []
    return sorted(directory.glob("events-*.jsonl"))


def read_archived_events(mem: Path) -> list[Event]:
    events: list[Event] = []
    for path in archive_files(mem):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(Event.from_dict(json.loads(line)))
    return events


def _append_manifest(mem: Path, entry: dict) -> None:
    archive_dir(mem).mkdir(parents=True, exist_ok=True)
    with manifest_path(mem).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


@dataclass
class ArchivePlan:
    keep: list[Event] = field(default_factory=list)
    archived: list[Event] = field(default_factory=list)
    archived_issues: list[str] = field(default_factory=list)
    archived_decisions: int = 0

    @property
    def archived_count(self) -> int:
        return len(self.archived)


def plan_archive(
    events: list[Event],
    before: date | None,
    closed_only: bool = True,
    decisions_before: date | None = None,
) -> ArchivePlan:
    """Split events into (keep, archive) by issue group and, optionally, age.

    An issue group is archivable when it has a fix event whose timestamp
    falls before ``before`` (with ``closed_only``; without it, an issue
    whose most recent event is old also qualifies — off by default because
    an open-but-stale issue is exactly the thing a future session may need
    front and center).

    ``decisions_before`` additionally retires *decision* events older than
    that date. Decisions are long-lived memory, so this is strictly opt-in
    and independent of issue archiving. Limitation: a decision moved to the
    archive can no longer be referenced by ``decision --supersedes`` (the
    resolver only sees the active log) — restore first if it must be
    superseded.
    """
    grouped = group_issue_events(events)
    archivable: set[str] = set()
    if before is not None:
        for issue_id, issue_events in grouped.items():
            fixes = [e for e in issue_events if e.type == "fix"]
            if closed_only:
                cutoff_met = any(_event_date(fix) < before for fix in fixes)
            else:
                last_ts = max((e.timestamp for e in issue_events), default="")
                cutoff_met = bool(last_ts) and _ts_date(last_ts) < before
            if cutoff_met:
                archivable.add(issue_id)

    plan = ArchivePlan()
    for event in events:
        if event.issue_id and event.issue_id in archivable:
            plan.archived.append(event)
        elif (
            decisions_before is not None
            and event.type == "decision"
            and _event_date(event) < decisions_before
        ):
            plan.archived.append(event)
            plan.archived_decisions += 1
        else:
            plan.keep.append(event)
    plan.archived_issues = sorted(archivable)
    return plan


def _ts_date(timestamp: str) -> date | None:
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        return None


def _event_date(event: Event) -> date:
    parsed = _ts_date(event.timestamp)
    if parsed is not None:
        return parsed
    return date(1970, 1, 1)


def run_archive(
    mem: Path,
    before: date | None = None,
    closed_only: bool = True,
    dry_run: bool = False,
    decisions_before: date | None = None,
) -> ArchivePlan:
    """Archive old closed issues and/or old decisions; returns the plan."""
    with DirLock(mem / "write.lock"):
        events = read_events(mem)
        plan = plan_archive(
            events,
            before,
            closed_only=closed_only,
            decisions_before=decisions_before,
        )
        if dry_run or not plan.archived:
            return plan

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive_file = archive_dir(mem) / f"events-{stamp}.jsonl"
        archive_dir(mem).mkdir(parents=True, exist_ok=True)
        archive_file.write_text(
            "".join(serialize_event(event) + "\n" for event in plan.archived),
            encoding="utf-8",
        )

        # Rewrite the active log without the archived lines. Atomic replace;
        # the archived copy already exists on disk at this point. Platform-
        # default newlines match the append path (the historical writer used
        # text-mode appends, i.e. CRLF on Windows).
        write_text_atomic(
            mem / EVENTS_FILE,
            "".join(serialize_event(event) + "\n" for event in plan.keep),
            newline=None,
        )

        _append_manifest(
            mem,
            {
                "op": "archive",
                "file": archive_file.name,
                "before": before.isoformat() if before else None,
                "decisionsBefore": (
                    decisions_before.isoformat() if decisions_before else None
                ),
                "closed_only": closed_only,
                "issues": len(plan.archived_issues),
                "decisions": plan.archived_decisions,
                "events": plan.archived_count,
            },
        )

        regenerate_summary(mem, events=plan.keep)
        return plan


@dataclass
class RestoreReport:
    restored_events: int = 0
    files_consumed: list[str] = field(default_factory=list)


def run_restore(mem: Path) -> RestoreReport:
    """Merge every archive file back into the active log and regenerate.

    Merge order is a stable sort by timestamp, which preserves the original
    relative order of same-second events from each source. The result is the
    same multiset of event lines that existed before archiving.
    """
    with DirLock(mem / "write.lock"):
        files = archive_files(mem)
        if not files:
            raise EngineError("No archive files to restore.")
        active, _skipped = read_events_lenient(mem)
        archived: list[Event] = []
        for path in files:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    archived.append(Event.from_dict(json.loads(line)))
        merged = sorted(active + archived, key=lambda event: event.timestamp)
        write_text_atomic(
            mem / EVENTS_FILE,
            "".join(serialize_event(event) + "\n" for event in merged),
            newline=None,
        )
        report = RestoreReport(restored_events=len(archived))
        for path in files:
            report.files_consumed.append(path.name)
            path.unlink()
        _append_manifest(
            mem,
            {
                "op": "restore",
                "events": report.restored_events,
                "files": report.files_consumed,
            },
        )
        regenerate_summary(mem, events=merged)
        return report


@dataclass
class ArchiveStatus:
    active_events: int = 0
    archived_events: int = 0
    archive_files: list[str] = field(default_factory=list)
    closed_issues_total: int = 0
    closed_issues_archivable_before: dict[str, int] = field(default_factory=dict)


def archive_status(mem: Path) -> ArchiveStatus:
    events, _skipped = read_events_lenient(mem)
    archived = read_archived_events(mem)
    status = ArchiveStatus(
        active_events=len(events),
        archived_events=len(archived),
        archive_files=[path.name for path in archive_files(mem)],
    )
    grouped = group_issue_events(events)
    status.closed_issues_total = sum(
        1 for issue_events in grouped.values() if any(e.type == "fix" for e in issue_events)
    )
    today = datetime.now(timezone.utc).date()
    for days in (30, 90, 180):
        cutoff = today.fromordinal(today.toordinal() - days)
        plan = plan_archive(events, cutoff)
        status.closed_issues_archivable_before[f"{days}d"] = len(plan.archived_issues)
    return status
