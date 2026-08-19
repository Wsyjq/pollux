"""Whole-memory backups.

The raw event log is irreplaceable history that lives outside git in every
profile (team ignores it, private ignores the whole directory), so a memory
with months of context otherwise has zero backups. A backup is a zip of the
complete ``.projectmem`` tree taken under the write lock (a consistent
snapshot — no torn appends), with an embedded manifest for integrity checks
and a self-test right after writing.

Restore is deliberately manual (unzip over an empty memory root) — the
dangerous direction of the operation should never run by accident.
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pollux.engine.errors import EngineError
from pollux.engine.locking import DirLock
from pollux.engine.storage import EVENTS_FILE, read_events_lenient

_MANIFEST_NAME = "MANIFEST.json"
_SKIP_DIRS = {"write.lock", "cache"}


@dataclass
class BackupReport:
    zip_path: Path
    file_count: int = 0
    events: int = 0
    events_sha256: str = ""
    skipped: list[str] = field(default_factory=list)


def default_backup_dir(memory_root: Path) -> Path:
    return Path.home() / ".pollux" / "backups" / memory_root.name


def run_backup(mem: Path, dest_dir: Path | None = None) -> BackupReport:
    """Snapshot the whole memory into one verified zip, under the write lock."""
    if not mem.is_dir():
        raise EngineError(f"Memory directory not found: {mem}")
    target_dir = dest_dir or default_backup_dir(mem.parent)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    zip_path = target_dir / f"backup-{mem.parent.name}-{stamp}.zip"

    with DirLock(mem / "write.lock"):
        events, _skipped_lines = read_events_lenient(mem)
        report = BackupReport(zip_path=zip_path, events=len(events))
        events_bytes = (mem / EVENTS_FILE).read_bytes()
        report.events_sha256 = hashlib.sha256(events_bytes).hexdigest()

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(mem.rglob("*")):
                relative = path.relative_to(mem)
                if path.is_dir():
                    continue
                if relative.parts and relative.parts[0] in _SKIP_DIRS:
                    report.skipped.append(relative.as_posix())
                    continue
                archive.write(path, arcname=relative.as_posix())
                report.file_count += 1
            manifest = {
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "memoryRoot": str(mem.parent.resolve()),
                "fileCount": report.file_count,
                "events": report.events,
                "eventsSha256": report.events_sha256,
                "skippedRuntime": sorted(report.skipped),
            }
            archive.writestr(
                _MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True)
            )

    # Self-test: a backup that cannot be read back is not a backup.
    with zipfile.ZipFile(zip_path) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise EngineError(f"Backup failed integrity check at entry: {bad}")
        stored = json.loads(archive.read(_MANIFEST_NAME))
        stored_events = archive.read(EVENTS_FILE)
        if hashlib.sha256(stored_events).hexdigest() != stored["eventsSha256"]:
            raise EngineError("Backup events hash mismatch after write.")
    return report


def verify_backup(zip_path: Path) -> dict:
    """Read a backup zip, check CRCs and the manifest hash; return the manifest."""
    if not zip_path.is_file():
        raise EngineError(f"Backup not found: {zip_path}")
    with zipfile.ZipFile(zip_path) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise EngineError(f"Backup integrity check failed at entry: {bad}")
        manifest = json.loads(archive.read(_MANIFEST_NAME))
        actual = hashlib.sha256(archive.read(EVENTS_FILE)).hexdigest()
        if actual != manifest.get("eventsSha256"):
            raise EngineError("Stored events no longer match the manifest hash.")
        return manifest
