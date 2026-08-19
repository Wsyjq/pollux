"""Derived-file regeneration: ``summary.md`` and ``issues/*.md``.

The rendered layout (section order, issue lines, attempt bullets, decision
and note lists) reproduces the historical generator exactly — a regenerated
summary must be interchangeable with what the previous engine produced, or
every diff after cutover looks like a semantic change.

Two deliberate improvements over the historical generator:

- Issue files are synced incrementally: only files whose content actually
  changed are rewritten, and files that no longer correspond to an issue
  (e.g. renamed by the CJK-aware slug) are removed. Rewriting hundreds of
  untouched files per event was pure IO and git churn.
- Writes go through atomic replace, so a crashed regenerator cannot leave a
  half-written summary behind.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pollux.engine.models import (
    Event,
    slugify,
    superseded_ids,
)
from pollux.engine.storage import (
    issues_dir as issues_dir_path,
)
from pollux.engine.storage import (
    project_map_path,
    read_config,
    read_events,
    summary_path,
)
from pollux.files import write_text_atomic

# Placeholder phrases meaning "the purpose is still unset" — recognized in
# both PROJECT_MAP.md and legacy summaries so a placeholder never round-trips
# into real content forever.
_PLACEHOLDER_PHRASES = (
    "Not described yet.",
    "Short description of what the project does.",
    "Replace this placeholder",
    "Status: not created yet",
    "This file should be created by the first AI assistant",
)

_DEFAULT_PURPOSE = (
    "Replace this placeholder with a concise description of what this "
    "project does, who it serves, and the main technologies or runtime "
    "assumptions."
)


@dataclass
class RegenStats:
    summary_written: bool = False
    issues_written: int = 0
    issues_removed: int = 0
    issue_files_untouched: int = 0
    removed_paths: list[str] = field(default_factory=list)


def _looks_like_placeholder(text: str | None) -> bool:
    if not text or not text.strip():
        return True
    stripped = text.strip()
    return any(phrase in stripped for phrase in _PLACEHOLDER_PHRASES)


def extract_project_purpose_from_map(map_path: Path) -> str | None:
    """Read the ``## Project purpose`` section from PROJECT_MAP.md."""
    if not map_path.exists():
        return None
    content = map_path.read_text(encoding="utf-8")
    match = re.search(
        r"^## Project purpose\s*\n(?P<body>.*?)(?=\n## |\Z)",
        content,
        flags=re.DOTALL | re.MULTILINE,
    )
    if not match:
        return None
    body = match.group("body").strip()
    if _looks_like_placeholder(body):
        return None
    return body


def extract_project_purpose(summary: str) -> str | None:
    """Pull the purpose section out of an existing summary.md (legacy path)."""
    match = re.search(
        r"^## (?:Project purpose|What this project is)\n(?P<body>.*?)(?=\n## |\Z)",
        summary,
        flags=re.DOTALL | re.MULTILINE,
    )
    if not match:
        return None
    body = match.group("body").strip()
    if _looks_like_placeholder(body):
        return None
    return body


def group_issue_events(events: list[Event]) -> dict[str, list[Event]]:
    issues: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        if event.issue_id:
            issues[event.issue_id].append(event)
    return dict(issues)


def infer_file_mentions(text: str) -> list[str]:
    pattern = r"(?<![\w/.-])[\w./-]+\.[A-Za-z0-9]+(?::\d+)?"
    return re.findall(pattern, text)


def collect_files(events: list[Event]) -> list[str]:
    seen: set[str] = set()
    files: list[str] = []
    for event in events:
        for explicit in event.files:
            if explicit not in seen:
                seen.add(explicit)
                files.append(explicit)
        for inferred in infer_file_mentions(event.summary):
            if inferred not in seen:
                seen.add(inferred)
                files.append(inferred)
    return files


def build_summary(
    events: list[Event],
    root_name: str,
    project_purpose: str | None = None,
    recent_issues_limit: int = 0,
    decisions_limit: int = 0,
) -> str:
    """Render summary.md content.

    Byte-compatible with the historical format when ``recent_issues_limit``
    and ``decisions_limit`` are 0 (default). A positive limit caps the
    corresponding section to the N most recent entries and says so
    explicitly — a capped list that looks complete would be worse than a
    long one.
    """
    now = datetime.now(timezone.utc).date().isoformat()
    issues = group_issue_events(events)
    retired = superseded_ids(events)
    decisions = [
        event
        for event in events
        if event.type == "decision" and event.id not in retired
    ]
    notes = [event for event in events if event.type == "note"]

    lines = [
        f"# projectmem - {root_name}",
        "",
        f"_Last updated: {now}_",
        "",
        "## Project purpose",
        project_purpose or _DEFAULT_PURPOSE,
        "",
        "## Recent issues",
    ]

    if not issues:
        lines.append("- No issues logged yet.")
    else:
        sorted_issues = sorted(issues.items(), reverse=True)
        shown_issues = sorted_issues
        hidden = 0
        if recent_issues_limit > 0 and len(sorted_issues) > recent_issues_limit:
            shown_issues = sorted_issues[:recent_issues_limit]
            hidden = len(sorted_issues) - recent_issues_limit
        for issue_id, issue_events in shown_issues:
            issue = next(event for event in issue_events if event.type == "issue")
            fix = next(
                (event for event in reversed(issue_events) if event.type == "fix"), None
            )
            status = "fixed" if fix else "open"
            marker = "DONE" if fix else "OPEN"

            issue_loc = f" [{issue.location}]" if issue.location else ""
            if fix:
                fix_loc = f" [{fix.location}]" if fix.location else ""
                outcome = f" -> {fix.summary}{fix_loc}"
            else:
                outcome = ""

            lines.append(
                f"- [{marker}] #{issue_id} {issue.summary}{issue_loc}{outcome} ({status})"
            )
            lessons = [
                event
                for event in issue_events
                if event.type == "attempt" and event.outcome in ("failed", "partial")
            ]
            label = {"failed": "Failed attempt", "partial": "Partial attempt"}
            for lesson_event in lessons[-3:]:
                loc = f" [{lesson_event.location}]" if lesson_event.location else ""
                tag = label.get(lesson_event.outcome or "failed", "Attempt")
                lines.append(f"  - {tag}: {lesson_event.summary}{loc}")
        if hidden:
            lines.append(
                f"- ... {hidden} older issue(s) not listed "
                f"(details in .projectmem/issues/, use search to reach them)"
            )

    lines.extend(["", "## Decisions"])
    if decisions:
        shown = decisions
        hidden_decisions = 0
        if decisions_limit > 0 and len(decisions) > decisions_limit:
            shown = decisions[-decisions_limit:]
            hidden_decisions = len(decisions) - decisions_limit
        for event in shown:
            loc = f" [{event.location}]" if event.location else ""
            lines.append(f"- {event.summary}{loc}")
        if hidden_decisions:
            lines.append(
                f"- ... {hidden_decisions} older decision(s) not listed "
                f"(search to reach them)"
            )
    else:
        lines.append("- No decisions logged yet.")

    lines.extend(["", "## Notes"])
    if notes:
        for event in notes[-10:]:
            loc = f" [{event.location}]" if event.location else ""
            lines.append(f"- {event.summary}{loc}")
    else:
        lines.append("- No notes logged yet.")

    lines.extend(["", "## Key files"])
    key_files = collect_files(events)
    if key_files:
        for file_path in key_files[:20]:
            lines.append(f"- `{file_path}`")
    else:
        lines.append("- No key files logged yet.")

    lines.extend(["", "## Open questions"])
    lines.append("- None logged yet.")
    lines.append("")
    return "\n".join(lines)


def issue_file_content(issue_id: str, issue_events: list[Event]) -> str | None:
    """Render one ``issues/NNNN-slug.md`` file, or None if the group has no
    issue event (orphaned attachment events keep the file from regenerating)."""
    issue = next((event for event in issue_events if event.type == "issue"), None)
    if issue is None:
        return None
    lines = [f"# #{issue_id} {issue.summary}", ""]
    for event in issue_events:
        loc = f" [{event.location}]" if event.location else ""
        detail = f"- {event.timestamp} `{event.type}`: {event.summary}{loc}"
        if event.outcome:
            detail += f" ({event.outcome})"
        lines.append(detail)
    lines.append("")
    return "\n".join(lines)


def sync_issue_files(events: list[Event], mem: Path) -> tuple[int, int, int, list[str]]:
    """Bring ``issues/`` in line with the event log.

    Writes only files whose bytes differ; removes files whose issue vanished
    or whose name changed (slug change). Returns (written, removed, untouched,
    removed_paths).
    """
    target_dir = issues_dir_path(mem)
    target_dir.mkdir(parents=True, exist_ok=True)
    grouped = group_issue_events(events)

    expected: dict[str, str] = {}
    for issue_id, issue_events in grouped.items():
        content = issue_file_content(issue_id, issue_events)
        if content is None:
            continue
        issue = next(event for event in issue_events if event.type == "issue")
        name = f"{issue_id}-{slugify(issue.summary)}.md"
        expected[name] = content

    written = removed = untouched = 0
    removed_paths: list[str] = []
    for name, content in expected.items():
        path = target_dir / name
        if path.exists():
            try:
                if path.read_text(encoding="utf-8") == content:
                    untouched += 1
                    continue
            except OSError:
                pass  # unreadable → rewrite it
        # Platform-default newlines match what the historical writer produced
        # (CRLF on Windows), so a regenerated file never churns on line
        # endings alone.
        write_text_atomic(path, content, newline=None)
        written += 1

    for existing in target_dir.glob("*.md"):
        if existing.name not in expected:
            try:
                existing.unlink()
                removed += 1
                removed_paths.append(existing.name)
            except OSError:
                pass
    return written, removed, untouched, removed_paths


def regenerate_summary(mem: Path, events: list[Event] | None = None) -> RegenStats:
    """Regenerate summary.md and issue files from the event log.

    ``events`` may be passed by a caller that already holds them (the write
    lock holder does) to avoid a second parse of a large log.
    """
    if events is None:
        events = read_events(mem)

    stats = RegenStats()
    summary_file = summary_path(mem)

    existing_summary = ""
    if summary_file.exists():
        try:
            existing_summary = summary_file.read_text(encoding="utf-8")
        except OSError:
            existing_summary = ""

    try:
        map_purpose = extract_project_purpose_from_map(project_map_path(mem))
    except OSError:
        map_purpose = None
    project_purpose = map_purpose or extract_project_purpose(existing_summary)

    config = read_config(mem)
    content = build_summary(
        events,
        mem.parent.name,
        project_purpose=project_purpose,
        recent_issues_limit=config.recent_issues_limit,
        decisions_limit=config.decisions_limit,
    )
    if content != existing_summary:
        write_text_atomic(summary_file, content, newline=None)
        stats.summary_written = True

    written, removed, untouched, removed_paths = sync_issue_files(events, mem)
    stats.issues_written = written
    stats.issues_removed = removed
    stats.issue_files_untouched = untouched
    stats.removed_paths = removed_paths
    return stats


# Threshold above which MCP get_summary switches from the full file to a
# digest: ~12k chars ≈ 3k tokens. Beyond that, session-start reads cost more
# than they return — the historical summary listed *every* issue, and the
# client truncated the result invisibly, silently hiding older entries.
DIGEST_THRESHOLD_CHARS = 12_000
DIGEST_OPEN_ISSUES_MAX = 60
DIGEST_RECENT_CLOSED = 15
DIGEST_DECISIONS = 12


def _clip(text: str, limit: int = 140) -> str:
    """One-line clip for digest entries (whole-memory entries stay full in
    summary.md / issue files — the digest just can't afford them)."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in ("。", "；", ". ", "; ", " "):
        idx = cut.rfind(sep)
        if idx >= limit // 2:
            return cut[:idx].rstrip(",;:—- ") + " …"
    return cut.rstrip() + "…"


def build_summary_digest(
    events: list[Event],
    root_name: str,
    project_purpose: str | None = None,
) -> str:
    """A compact, pointer-rich summary view for token-budgeted callers.

    Unlike the full summary this always bounds every section: open issues up
    front (capped with disclosure), a recent-closed sample, latest decisions
    and notes, plus explicit pointers to the tools that reach the rest. The
    full file remains available via ``pollux show``.
    """
    now = datetime.now(timezone.utc).date().isoformat()
    grouped = group_issue_events(events)
    resolved = {
        event.issue_id for event in events if event.type == "fix" and event.issue_id
    }
    open_groups = [
        (issue_id, issue_events)
        for issue_id, issue_events in sorted(grouped.items(), reverse=True)
        if issue_id not in resolved
    ]
    closed_groups = [
        (issue_id, issue_events)
        for issue_id, issue_events in sorted(grouped.items(), reverse=True)
        if issue_id in resolved
    ]
    retired = superseded_ids(events)
    decisions = [
        event
        for event in events
        if event.type == "decision" and event.id not in retired
    ]
    notes = [event for event in events if event.type == "note"]

    lines = [
        f"# pollux summary digest — {root_name}",
        "",
        f"_Last updated: {now}; events: {len(events)}, open issues: "
        f"{len(open_groups)}, closed: {len(closed_groups)}, decisions: "
        f"{len(decisions)}_",
        "",
        "_Digest (the full summary exceeds the MCP size budget). Complete file: "
        "`pollux show`; one issue: `get_issue(id)`; anything: `search_events`._",
        "",
        "## Project purpose",
        project_purpose or _DEFAULT_PURPOSE,
        "",
        f"## Open issues ({len(open_groups)})",
    ]
    if not open_groups:
        lines.append("- None open.")
    else:
        shown_open = open_groups[:DIGEST_OPEN_ISSUES_MAX]
        for issue_id, issue_events in shown_open:
            issue = next(event for event in issue_events if event.type == "issue")
            loc = f" [{issue.location}]" if issue.location else ""
            lines.append(f"- #{issue_id} {_clip(issue.summary)}{loc}")
        if len(open_groups) > len(shown_open):
            hidden = len(open_groups) - len(shown_open)
            lines.append(f"- ... {hidden} more open issue(s) — use `get_issue`")

    lines.extend(["", f"## Recently closed (last {DIGEST_RECENT_CLOSED} of {len(closed_groups)})"])
    for issue_id, issue_events in closed_groups[:DIGEST_RECENT_CLOSED]:
        issue = next(event for event in issue_events if event.type == "issue")
        fix = next(
            (event for event in reversed(issue_events) if event.type == "fix"), None
        )
        outcome = f" -> {_clip(fix.summary)}" if fix else ""
        lines.append(f"- #{issue_id} {_clip(issue.summary)}{outcome}")

    lines.extend(["", f"## Decisions (latest {DIGEST_DECISIONS} of {len(decisions)})"])
    for event in decisions[-DIGEST_DECISIONS:]:
        lines.append(f"- {_clip(event.summary)}")

    lines.extend(["", "## Notes (latest 10)"])
    for event in notes[-10:]:
        lines.append(f"- {_clip(event.summary)}")

    lines.extend(["", "## Key files"])
    key_files = collect_files(events)
    if key_files:
        for file_path in key_files[:20]:
            lines.append(f"- `{file_path}`")
    else:
        lines.append("- No key files logged yet.")
    lines.append("")
    return "\n".join(lines)


def get_summary_view(mem: Path) -> str:
    """The full summary when small enough to be worth its tokens, otherwise
    a digest built from the same event log."""
    summary_file = summary_path(mem)
    if summary_file.exists():
        text = summary_file.read_text(encoding="utf-8")
        if len(text) <= DIGEST_THRESHOLD_CHARS:
            return text
    events, _skipped = _read_events_lenient_for_view(mem)
    try:
        purpose = extract_project_purpose_from_map(project_map_path(mem))
    except OSError:
        purpose = None
    if purpose is None and summary_file.exists():
        purpose = extract_project_purpose(summary_file.read_text(encoding="utf-8"))
    return build_summary_digest(events, mem.parent.name, project_purpose=purpose)


def _read_events_lenient_for_view(mem: Path) -> tuple[list[Event], list[int]]:
    from pollux.engine.storage import read_events_lenient

    return read_events_lenient(mem)
