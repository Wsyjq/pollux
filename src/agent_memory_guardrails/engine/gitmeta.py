"""Batched git metadata: the fix for the precheck subprocess storm.

The historical implementation spawned one ``git log`` per (file, timestamp)
pair plus one churn query per staged file, each with a 5s timeout — on a
memory with hundreds of referenced files this alone exceeded two minutes on
Windows. Here, one ``git log --name-only`` pass over the needed time window
answers every staleness and churn question for the whole run.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass
class FileHistory:
    """Commits touching a file, newest first, parsed from one git-log pass."""

    commits: list[tuple[str, str]] = field(default_factory=list)  # (iso_date, hash)

    def last_commit_ts(self) -> str | None:
        return self.commits[0][0] if self.commits else None

    def commits_since(self, iso_ts: str) -> int:
        try:
            threshold = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return 0
        count = 0
        for iso_date, _hash in self.commits:
            try:
                commit_ts = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
            except ValueError:
                continue
            if commit_ts > threshold:
                count += 1
            else:
                break  # newest-first: nothing later can pass the threshold
        return count


def _run_git(args: list[str], cwd: Path, timeout: float = 10.0) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return result.stdout


def head_commit(root: Path) -> str | None:
    out = _run_git(["rev-parse", "--short", "HEAD"], root, timeout=5.0)
    return out.strip() if out else None


def status_files(root: Path) -> list[str]:
    """Paths that are staged, modified, or untracked right now."""
    out = _run_git(
        ["status", "--short", "--untracked-files=all"], root, timeout=10.0
    )
    if not out:
        return []
    files: list[str] = []
    for line in out.splitlines():
        if len(line) > 3:
            files.append(line[3:].strip().strip('"'))
    return files


def staged_files(root: Path) -> list[str]:
    out = _run_git(["diff", "--cached", "--name-only"], root, timeout=10.0)
    if not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def file_histories_since(root: Path, since_iso: str) -> dict[str, FileHistory] | None:
    """One git-log pass: file → commit history since ``since_iso``.

    Returns None when git is unavailable or the directory is not a repository
    — callers must treat that as "cannot judge", never as "stale".
    """
    out = _run_git(
        [
            "log",
            f"--since={since_iso}",
            "--name-only",
            "--pretty=format:%x00%cI %h",
        ],
        root,
        timeout=30.0,
    )
    if out is None:
        return None
    histories: dict[str, FileHistory] = {}
    current: tuple[str, str] | None = None
    for line in out.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("\x00"):
            parts = stripped[1:].split(" ", 1)
            if len(parts) == 2:
                current = (parts[0], parts[1])
            continue
        if current is None:
            continue
        histories.setdefault(stripped, FileHistory()).commits.append(current)
    return histories


def churn_since(
    histories: dict[str, FileHistory], file_path: str, days: int = 30
) -> int:
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    history = histories.get(file_path)
    if history is None:
        return 0
    return history.commits_since(cutoff)
