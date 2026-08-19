"""Runtime helpers shared by the CLI and doctor.

These used to live in the upstream-companion adapter; with the engine
in-house they describe *this* runtime, not a subprocess dependency.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from pollux.engine.errors import EngineError
from pollux.engine.storage import discover_mem_dir


def resolve_python(value: str | None) -> Path:
    candidate = value or sys.executable
    explicit = Path(candidate).expanduser()
    if explicit.is_file():
        return explicit.resolve()
    found = shutil.which(candidate)
    if not found:
        raise EngineError(f"Python executable not found: {candidate}")
    return Path(found).resolve()


def discover_memory_root(start: Path) -> Path | None:
    found = discover_mem_dir(start)
    return found.parent if found else None


def validate_roots(profile: str, project_root: Path, memory_root: Path) -> None:
    project_root = project_root.resolve()
    memory_root = memory_root.resolve()
    if profile in ("team", "private") and memory_root != project_root:
        raise EngineError(f"{profile} profile requires memory root == project root.")
    if profile == "family":
        if memory_root == project_root:
            raise EngineError("family profile requires a parent memory root.")
        if memory_root not in project_root.parents:
            raise EngineError("family memory root must be an ancestor of the project root.")
