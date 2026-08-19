"""Cross-process advisory locking for memory writes.

The family topology points several concurrent agents (worktrees, MCP servers,
hooks) at one shared ``.projectmem/``. Without a lock, two writers can
allocate the same issue id or interleave partial lines. ``os.mkdir`` is atomic
on both Windows and POSIX, which makes a lock *directory* the simplest
primitive that works everywhere without optional dependencies.
"""
from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path

from agent_memory_guardrails.engine.errors import EngineError


class LockTimeout(EngineError):
    """Raised when a lock cannot be acquired within the timeout."""


class DirLock:
    """Mutual-exclusion lock built on an atomic directory creation.

    The lock directory holds a small info file (pid + acquired-at) used only
    for stale takeover: if a holder crashed without releasing, a waiter may
    remove a lock older than ``stale_after`` seconds and proceed.
    """

    def __init__(
        self,
        path: Path,
        timeout: float = 10.0,
        stale_after: float = 120.0,
        poll_interval: float = 0.02,
    ) -> None:
        self.path = path
        self.timeout = timeout
        self.stale_after = stale_after
        self.poll_interval = poll_interval
        self._acquired = False

    @property
    def info_path(self) -> Path:
        return self.path / "owner"

    def _try_acquire(self) -> bool:
        try:
            os.mkdir(self.path)
        except FileExistsError:
            return False
        # Owner info is advisory only; the directory itself is the lock.
        with contextlib.suppress(OSError):
            self.info_path.write_text(
                f"pid={os.getpid()} acquired={time.time():.3f}\n", encoding="utf-8"
            )
        return True

    def _is_stale(self) -> bool:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return False
        return (time.time() - mtime) > self.stale_after

    def _break_stale(self) -> None:
        try:
            self.info_path.unlink(missing_ok=True)
            self.path.rmdir()
        except OSError:
            pass  # someone else released or is mid-takeover; keep waiting

    def acquire(self) -> None:
        deadline = time.monotonic() + self.timeout
        while not self._try_acquire():
            if self._is_stale():
                self._break_stale()
                continue
            if time.monotonic() >= deadline:
                raise LockTimeout(
                    f"Could not acquire memory lock {self.path} within {self.timeout:.1f}s."
                )
            time.sleep(self.poll_interval)
        self._acquired = True

    def release(self) -> None:
        if not self._acquired:
            return
        self._acquired = False
        with contextlib.suppress(OSError):
            self.info_path.unlink(missing_ok=True)
        # A stale-breaker may have removed the directory already; that's fine.
        with contextlib.suppress(OSError):
            self.path.rmdir()

    def __enter__(self) -> DirLock:
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()
