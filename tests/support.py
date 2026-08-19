"""Shared helpers for engine tests: minimal memory skeletons and corpus
builders. The engine's own ``init`` lands in a later milestone, so tests
create the minimal ``.projectmem/`` layout directly."""
from __future__ import annotations

from pathlib import Path

from pollux.engine.models import Event
from pollux.engine.storage import serialize_event

CONFIG_DEFAULTS = 'summary_size_limit_kb = 20\nrecent_days = 30\nproject_description = ""\n'


def init_memory(root: Path, purpose: str = "Test project.") -> Path:
    """Create a minimal initialized memory under ``root`` and return its path."""
    mem = root / ".projectmem"
    (mem / "issues").mkdir(parents=True, exist_ok=True)
    (mem / "events.jsonl").write_text("", encoding="utf-8")
    (mem / "config.toml").write_text(CONFIG_DEFAULTS, encoding="utf-8")
    (mem / "PROJECT_MAP.md").write_text(
        f"# Project Map - {root.name}\n\n## Project purpose\n{purpose}\n",
        encoding="utf-8",
    )
    return mem


def write_events(mem: Path, events: list[Event]) -> None:
    """Write events directly (fast path for fixtures; bypasses the lock)."""
    path = mem / "events.jsonl"
    path.write_text(
        "".join(serialize_event(event) + "\n" for event in events), encoding="utf-8"
    )


def fixed_event(event_type: str, summary: str, event_id: str, **kwargs) -> Event:
    """An Event with a fixed id and timestamp for deterministic goldens."""
    return Event(
        type=event_type,
        summary=summary,
        id=event_id,
        timestamp="2026-08-01T00:00:00Z",
        **kwargs,
    )
