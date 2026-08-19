"""In-memory inverted index over the event log.

Built once per command invocation from the parsed events (no disk cache —
the historical timeout was caused by per-file rescans and a git-subprocess
storm, not by the single O(N) parse, so a persistent cache would add
staleness complexity for no measurable win at project-memory scale).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agent_memory_guardrails.engine.models import Event, superseded_ids


@dataclass
class MemoryIndex:
    """Lookup structures for the queries the CLI and MCP server run.

    ``events`` keeps the original objects in log order; everything else maps
    keys back to those objects, so matching semantics stay identical to a
    linear scan.
    """

    events: list[Event] = field(default_factory=list)
    by_file: dict[str, list[Event]] = field(default_factory=dict)
    by_location_file: dict[str, list[Event]] = field(default_factory=dict)
    by_issue: dict[str, list[Event]] = field(default_factory=dict)
    by_commit: dict[str, Event] = field(default_factory=dict)
    resolved_issues: set[str] = field(default_factory=set)

    @classmethod
    def build(cls, events: list[Event]) -> MemoryIndex:
        index = cls(events=events)
        for event in events:
            for file_path in event.files:
                index.by_file.setdefault(file_path, []).append(event)
            if event.location:
                loc_file = event.location.split(":")[0].strip()
                if loc_file:
                    index.by_location_file.setdefault(loc_file, []).append(event)
            if event.issue_id:
                index.by_issue.setdefault(event.issue_id, []).append(event)
            if event.git_commit:
                index.by_commit.setdefault(event.git_commit, event)
            if event.type == "fix" and event.issue_id:
                index.resolved_issues.add(event.issue_id)
        return index

    def retired_ids(self) -> set[str]:
        return superseded_ids(self.events)

    def events_for_file(self, file_path: str) -> list[Event]:
        """Events referencing ``file_path`` — the same three-way match the
        historical precheck used: exact ``files`` entry, location file part,
        or a summary mention (summary mentions need the linear scan, but only
        over events not already matched, once per invocation)."""
        seen: set[int] = set()
        matched: list[Event] = []
        for event in self.by_file.get(file_path, []):
            seen.add(id(event))
            matched.append(event)
        for event in self.by_location_file.get(file_path, []):
            if id(event) not in seen:
                seen.add(id(event))
                matched.append(event)
        needle = file_path.casefold()
        for event in self.events:
            if id(event) in seen:
                continue
            if needle in event.summary.casefold():
                matched.append(event)
        # Preserve log order for stable output.
        order = {id(event): position for position, event in enumerate(self.events)}
        matched.sort(key=lambda event: order[id(event)])
        return matched

    def open_issues(self) -> list[Event]:
        return [
            event
            for event in self.events
            if event.type == "issue" and event.issue_id not in self.resolved_issues
        ]
