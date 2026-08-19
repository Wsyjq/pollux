from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Severity
    message: str
    hint: str | None = None
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["severity"] = self.severity.value
        return {key: value for key, value in data.items() if value is not None}


@dataclass
class DoctorReport:
    project_root: Path
    memory_root: Path | None
    profile: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(item.severity is Severity.ERROR for item in self.findings)

    @property
    def warning_count(self) -> int:
        return sum(item.severity is Severity.WARNING for item in self.findings)

    @property
    def error_count(self) -> int:
        return sum(item.severity is Severity.ERROR for item in self.findings)

    def add(
        self,
        code: str,
        severity: Severity,
        message: str,
        *,
        hint: str | None = None,
        path: Path | str | None = None,
    ) -> None:
        self.findings.append(
            Finding(
                code=code,
                severity=severity,
                message=message,
                hint=hint,
                path=str(path) if path is not None else None,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_root": str(self.project_root),
            "memory_root": str(self.memory_root) if self.memory_root else None,
            "profile": self.profile,
            "ok": self.ok,
            "errors": self.error_count,
            "warnings": self.warning_count,
            "findings": [finding.to_dict() for finding in self.findings],
        }

    def render_text(self) -> str:
        memory = str(self.memory_root) if self.memory_root else "not found"
        lines = [
            "Agent Memory Guardrails doctor",
            f"Project: {self.project_root}",
            f"Memory:  {memory}",
            f"Profile: {self.profile}",
            "",
        ]
        if not self.findings:
            lines.append("[OK] No findings.")
        for finding in self.findings:
            label = finding.severity.value.upper()
            location = f" ({finding.path})" if finding.path else ""
            lines.append(f"[{label}] {finding.code}: {finding.message}{location}")
            if finding.hint:
                lines.append(f"  Hint: {finding.hint}")
        lines.extend(
            [
                "",
                f"Result: {'PASS' if self.ok else 'FAIL'} "
                f"({self.error_count} errors, {self.warning_count} warnings)",
            ]
        )
        return "\n".join(lines)
