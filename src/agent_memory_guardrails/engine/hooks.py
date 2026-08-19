"""Managed git-hook blocks for auto-capture and advisory precheck.

Differences from the historical hook installation, all deliberate:

- The snippet never checks ``$PWD/.projectmem`` — that check is exactly why
  parent-anchored (family) memories never captured anything. The CLI does
  its own walk-up from the git repo root.
- The amguard runtime is baked in as an absolute path next to the installing
  interpreter, so a PATH-level install cannot drift versions.
- Pre-commit is advisory (it prints, it does not block) — consistent with
  the governance layer's conservative defaults.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from agent_memory_guardrails.files import GuardrailsFileError, set_marked_block

HOOK_MARKER_START = "# >>> amguard auto-capture >>>"
HOOK_MARKER_END = "# <<< amguard auto-capture <<<"

_HOOK_NAMES = ("pre-commit", "post-commit", "post-merge")

_BASH_HEADER = "#!/usr/bin/env bash\n"

_PRE_COMMIT_BLOCK = f"""{HOOK_MARKER_START}
AMGUARD_BIN="${{AMGUARD_BIN:-__AMGUARD_ENTRY__}}"
if command -v "$AMGUARD_BIN" >/dev/null 2>&1; then
  "$AMGUARD_BIN" precheck --level block --root . || \\
    echo "amguard: precheck flagged risk for staged files (advisory)"
fi
{HOOK_MARKER_END}"""

_POST_COMMIT_BLOCK = f"""{HOOK_MARKER_START}
AMGUARD_BIN="${{AMGUARD_BIN:-__AMGUARD_ENTRY__}}"
if command -v "$AMGUARD_BIN" >/dev/null 2>&1; then
  "$AMGUARD_BIN" capture commit >/dev/null 2>&1 &
fi
{HOOK_MARKER_END}"""

_POST_MERGE_BLOCK = f"""{HOOK_MARKER_START}
AMGUARD_BIN="${{AMGUARD_BIN:-__AMGUARD_ENTRY__}}"
if command -v "$AMGUARD_BIN" >/dev/null 2>&1; then
  "$AMGUARD_BIN" capture merge >/dev/null 2>&1 &
fi
{HOOK_MARKER_END}"""


def amguard_entry_path() -> str:
    """Absolute POSIX-style path to the amguard CLI beside this interpreter.

    Baked into installed hooks so they cannot silently switch to a different
    install via PATH (the same reasoning as the PJM_BIN pinning upstream of
    this project's governance work).
    """
    scripts_dir = Path(sys.executable).parent
    for name in ("amguard.exe", "amguard"):
        candidate = scripts_dir / name
        if candidate.exists():
            return candidate.resolve().as_posix()
    return "amguard"  # PATH fallback; doctor flags this as drift-prone


def _hook_path(repo_root: Path, name: str) -> Path:
    return repo_root / ".git" / "hooks" / name


def install_hooks(repo_root: Path) -> list[str]:
    """Install or refresh the three managed hook blocks. Returns the hook
    names written. Idempotent: only the marked block is replaced."""
    git_hooks_dir = repo_root / ".git" / "hooks"
    git_hooks_dir.mkdir(parents=True, exist_ok=True)
    entry = amguard_entry_path()
    written: list[str] = []
    blocks = {
        "pre-commit": _PRE_COMMIT_BLOCK,
        "post-commit": _POST_COMMIT_BLOCK,
        "post-merge": _POST_MERGE_BLOCK,
    }
    for name, block in blocks.items():
        path = _hook_path(repo_root, name)
        if not path.exists():
            path.write_text(_BASH_HEADER, encoding="utf-8", newline="\n")
            if os.name != "nt":
                os.chmod(path, 0o755)
        content = path.read_text(encoding="utf-8")
        if _is_legacy_projectmem_block(content):
            raise GuardrailsFileError(
                f"{path} still contains a legacy projectmem hook block; "
                f"remove it (pjm hooks uninstall) before installing amguard's."
            )
        set_marked_block(
            path,
            HOOK_MARKER_START,
            HOOK_MARKER_END,
            block.replace("__AMGUARD_ENTRY__", entry),
            heading=_BASH_HEADER,
        )
        written.append(name)
    return written


def uninstall_hooks(repo_root: Path) -> list[str]:
    """Remove the managed block from each hook; delete the file when nothing
    else remains. Returns the hook names touched."""
    touched: list[str] = []
    for name in _HOOK_NAMES:
        path = _hook_path(repo_root, name)
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        if HOOK_MARKER_START not in content:
            continue
        body = _strip_managed_block(content)
        if body.strip() and body.strip() != _BASH_HEADER.strip():
            path.write_text(body, encoding="utf-8", newline="\n")
        else:
            path.unlink()
        touched.append(name)
    return touched


def _strip_managed_block(content: str) -> str:
    start = content.find(HOOK_MARKER_START)
    end = content.find(HOOK_MARKER_END)
    if start == -1 or end == -1:
        return content
    return content[:start] + content[end + len(HOOK_MARKER_END):]


def _is_legacy_projectmem_block(content: str) -> bool:
    return "# >>> projectmem auto-capture >>>" in content
