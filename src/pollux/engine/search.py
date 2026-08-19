"""Event search: case-insensitive substring or regex over the fields that
carry meaning, including ``git_commit`` — the historical search ignored it,
which forced hook verification to grep the raw JSONL by hand."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from pollux.engine.models import Event
from pollux.engine.storage import read_events_lenient


def _event_matches(event: Event, matcher) -> bool:
    if matcher(event.summary):
        return True
    if event.notes and matcher(event.notes):
        return True
    if event.location and matcher(event.location):
        return True
    if event.git_commit and matcher(event.git_commit):
        return True
    if event.git_message and matcher(event.git_message):
        return True
    return any(matcher(file_path) for file_path in event.files)


def search_events(
    mem: Path,
    query: str,
    regex: bool = False,
    failed_only: bool = False,
    include_archived: bool = False,
    ranked: bool = False,
) -> list[Event]:
    """Search the event log; default order is log order (like the historical
    command), ``ranked=True`` sorts by relevance first. A bad regex falls
    back to literal substring. ``include_archived`` also searches archived
    event files."""
    events, _skipped = read_events_lenient(mem)
    if include_archived:
        from pollux.engine.archive import read_archived_events

        events = events + read_archived_events(mem)
    if regex:
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error:
            return search_events(
                mem, query, regex=False, failed_only=failed_only,
                include_archived=include_archived, ranked=ranked,
            )
        matcher = pattern.search
    else:
        needle = query.casefold()

        def matcher(text: str) -> bool:
            return needle in text.casefold()

    results = [
        event
        for event in events
        if _event_matches(event, matcher)
        and (not failed_only or (event.type == "attempt" and event.outcome == "failed"))
    ]
    if ranked:
        resolved = {
            event.issue_id for event in events if event.type == "fix" and event.issue_id
        }
        order = {id(event): index for index, event in enumerate(results)}
        results.sort(
            key=lambda event: (-relevance_score(event, query, resolved), order[id(event)])
        )
    return results


def relevance_score(event: Event, query: str, resolved_issue_ids: set[str]) -> float:
    """Cheap relevance heuristic for ranked search.

    Weights favor what a working session most needs first: events on still-open
    issues, unresolved failures, recency, and (when the query looks like a
    path) direct file hits. Deliberately transparent, no external services.
    """
    score = {"issue": 3, "attempt": 2, "fix": 2, "decision": 2, "note": 1}.get(
        event.type, 1
    )
    if event.type == "attempt" and event.outcome in ("failed", "partial"):
        score += 2
    if event.issue_id and event.issue_id not in resolved_issue_ids:
        score += 3
    try:
        ts = datetime.fromisoformat(event.timestamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        ts = None
    if ts is not None:
        age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400
        if age_days <= 7:
            score += 2
        elif age_days <= 30:
            score += 1
    looks_like_path = "/" in query or "." in query
    if looks_like_path:
        needle = query.casefold()
        if (event.location and needle in event.location.casefold()) or any(
            needle in file_path.casefold() for file_path in event.files
        ):
            score += 2
    return score


def format_result(event: Event) -> str:
    line = f"{event.timestamp} [{event.id}] {event.type}: {event.summary}"
    if event.location:
        line += f" [{event.location}]"
    if event.outcome:
        line += f" ({event.outcome})"
    return line
