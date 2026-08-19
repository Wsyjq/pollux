"""Git auto-capture: commits and merges become memory events.

Three defects of the historical capture path are fixed here:

1. Root resolution — the old hook checked ``$PWD/.projectmem`` and the old
   command checked ``Path.cwd()/.projectmem``; neither walked up, so the
   family topology (memory anchored at a parent of several repos) could
   never auto-capture. We resolve the repo root via git and walk up from
   there.
2. Chinese commit subjects — the old classifier only knew English
   conventional prefixes and silently dropped everything else. Chinese
   subject conventions (修复…/新增…/重构…/回滚…) are first-class now.
3. Dedup cost — the old path re-parsed the whole log per commit; the
   inverted index answers the same question from one parse.
"""
from __future__ import annotations

import re
from pathlib import Path

from agent_memory_guardrails.engine.commands import Memory
from agent_memory_guardrails.engine.gitmeta import _run_git, head_commit
from agent_memory_guardrails.engine.index import MemoryIndex
from agent_memory_guardrails.engine.models import Event
from agent_memory_guardrails.engine.storage import read_events_lenient

CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}
MIN_CONFIDENCE = "medium"

# Ordered rules: (name, subject regex, event type, outcome, confidence).
# English rules mirror the historical classifier's order and confidence
# levels; Chinese rules match common zh-CN subject conventions.
def _rule(
    name: str,
    pattern: str,
    event_type: str,
    outcome: str | None,
    confidence: str,
) -> tuple[str, re.Pattern[str], str, str | None, str]:
    return (name, re.compile(pattern, re.IGNORECASE), event_type, outcome, confidence)


_COMMIT_PATTERNS: list[tuple[str, re.Pattern[str], str, str | None, str]] = [
    _rule(
        "revert",
        # No \b after the CJK alternatives: every CJK char is \w, so a
        # word boundary never fires between 回滚 and the next character.
        r"^\s*(?:revert\b|回滚|还原此提交|撤销提交|回退)"
        r"|^\s*(?:还原|撤销)\S*提交",
        "attempt", "failed", "medium",
    ),
    _rule(
        "fix",
        r"^\s*(?:fix|hotfix|bugfix|patch)\b|^\s*(?:修复|修正|解决|热修|更正)",
        "fix", None, "medium",
    ),
    _rule(
        "breaking",
        r"^\s*(?:break\w*)\b|BREAKING CHANGE|^\s*(?:不兼容|破坏性变更|重大变更)",
        "decision", None, "high",
    ),
    _rule(
        "feature",
        r"^\s*(?:feat|feature|add)\b|^\s*(?:新增|添加|接入|实现|支持)",
        "note", None, "medium",
    ),
    _rule(
        "refactor",
        r"^\s*(?:refactor|cleanup|reorganize|restructure)\b"
        r"|^\s*(?:重构|整理|清理|优化|迁移|重写)",
        "decision", None, "medium",
    ),
    _rule(
        "docs",
        r"^\s*(?:docs?|readme|changelog)\b|^\s*(?:文档|注释更新|说明)",
        "note", None, "low",
    ),
    _rule(
        "test",
        r"^\s*(?:tests?|spec)\b|^\s*(?:测试|单测|补测试|回归测试)",
        "note", None, "low",
    ),
]


def classify_message(message: str) -> dict | None:
    """Classify a commit subject into an auto-capture recipe, or None."""
    subject = message.strip().split("\n")[0]
    for name, pattern, event_type, outcome, confidence in _COMMIT_PATTERNS:
        if pattern.search(subject):
            return {
                "prefix": name,
                "event_type": event_type,
                "outcome": outcome,
                "confidence": confidence,
                "capture_source": (
                    "git_post_revert" if name == "revert" else "git_post_commit"
                ),
            }
    return None


def _last_commit_info(repo_root: Path) -> tuple[str, str, list[str]] | None:
    """(short hash, message, changed files) for HEAD, or None off-repo."""
    out = _run_git(
        ["log", "-1", "--name-only", "--pretty=format:%x00%h%x00%B"],
        repo_root,
        timeout=10.0,
    )
    if not out:
        return None
    stripped = out.strip("\n")
    if not stripped.startswith("\x00"):
        return None
    _nul, rest = stripped.split("\x00", 1)
    parts = rest.split("\x00", 1)
    if len(parts) != 2:
        return None
    commit_hash, remainder = parts
    # Message runs to the first blank line; the remaining non-empty lines
    # are the changed file paths.
    message_lines: list[str] = []
    files: list[str] = []
    seen_blank = False
    for line in remainder.splitlines():
        if not seen_blank:
            if line.strip() == "":
                seen_blank = True
            else:
                message_lines.append(line)
        elif line.strip():
            files.append(line.strip())
    message = "\n".join(message_lines).strip()
    return commit_hash.strip(), message, files


def _already_captured(mem: Path, commit_hash: str) -> bool:
    events, _skipped = read_events_lenient(mem)
    index = MemoryIndex.build(events)
    return commit_hash in index.by_commit


def capture_commit(repo_root: Path) -> Event | None:
    """Capture HEAD as a memory event (walk-up root, CN+EN classifier,
    dedup by commit hash). Returns the event, or None when there is
    nothing to capture (no memory, off-repo, duplicate, unmatched subject,
    or below the confidence threshold)."""
    mem = _discover_memory(repo_root)
    if mem is None:
        return None
    info = _last_commit_info(repo_root)
    if info is None:
        return None
    commit_hash, message, files = info
    if not message:
        return None
    if commit_hash and _already_captured(mem, commit_hash):
        return None

    matched = classify_message(message)
    if not matched:
        return None
    if CONFIDENCE_RANK.get(matched["confidence"], 0) < CONFIDENCE_RANK[MIN_CONFIDENCE]:
        return None

    first_line = message.split("\n")[0][:120]
    summary = f"{matched['prefix']}: {first_line}"
    memory = Memory(mem)
    # Auto-captured events are annotations, not work items: never touch the
    # active-issue marker.
    event = Event(
        type=matched["event_type"],
        summary=summary,
        outcome=matched["outcome"],
        files=files[:10],
        git_commit=commit_hash,
        location=files[0] if files else None,
        auto_captured=True,
        capture_source=matched["capture_source"],
        capture_confidence=matched["confidence"],
        git_message=first_line,
        command="auto-capture",
    )
    with memory.write_lock():
        from agent_memory_guardrails.engine.storage import append_event

        append_event(event, mem)
        from agent_memory_guardrails.engine.summary import regenerate_summary

        regenerate_summary(mem)
    return event


def capture_merge(repo_root: Path) -> Event | None:
    """Capture HEAD as a merge note (high confidence)."""
    mem = _discover_memory(repo_root)
    if mem is None:
        return None
    commit_hash = head_commit(repo_root)
    out = _run_git(["log", "-1", "--pretty=format:%B"], repo_root, timeout=10.0)
    if not out:
        return None
    first_line = out.strip().split("\n")[0][:120]
    if commit_hash and _already_captured(mem, commit_hash):
        return None
    memory = Memory(mem)
    event = Event(
        type="note",
        summary=f"Merge: {first_line}",
        git_commit=commit_hash,
        auto_captured=True,
        capture_source="git_post_merge",
        capture_confidence="high",
        git_message=first_line,
        command="auto-capture",
    )
    with memory.write_lock():
        from agent_memory_guardrails.engine.storage import append_event

        append_event(event, mem)
        from agent_memory_guardrails.engine.summary import regenerate_summary

        regenerate_summary(mem)
    return event


def _discover_memory(repo_root: Path) -> Path | None:
    from agent_memory_guardrails.engine.storage import discover_mem_dir

    return discover_mem_dir(repo_root)


__all__ = [
    "capture_commit",
    "capture_merge",
    "classify_message",
    "MIN_CONFIDENCE",
]
