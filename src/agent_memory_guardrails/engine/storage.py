"""Storage layer for the ``.projectmem/`` directory.

Root discovery walks up like git does for ``.git/`` — this is what makes the
family topology work: a worktree or sub-repo resolves to the shared ancestor
memory. A candidate only counts as a project memory when it has a
``config.toml``, which is what distinguishes it from the machine-wide global
store at ``~/.projectmem/``.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_memory_guardrails.engine.errors import EngineError
from agent_memory_guardrails.engine.models import Event
from agent_memory_guardrails.engine.redaction import redact_event_fields

MEM_DIR = ".projectmem"
SUMMARY_FILE = "summary.md"
EVENTS_FILE = "events.jsonl"
CONFIG_FILE = "config.toml"
ISSUES_DIR = "issues"
AI_INSTRUCTIONS_FILE = "AI_INSTRUCTIONS.md"
PROJECT_MAP_FILE = "PROJECT_MAP.md"
PLAN_FILE = "plan.md"
CACHE_DIR = "cache"
ARCHIVE_DIR = "archive"
CURRENT_ISSUE_MARKER = ".current_issue"

REQUIRED_FILES = (
    CONFIG_FILE,
    EVENTS_FILE,
    SUMMARY_FILE,
    PROJECT_MAP_FILE,
    PLAN_FILE,
    AI_INSTRUCTIONS_FILE,
)


def mem_path(root: Path | None = None) -> Path:
    return (root or Path.cwd()) / MEM_DIR


def _is_project_mem_dir(candidate: Path) -> bool:
    # config.toml is the marker of an initialized project memory; the global
    # store never has one, so walk-up from $HOME cannot misresolve there.
    return candidate.is_dir() and (candidate / CONFIG_FILE).exists()


def discover_mem_dir(start: Path | None = None) -> Path | None:
    """Walk up from ``start`` looking for an initialized ``.projectmem/``."""
    current = (start or Path.cwd()).resolve()
    for path in [current, *current.parents]:
        candidate = path / MEM_DIR
        if _is_project_mem_dir(candidate):
            return candidate
    return None


def require_mem_dir(root: Path | None = None) -> Path:
    """Resolve the memory directory: explicit root, env, cwd, then walk-up."""
    if root is not None:
        path = mem_path(root)
        if path.exists():
            return path
        raise EngineError(f"No .projectmem directory found in {root}. Run amguard init.")

    env_root = os.environ.get("PROJECTMEM_ROOT")
    if env_root:
        path = Path(env_root).expanduser().resolve() / MEM_DIR
        if path.is_dir():
            return path
        raise EngineError(
            f"PROJECTMEM_ROOT points at {env_root}, which has no .projectmem directory."
        )

    cwd_path = mem_path(None)
    if _is_project_mem_dir(cwd_path):
        return cwd_path
    found = discover_mem_dir(None)
    if found is not None:
        return found
    raise EngineError(
        f"No .projectmem directory found in {Path.cwd()} or any parent. "
        f"If running over MCP, set the project root in the MCP client config or "
        f"via the PROJECTMEM_ROOT environment variable."
    )


def memory_root(mem_dir: Path) -> Path:
    """The project directory that owns ``mem_dir`` (its parent)."""
    return mem_dir.parent


def events_path(mem: Path | None = None, *, root: Path | None = None) -> Path:
    return (mem if mem is not None else require_mem_dir(root)) / EVENTS_FILE


def summary_path(mem: Path | None = None, *, root: Path | None = None) -> Path:
    return (mem if mem is not None else require_mem_dir(root)) / SUMMARY_FILE


def config_path(mem: Path | None = None, *, root: Path | None = None) -> Path:
    return (mem if mem is not None else require_mem_dir(root)) / CONFIG_FILE


def project_map_path(mem: Path | None = None, *, root: Path | None = None) -> Path:
    return (mem if mem is not None else require_mem_dir(root)) / PROJECT_MAP_FILE


def plan_path(mem: Path | None = None, *, root: Path | None = None) -> Path:
    return (mem if mem is not None else require_mem_dir(root)) / PLAN_FILE


def issues_dir(mem: Path | None = None, *, root: Path | None = None) -> Path:
    return (mem if mem is not None else require_mem_dir(root)) / ISSUES_DIR


def cache_dir(mem: Path | None = None, *, root: Path | None = None) -> Path:
    return (mem if mem is not None else require_mem_dir(root)) / CACHE_DIR


def current_issue_marker_path(mem: Path | None = None, *, root: Path | None = None) -> Path:
    return (mem if mem is not None else require_mem_dir(root)) / CURRENT_ISSUE_MARKER


@dataclass
class EngineConfig:
    """Configuration that the engine actually reads (unlike the historical
    writer, which wrote these keys but never parsed them)."""

    summary_size_limit_kb: int = 20
    recent_days: int = 30
    project_description: str = ""
    # 0 keeps the historical unbounded Recent issues list; a positive value
    # caps the summary's issue section (disclosed inside the summary).
    recent_issues_limit: int = 0
    # 0 keeps every live decision listed; a positive value caps the section
    # to the N most recent decisions (disclosed inside the summary).
    decisions_limit: int = 0


def read_config(mem: Path) -> EngineConfig:
    """Parse the simple ``key = value`` config file.

    The file stays intentionally line-oriented so it remains editable by hand
    and diffable in git; unknown keys are ignored so future keys never break
    older checkouts. Malformed files fall back to defaults rather than
    blocking every memory command.
    """
    config = EngineConfig()
    path = mem / CONFIG_FILE
    if not path.exists():
        return config
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return config
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, raw = stripped.partition("=")
        key = key.strip()
        value = raw.strip().strip('"').strip("'")
        if key == "summary_size_limit_kb":
            try:
                config.summary_size_limit_kb = int(value)
            except ValueError:
                continue
        elif key == "recent_days":
            try:
                config.recent_days = int(value)
            except ValueError:
                continue
        elif key == "project_description":
            config.project_description = value
        elif key == "recent_issues_limit":
            try:
                config.recent_issues_limit = max(0, int(value))
            except ValueError:
                continue
        elif key == "decisions_limit":
            try:
                config.decisions_limit = max(0, int(value))
            except ValueError:
                continue
    return config


def read_events(mem: Path | None = None, *, root: Path | None = None) -> list[Event]:
    """Read all events strictly — a corrupt line is an error, not a skip."""
    path = events_path(mem, root=root)
    return parse_events_text(path.read_text(encoding="utf-8"), source=str(path))


def parse_events_text(text: str, source: str = "events.jsonl") -> list[Event]:
    events: list[Event] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            events.append(Event.from_dict(json.loads(line)))
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise EngineError(f"Invalid event at {source}:{line_number}: {exc}") from exc
    return events


def read_events_lenient(
    mem: Path | None = None, *, root: Path | None = None
) -> tuple[list[Event], list[int]]:
    """Read events, skipping corrupt lines instead of failing.

    Used by read paths (search, index build, MCP reads): a torn trailing line
    from a crashed writer must not take the whole memory offline. Returns the
    events plus the line numbers that were skipped.
    """
    path = events_path(mem, root=root)
    if not path.exists():
        return [], []
    events: list[Event] = []
    skipped: list[int] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            events.append(Event.from_dict(json.loads(line)))
        except (json.JSONDecodeError, KeyError, ValueError):
            skipped.append(line_number)
    return events, skipped


def serialize_event(event: Event) -> str:
    """One JSON line, sorted keys, ASCII-escaped — the historical writer's
    exact serialization, kept byte-identical so mixed-engine histories stay
    homogeneous."""
    return json.dumps(event.to_dict(), sort_keys=True)


def append_event(event: Event, mem: Path) -> Event:
    """Redact then append one event line. Caller holds the write lock."""
    fired = redact_event_fields(event)
    if fired:
        kinds = ", ".join(sorted(set(fired)))
        print(
            f"amguard: redacted {len(fired)} secret(s) before write ({kinds})",
            file=sys.stderr,
        )
    path = events_path(mem)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(serialize_event(event) + "\n")
        handle.flush()
    return event


def next_issue_id(events: list[Event]) -> str:
    issue_ids = [
        int(event.issue_id)
        for event in events
        if event.type == "issue" and event.issue_id and event.issue_id.isdigit()
    ]
    return f"{(max(issue_ids) if issue_ids else 0) + 1:04d}"


def current_issue_id(events: list[Event]) -> str | None:
    closed = {event.issue_id for event in events if event.type == "fix" and event.issue_id}
    for event in reversed(events):
        if event.type == "issue" and event.issue_id not in closed:
            return event.issue_id
    return None


def write_current_issue(issue_id: str, mem: Path) -> None:
    # Advisory marker; never fail the command over it.
    with contextlib.suppress(OSError):
        current_issue_marker_path(mem).write_text(issue_id, encoding="utf-8")


def read_current_issue(mem: Path) -> str | None:
    path = current_issue_marker_path(mem)
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def clear_current_issue(mem: Path) -> None:
    with contextlib.suppress(OSError):
        current_issue_marker_path(mem).unlink(missing_ok=True)


def latest_open_issue_within(events: list[Event], minutes: int = 5) -> str | None:
    """Most recent OPEN issue id iff it was opened within ``minutes``.

    The time fence keeps an orphan ``attempt`` from silently attaching to a
    stale issue when no explicit id and no marker exist.
    """
    closed = {event.issue_id for event in events if event.type == "fix" and event.issue_id}
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    for event in reversed(events):
        if event.type != "issue" or event.issue_id in closed:
            continue
        try:
            ts = datetime.fromisoformat(event.timestamp.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None
        if ts >= cutoff:
            return event.issue_id
        return None
    return None


def get_git_commit(root: Path | None = None) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root or Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    commit = result.stdout.strip()
    return commit or None
