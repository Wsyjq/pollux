from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Iterable
from pathlib import Path


class GuardrailsFileError(RuntimeError):
    pass


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _newline_for(content: str) -> str:
    return "\r\n" if "\r\n" in content else "\n"


def write_text_atomic(path: Path, content: str, *, newline: str | None = "") -> None:
    """Atomically replace ``path`` with ``content``.

    ``newline=""`` writes the content byte-for-byte (no translation) — the
    right default for managed files like Git hooks that must keep LF.
    ``newline=None`` applies the platform text-mode default, matching what a
    plain ``Path.write_text`` would produce on this OS.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline=newline,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
        if existing_mode is not None:
            os.chmod(path, existing_mode)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def set_marked_block(
    path: Path,
    start_marker: str,
    end_marker: str,
    block: str,
    *,
    heading: str,
) -> bool:
    content = read_text(path)
    if not content:
        content = heading.rstrip() + "\n\n"
    start_count = content.count(start_marker)
    end_count = content.count(end_marker)
    if start_count != end_count or start_count > 1:
        raise GuardrailsFileError(
            f"Expected zero or one complete marker pair in {path}; "
            f"found {start_count} start and {end_count} end markers."
        )

    normalized_block = block.rstrip()
    if start_count == 1:
        start = content.index(start_marker)
        raw_end = content.index(end_marker)
        if raw_end < start:
            raise GuardrailsFileError(f"Marker order is invalid in {path}.")
        end = raw_end + len(end_marker)
        updated = content[:start] + normalized_block + content[end:]
    else:
        prefix = content.rstrip()
        updated = f"{prefix}\n\n{normalized_block}\n" if prefix else f"{normalized_block}\n"

    if updated == content:
        return False
    write_text_atomic(path, updated)
    return True


def ensure_lines(path: Path, entries: Iterable[str]) -> bool:
    content = read_text(path)
    newline = _newline_for(content)
    existing = {line.strip() for line in content.splitlines()}
    missing = [entry for entry in entries if entry not in existing]
    if not missing:
        return False
    prefix = content.rstrip("\r\n")
    updated = prefix + (newline if prefix else "") + newline.join(missing) + newline
    write_text_atomic(path, updated)
    return True


def has_exact_line(path: Path, expected: str) -> bool:
    return any(line.strip() == expected for line in read_text(path).splitlines())


LEGACY_HOOK_MARKER_START = "# >>> projectmem auto-capture >>>"
LEGACY_HOOK_MARKER_END = "# <<< projectmem auto-capture <<<"


LEGACY_HOOK_MARKER_START = "# >>> projectmem auto-capture >>>"
LEGACY_HOOK_MARKER_END = "# <<< projectmem auto-capture <<<"


def managed_hook_block(content: str, start_marker: str, end_marker: str) -> str | None:
    """Extract one complete managed marker block, or None when absent.

    Zero-or-one pair is enforced: a hook with broken or duplicated markers
    is an error, not something to patch silently.
    """
    start_count = content.count(start_marker)
    end_count = content.count(end_marker)
    if start_count == 0 and end_count == 0:
        return None
    if start_count != 1 or end_count != 1:
        raise GuardrailsFileError(
            "Expected one complete managed hook marker pair; "
            f"found {start_count} start and {end_count} end markers."
        )
    start = content.index(start_marker)
    raw_end = content.index(end_marker)
    if raw_end < start:
        raise GuardrailsFileError("Managed hook marker order is invalid.")
    return content[start : raw_end + len(end_marker)]
