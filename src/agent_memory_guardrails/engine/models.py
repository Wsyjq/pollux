"""Event model for the ``.projectmem/events.jsonl`` contract.

The serialization contract is fixed by the existing on-disk format: JSON
objects on one line each, keys sorted, empty values (``None``/``[]``/``False``)
omitted, timestamps in ISO-8601 Zulu. Any change here breaks every existing
memory, so this module mirrors that contract exactly rather than inventing a
"cleaner" schema.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from agent_memory_guardrails.engine.errors import EngineError

VALID_EVENT_TYPES = {
    "issue",
    "hypothesis",
    "attempt",
    "fix",
    "decision",
    "note",
}

VALID_OUTCOMES = {"worked", "failed", "partial"}

VALID_CAPTURE_SOURCES = {
    "git_post_commit",
    "git_post_revert",
    "git_post_merge",
    "churn_detector",
    "ci_parser",
}

VALID_CONFIDENCE_LEVELS = {"high", "medium", "low"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def normalize_timestamp(ts: str | None) -> str:
    """Canonicalize a timestamp to ISO-8601 Zulu (``YYYY-MM-DDTHH:MM:SSZ``).

    Accepts ISO-8601 (``Z`` or ``+00:00``), git's ``%ai`` format, and anything
    ``datetime.fromisoformat`` accepts. Unparseable input is returned unchanged
    so historical lines still round-trip; only new writes are guaranteed
    canonical.
    """
    if not ts:
        return utc_now_iso()
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S %z")
        except ValueError:
            return ts
    return (
        parsed.astimezone(timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


@dataclass
class Event:
    type: str
    summary: str
    id: str = field(default_factory=lambda: f"evt_{uuid4().hex[:20]}")
    timestamp: str = field(default_factory=utc_now_iso)
    issue_id: str | None = None
    outcome: str | None = None
    files: list[str] = field(default_factory=list)
    command: str | None = None
    notes: str | None = None
    git_commit: str | None = None
    location: str | None = None
    auto_captured: bool = False
    capture_source: str | None = None
    capture_confidence: str | None = None
    git_message: str | None = None
    # Read-time retirement pointer: this event supersedes the referenced id.
    # The log itself stays append-only; superseded events are filtered from
    # derived views, never deleted.
    supersedes: str | None = None

    def __post_init__(self) -> None:
        if self.type not in VALID_EVENT_TYPES:
            raise ValueError(f"Unsupported event type: {self.type}")
        if self.outcome is not None and self.outcome not in VALID_OUTCOMES:
            raise ValueError(f"Unsupported outcome: {self.outcome}")
        if self.capture_source is not None and self.capture_source not in VALID_CAPTURE_SOURCES:
            raise ValueError(f"Unsupported capture source: {self.capture_source}")
        if self.capture_confidence is not None and (
            self.capture_confidence not in VALID_CONFIDENCE_LEVELS
        ):
            raise ValueError(f"Unsupported confidence level: {self.capture_confidence}")
        self.summary = self.summary.strip()
        if not self.summary:
            raise ValueError("Event summary cannot be empty")

    def to_dict(self) -> dict[str, Any]:
        # Omit falsy empties (None/[]/False) exactly like the historical
        # writer so regenerated lines stay byte-identical.
        data = asdict(self)
        return {key: value for key, value in data.items() if value not in (None, [], False)}

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Event:
        return cls(
            id=data.get("id") or f"evt_{uuid4().hex[:20]}",
            timestamp=(
                normalize_timestamp(data.get("timestamp"))
                if data.get("timestamp")
                else utc_now_iso()
            ),
            type=data["type"],
            issue_id=data.get("issue_id"),
            summary=data["summary"],
            outcome=data.get("outcome"),
            files=list(data.get("files") or []),
            command=data.get("command"),
            notes=data.get("notes"),
            git_commit=data.get("git_commit"),
            location=data.get("location"),
            auto_captured=bool(data.get("auto_captured", False)),
            capture_source=data.get("capture_source"),
            capture_confidence=data.get("capture_confidence"),
            git_message=data.get("git_message"),
            supersedes=data.get("supersedes"),
        )


def superseded_ids(events: list[Event]) -> set[str]:
    """IDs retired by some later event's ``supersedes`` pointer."""
    return {event.supersedes for event in events if event.supersedes}


def resolve_event_ref(events: list[Event], ref: str) -> Event:
    """Resolve a full event id or a unique hex-prefix reference.

    Raises ``EngineError`` when nothing matches or the prefix is ambiguous —
    callers surface the message instead of guessing.
    """
    needle = ref.strip()
    if not needle:
        raise EngineError("Empty event reference")
    candidates = [event for event in events if event.id == needle]
    if not candidates:
        bare = needle.removeprefix("evt_")
        candidates = [
            event for event in events if event.id.removeprefix("evt_").startswith(bare)
        ]
    if not candidates:
        raise EngineError(
            f"No event matches '{ref}'. Use search to find the event id (shown as evt_...)."
        )
    if len(candidates) > 1:
        raise EngineError(
            f"Event reference '{ref}' is ambiguous ({len(candidates)} matches) — "
            f"use more characters of the id."
        )
    return candidates[0]


_CJK_RANGES = (
    ("\u3400", "\u4dbf"),  # CJK Extension A
    ("\u4e00", "\u9fff"),  # CJK Unified Ideographs
    ("\uf900", "\ufaff"),  # CJK Compatibility Ideographs
)


def _is_cjk(char: str) -> bool:
    return any(low <= char <= high for low, high in _CJK_RANGES)


def slugify(text: str, max_length: int = 48) -> str:
    """Slugify a summary for issue filenames.

    ASCII text produces the same slug as the historical writer (lowercase
    alphanumerics joined by ``-``). CJK characters are preserved instead of
    being stripped — the historical writer collapsed every Chinese summary to
    ``NNNN-issue.md``, making issue files indistinguishable on disk.
    """
    lowered = text.lower()
    parts: list[str] = []
    current: list[str] = []
    for char in lowered:
        if char.isalnum() and (char.isascii() or _is_cjk(char)):
            current.append(char)
        elif current:
            parts.append("".join(current))
            current = []
    if current:
        parts.append("".join(current))
    slug = "-".join(parts)
    return slug[:max_length].strip("-") or "issue"


def issue_id_from_filename(name: str) -> str | None:
    """Extract the zero-padded issue id from an ``issues/NNNN-slug.md`` name."""
    match = re.match(r"^(\d{4})-", name)
    return match.group(1) if match else None
