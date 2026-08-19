"""Token-budgeted context generation.

Scoring follows the historical optimizer (type base × outcome multiplier ×
recency decay × file relevance, failed attempts kept even beyond the cutoff
and boosted 1.5x) so context quality does not regress at cutover. Section
builders are simpler — warnings first, then decisions, then open issues,
then notes — each truncated to the budget.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_memory_guardrails.engine.gitmeta import status_files
from agent_memory_guardrails.engine.models import Event
from agent_memory_guardrails.engine.storage import read_events_lenient

CHARS_PER_TOKEN = 4

TYPE_BASE_SCORES = {
    "attempt": 8,
    "fix": 6,
    "decision": 5,
    "issue": 4,
    "note": 2,
    "hypothesis": 3,
}

OUTCOME_MULTIPLIERS = {
    "failed": 2.0,
    "worked": 0.8,
    "partial": 1.2,
    None: 1.0,
}


def _parse_ts(timestamp: str) -> datetime:
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.now(timezone.utc) - timedelta(days=15)


def _smart_truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[:limit]
    for sep in (". ", "! ", "? ", "。", "；"):
        idx = head.rfind(sep)
        if idx >= max(20, limit // 2):
            return head[: idx + 1] + " …"
    idx = head.rfind(" ")
    if idx >= max(20, limit // 3):
        return head[:idx].rstrip(",;:—-") + " …"
    return head.rstrip() + "…"


def score_event(
    event: Event,
    now: datetime,
    cutoff: datetime,
    focus: str | None,
    git_files: list[str],
) -> float:
    ts = _parse_ts(event.timestamp)
    if ts < cutoff and not (event.type == "attempt" and event.outcome == "failed"):
        return 0.0

    base = TYPE_BASE_SCORES.get(event.type, 1)
    outcome_mult = OUTCOME_MULTIPLIERS.get(event.outcome, 1.0)

    age_days = (now - ts).total_seconds() / 86400
    if age_days <= 1:
        recency = 1.0
    elif age_days <= 7:
        recency = 0.8
    elif age_days <= 30:
        recency = 0.5
    else:
        recency = 0.2

    event_files = set(event.files or [])
    if event.location and ":" in event.location:
        event_files.add(event.location.split(":")[0])

    file_relevance = 0.3
    if focus:
        for file_path in event_files:
            if file_path.startswith(focus) or focus in file_path:
                file_relevance = 1.0
                break
            if (
                "/" in file_path
                and "/" in focus
                and file_path.rsplit("/", 1)[0] == focus.rstrip("/")
            ):
                file_relevance = 0.7
                break
    if git_files:
        for file_path in event_files:
            if file_path in git_files:
                file_relevance = max(file_relevance, 0.9)
                break

    resolution_boost = 1.5 if (event.type == "attempt" and event.outcome == "failed") else 1.0
    return base * outcome_mult * recency * file_relevance * resolution_boost


def generate_context(
    mem: Path,
    token_budget: int = 2000,
    focus: str | None = None,
    recent_days: int = 30,
) -> dict:
    """Return ``{"markdown", "tokens_used", "events_included"}``."""
    events, _skipped = read_events_lenient(mem)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=recent_days)
    git_files = status_files(mem.parent)

    scored: list[tuple[float, Event]] = []
    for event in events:
        score = score_event(event, now, cutoff, focus, git_files)
        if score > 0:
            scored.append((score, event))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    char_budget = token_budget * CHARS_PER_TOKEN
    lines: list[str] = [
        f"## amguard context (budget: {token_budget} tokens, focus: {focus or 'all'})"
    ]
    chars_used = len(lines[0])
    included = 0

    def add_section(title: str, items: list[Event], per_item_budget: int) -> int:
        nonlocal chars_used, included
        if not items or chars_used >= char_budget:
            return 0
        added = [f"### {title}"]
        count = 0
        for event in items:
            text = _smart_truncate(event.summary, per_item_budget)
            line = f"- {text}"
            if chars_used + len(line) + 1 > char_budget:
                break
            added.append(line)
            chars_used += len(line) + 1
            count += 1
            included += 1
        if count:
            block = "\n".join(added) + "\n"
            lines.append(block)
            chars_used += len(f"### {title}\n")
        return count

    warnings = [
        event
        for _score, event in scored
        if event.type == "attempt" and event.outcome == "failed"
    ][:10]
    decisions = [event for _score, event in scored if event.type == "decision"][:10]
    issues = [event for _score, event in scored if event.type == "issue"][:8]
    notes = [event for _score, event in scored if event.type == "note"][:10]

    per_item = 240 if token_budget >= 4000 else 160 if token_budget >= 1000 else 90
    add_section("Failed attempts (do not repeat)", warnings, per_item)
    add_section("Decisions", decisions, per_item)
    add_section("Open-recent issues", issues, per_item)
    add_section("Notes", notes, per_item)

    return {
        "markdown": "\n".join(lines),
        "tokens_used": chars_used // CHARS_PER_TOKEN,
        "events_included": included,
    }
