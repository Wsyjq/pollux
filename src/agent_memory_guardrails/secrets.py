from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SecretMatch:
    kind: str
    path: Path
    line: int


PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "openai-key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "generic-secret": re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\b"
        r"\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{16,}"
    ),
}


def scan_files(paths: Iterable[Path], *, max_bytes: int = 2_000_000) -> list[SecretMatch]:
    matches: list[SecretMatch] = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > max_bytes:
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, 1):
            for kind, pattern in PATTERNS.items():
                if pattern.search(line):
                    matches.append(SecretMatch(kind=kind, path=path, line=line_number))
    return matches


def memory_files(project_root: Path, memory_root: Path) -> list[Path]:
    mem = memory_root / ".projectmem"
    paths = [
        project_root / "AGENTS.md",
        project_root / "CLAUDE.md",
        mem / "events.jsonl",
        mem / "summary.md",
        mem / "PROJECT_MAP.md",
        mem / "plan.md",
    ]
    if memory_root != project_root:
        paths.extend((memory_root / "AGENTS.md", memory_root / "CLAUDE.md"))
    issues = mem / "issues"
    if issues.is_dir():
        paths.extend(sorted(issues.glob("*.md")))
    return paths
