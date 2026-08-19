from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from pollux.constants import (
    AGENT_MARKER_END,
    AGENT_MARKER_START,
    EXPECTED_MEMORY_FILES,
    RUNTIME_IGNORE_ENTRIES,
    VERSION,
)
from pollux.engine.errors import EngineError
from pollux.engine.hooks import HOOK_MARKER_END, HOOK_MARKER_START
from pollux.files import (
    PolluxFileError,
    has_exact_line,
    managed_hook_block,
    read_text,
)
from pollux.models import DoctorReport, Severity
from pollux.runtime import (
    discover_memory_root,
    validate_roots,
)
from pollux.secrets import memory_files, scan_files

LEGACY_HOOK_MARKER_START = "# >>> projectmem auto-capture >>>"


def infer_profile(project_root: Path, memory_root: Path | None) -> str:
    if memory_root is not None and memory_root != project_root:
        return "family"
    if has_exact_line(project_root / ".gitignore", ".projectmem/"):
        return "private"
    return "team"


def _tracked(root: Path, relative: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", relative],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _check_agent_rules(report: DoctorReport) -> None:
    roots = [report.project_root]
    if report.memory_root is not None and report.memory_root != report.project_root:
        roots.append(report.memory_root)
    for root in roots:
        path = root / "AGENTS.md"
        content = read_text(path)
        if AGENT_MARKER_START not in content or AGENT_MARKER_END not in content:
            report.add(
                "agent-rules-missing",
                Severity.ERROR,
                "AGENTS.md does not contain the pollux workflow block.",
                hint="Run `pollux init` to install the workflow block.",
                path=path,
            )
            continue
        required = ("precheck_file", "PROJECT_MAP.md", "plan.md", "record_attempt")
        missing = [token for token in required if token not in content]
        if missing:
            report.add(
                "agent-rules-incomplete",
                Severity.ERROR,
                f"The pollux block is missing: {', '.join(missing)}.",
                hint="Re-run `pollux init` to refresh the marked block.",
                path=path,
            )


def _check_gitignore(report: DoctorReport) -> None:
    target_root = report.memory_root if report.profile == "family" else report.project_root
    if target_root is None:
        return
    path = target_root / ".gitignore"
    if report.profile == "private":
        if not has_exact_line(path, ".projectmem/"):
            report.add(
                "private-ignore-missing",
                Severity.ERROR,
                "Private profile does not ignore the full .projectmem directory.",
                hint="Run `pollux init --profile private`.",
                path=path,
            )
        return
    for entry in RUNTIME_IGNORE_ENTRIES:
        if not has_exact_line(path, entry):
            report.add(
                "runtime-ignore-missing",
                Severity.WARNING,
                f"Runtime memory file is not explicitly ignored: {entry}",
                hint="Run `pollux init` to add non-destructive ignore entries.",
                path=path,
            )


def _check_hooks(report: DoctorReport, python: Path) -> None:
    hooks = report.project_root / ".git" / "hooks"
    if not hooks.is_dir():
        return
    from pollux.engine.hooks import pollux_entry_path

    expected = Path(pollux_entry_path())
    installed = []
    for name in ("pre-commit", "post-commit", "post-merge"):
        path = hooks / name
        content = read_text(path)
        if LEGACY_HOOK_MARKER_START in content:
            report.add(
                "hook-legacy-projectmem",
                Severity.WARNING,
                f"{name} still contains a legacy projectmem hook block.",
                hint="Remove it with `pjm hooks uninstall`, then `pollux hooks install`.",
                path=path,
            )
        try:
            block = managed_hook_block(content, HOOK_MARKER_START, HOOK_MARKER_END)
        except PolluxFileError as exc:
            report.add(
                "hook-block-invalid",
                Severity.ERROR,
                f"{name} has invalid pollux markers: {exc}",
                hint="Repair the marker pair, then re-run `pollux hooks install`.",
                path=path,
            )
            continue
        if block is None:
            continue
        installed.append(name)
        match = re.search(r'POLLUX_BIN="\$\{POLLUX_BIN:-(.+)\}"', block)
        if match is None:
            report.add(
                "hook-runtime-unpinned",
                Severity.WARNING,
                f"{name} does not pin the pollux runtime in the managed block.",
                hint="Re-run `pollux hooks install` after repairing the hook block.",
                path=path,
            )
            continue
        pinned = match.group(1)
        if "\\" in pinned:
            report.add(
                "windows-hook-path",
                Severity.WARNING,
                f"{name} uses a backslash runtime path that Git Bash may reject.",
                hint="Re-run `pollux hooks install` to normalize it.",
                path=path,
            )
        actual_path = pinned.replace("\\", "/").rstrip("/")
        expected_path = expected.as_posix().rstrip("/")
        if os.name == "nt":
            actual_path = actual_path.casefold()
            expected_path = expected_path.casefold()
        if actual_path != expected_path:
            report.add(
                "hook-runtime-mismatch",
                Severity.WARNING,
                f"{name} pins a pollux runtime outside the selected environment.",
                hint="Re-run `pollux hooks install` to pin the selected runtime.",
                path=path,
            )
        elif not expected.is_file():
            report.add(
                "hook-runtime-unresolved",
                Severity.WARNING,
                "The pinned pollux CLI does not exist beside the selected runtime.",
                hint="Install pollux in that environment and re-run install.",
                path=hooks,
            )


def _check_opencode(report: DoctorReport) -> None:
    path = report.project_root / "opencode.json"
    if not path.exists() or report.memory_root is None:
        return
    try:
        data = json.loads(read_text(path))
        command = data["mcp"]["pollux"]["command"]
    except (json.JSONDecodeError, KeyError, TypeError):
        report.add(
            "opencode-config-invalid",
            Severity.ERROR,
            "opencode.json has no readable mcp.pollux command.",
            hint="Use `pollux render opencode` and merge only the pollux entry.",
            path=path,
        )
        return
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        report.add(
            "opencode-config-invalid",
            Severity.ERROR,
            "opencode.json mcp.pollux.command must be a list of strings.",
            hint="Use `pollux render opencode` and merge only the pollux entry.",
            path=path,
        )
        return
    try:
        root_index = command.index("--root")
        configured_root = Path(command[root_index + 1]).expanduser().resolve()
    except (ValueError, IndexError, OSError):
        configured_root = None
    if configured_root != report.memory_root:
        report.add(
            "opencode-root-mismatch",
            Severity.ERROR,
            "OpenCode pollux command does not target the discovered memory root.",
            hint="Regenerate the client block with `pollux render opencode`.",
            path=path,
        )


def run_doctor(
    project_root: Path,
    *,
    python: Path,
    profile: str | None = None,
    memory_root: Path | None = None,
    scan_secrets_enabled: bool = True,
) -> DoctorReport:
    project_root = project_root.expanduser().resolve()
    discovered = (
        memory_root.expanduser().resolve()
        if memory_root
        else discover_memory_root(project_root)
    )
    selected_profile = profile or infer_profile(project_root, discovered)
    report = DoctorReport(project_root, discovered, selected_profile)

    if not project_root.is_dir():
        report.add("project-root-missing", Severity.ERROR, "Project root does not exist.")
        return report
    if discovered is None:
        report.add(
            "memory-root-missing",
            Severity.ERROR,
            "No initialized .projectmem directory was found.",
            hint="Run `pollux init`.",
        )
        return report

    try:
        validate_roots(selected_profile, project_root, discovered)
    except EngineError as exc:
        report.add(
            "profile-root-mismatch",
            Severity.ERROR,
            str(exc),
            hint="Use matching --profile and --memory-root values.",
            path=discovered,
        )

    memory_dir = discovered / ".projectmem"
    for name in EXPECTED_MEMORY_FILES:
        path = memory_dir / name
        if not path.is_file():
            report.add(
                "memory-file-missing",
                Severity.ERROR,
                f"Required memory file is missing: {name}",
                hint="Re-run `pollux init` to complete the memory skeleton.",
                path=path,
            )

    report.add(
        "pollux-version",
        Severity.INFO,
        f"pollux {VERSION} (own engine, on-disk format compatible with the "
        f"historical projectmem layout).",
    )

    _check_agent_rules(report)
    _check_gitignore(report)
    _check_hooks(report, python)
    _check_opencode(report)

    if _tracked(discovered, ".projectmem/events.jsonl"):
        report.add(
            "raw-events-tracked",
            Severity.ERROR,
            "Raw events.jsonl is tracked by Git.",
            hint="Remove it from the index without deleting the local file.",
            path=memory_dir / "events.jsonl",
        )

    if scan_secrets_enabled:
        for match in scan_files(memory_files(project_root, discovered)):
            report.add(
                "possible-secret",
                Severity.ERROR,
                f"Possible {match.kind} detected at line {match.line}; value not displayed.",
                hint="Remove or rotate the credential before sharing memory files.",
                path=match.path,
            )

    return report
