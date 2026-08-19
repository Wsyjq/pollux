"""Pre-change safety check: what does memory say about the files you are
about to touch?

Semantics follow the historical command (open issues on the file, failed and
partial attempts, stale decisions/notes, recent churn), but staleness and
churn come from a single batched git-log pass instead of one subprocess per
(file, timestamp) pair — that storm is what made precheck time out on large
memories.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pollux.engine.gitmeta import churn_since, file_histories_since
from pollux.engine.index import MemoryIndex
from pollux.engine.models import Event
from pollux.engine.storage import read_events_lenient

STALE_COMMIT_THRESHOLD = 3
STALE_CHECKED_TYPES = ("decision", "fix", "note")
FAILED_ATTEMPTS_ESCALATION = 3


def location_file(event: Event) -> str | None:
    """File part of an event's location (``src/auth.py:42`` -> ``src/auth.py``).

    Locations like ``class AuthHandler`` or ``deploy pipeline`` are not paths
    and are ignored, matching the historical behavior.
    """
    if not event.location:
        return None
    file_part = event.location.split(":")[0].strip()
    if not file_part or ("/" not in file_part and "." not in file_part):
        return None
    return file_part


@dataclass
class FileReport:
    path: str
    open_issues: list[Event] = field(default_factory=list)
    failed_attempts: list[Event] = field(default_factory=list)
    partial_attempts: list[Event] = field(default_factory=list)
    stale_events: list[tuple[Event, str, int]] = field(default_factory=list)
    churn_commits: int = 0

    @property
    def severity(self) -> str:
        if len(self.failed_attempts) >= FAILED_ATTEMPTS_ESCALATION or self.stale_events:
            return "block"
        if self.open_issues or self.failed_attempts or self.partial_attempts:
            return "warn"
        return "info"

    @property
    def has_findings(self) -> bool:
        return bool(
            self.open_issues
            or self.failed_attempts
            or self.partial_attempts
            or self.stale_events
        )


@dataclass
class PrecheckReport:
    files: list[FileReport] = field(default_factory=list)
    git_available: bool = True

    def max_severity(self, level: str) -> bool:
        """True when the report should block at the given min level."""
        order = {"info": 0, "warn": 1, "block": 2}
        threshold = order.get(level, 1)
        return any(order.get(report.severity, 0) >= threshold for report in self.files)

    def render_text(self) -> str:
        lines: list[str] = []
        for report in self.files:
            tag = {"info": "OK", "warn": "WARN", "block": "RISK"}[report.severity]
            lines.append(f"[{tag}] {report.path}")
            for issue in report.open_issues:
                lines.append(f"    open issue #{issue.issue_id}: {issue.summary}")
            for attempt in report.failed_attempts:
                lines.append(
                    f"    failed attempt ({attempt.issue_id or '-'}): {attempt.summary}"
                )
            for attempt in report.partial_attempts:
                lines.append(
                    f"    partial attempt ({attempt.issue_id or '-'}): {attempt.summary}"
                )
            for event, _stale_file, commits in report.stale_events:
                reason = (
                    "file no longer exists"
                    if commits < 0
                    else f"{commits} commit(s) since this was recorded"
                )
                lines.append(f"    stale {event.type}: {event.summary} ({reason})")
            if not report.has_findings:
                if report.churn_commits:
                    lines.append(
                        f"    no memory findings; {report.churn_commits} commit(s) in last 30d"
                    )
                else:
                    lines.append("    no memory findings")
        if not self.git_available:
            lines.append("note: git unavailable — staleness/churn not evaluated")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "git_available": self.git_available,
            "files": [
                {
                    "path": report.path,
                    "severity": report.severity,
                    "open_issues": [
                        {"issue_id": issue.issue_id, "summary": issue.summary}
                        for issue in report.open_issues
                    ],
                    "failed_attempts": [attempt.summary for attempt in report.failed_attempts],
                    "partial_attempts": [attempt.summary for attempt in report.partial_attempts],
                    "stale_events": [
                        {
                            "type": event.type,
                            "summary": event.summary,
                            "file": file_path,
                            "commits_since": commits,
                        }
                        for event, file_path, commits in report.stale_events
                    ],
                    "churn_commits_30d": report.churn_commits,
                }
                for report in self.files
            ],
        }


def precheck_files(
    mem: Path,
    files: list[str],
    project_root: Path | None = None,
    require_repo: bool = True,
) -> PrecheckReport:
    """Analyze ``files`` against the memory.

    ``project_root`` is where git runs (the repo being edited); it defaults
    to the memory's parent. When it is not a git repository, staleness and
    churn are reported as unavailable rather than guessed.
    """
    events, _skipped = read_events_lenient(mem)
    index = MemoryIndex.build(events)
    retired = index.retired_ids()
    resolved = index.resolved_issues
    report = PrecheckReport()

    root = (project_root or mem.parent).resolve()

    # One git pass covering the earliest event timestamp answers both
    # staleness ("commits since this was recorded") and churn (last 30 days).
    timestamps = [event.timestamp for event in events if event.timestamp]
    histories: dict | None = {}
    since_iso = min(timestamps) if timestamps else "1970-01-01T00:00:00Z"
    if require_repo and timestamps:
        histories = file_histories_since(root, since_iso)
        report.git_available = histories is not None

    stale_by_file: dict[str, list[tuple[Event, str, int]]] = {}
    if histories:
        for event in events:
            if event.type not in STALE_CHECKED_TYPES or event.id in retired:
                continue
            file_path = location_file(event)
            if not file_path:
                continue
            if not (root / file_path).exists():
                stale_by_file.setdefault(file_path, []).append((event, file_path, -1))
                continue
            history = histories.get(file_path)
            if history is None:
                continue
            commits = history.commits_since(event.timestamp)
            if commits >= STALE_COMMIT_THRESHOLD:
                stale_by_file.setdefault(file_path, []).append((event, file_path, commits))

    for file_path in files:
        normalized = file_path.replace("\\", "/")
        file_events = index.events_for_file(normalized)
        file_report = FileReport(path=normalized)
        for event in file_events:
            if event.type == "issue" and event.issue_id not in resolved:
                file_report.open_issues.append(event)
            elif event.type == "attempt" and event.outcome == "failed":
                file_report.failed_attempts.append(event)
            elif event.type == "attempt" and event.outcome == "partial":
                file_report.partial_attempts.append(event)
        if histories:
            file_report.stale_events = stale_by_file.get(normalized, [])
            file_report.churn_commits = churn_since(histories, normalized)
        report.files.append(file_report)
    return report
