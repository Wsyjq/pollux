"""Per-file engineering dossiers — generalized from an internal single-repo dossier
tool, whose three-source model is retained verbatim:

1. Static Markdown cards describe *current* responsibility (human-authored).
2. The memory engine supplies *recorded* rationale and failure history.
3. Git supplies the auditable change timeline and content hashes.

The three sources stay separate so a stale card cannot rewrite history and a
Git diff is never presented as proof of intent.

De-coupled from the original: the repository id defaults to the repository
directory name instead of a hardcoded project; the cards directory is
derived from the index location instead of a hardcoded path; the repo root
comes from git instead of ``__file__``; memory sections are read natively
from the engine instead of shelling out to ``pjm``; and a JSON-Schema
template is emitted per repository id instead of a hardcoded schema.
"""
from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

DEFAULT_HISTORY_LIMIT = 8
HASH_RE = re.compile(r"^[0-9a-f]{40}$")
CARD_HEADING_RE = re.compile(r"^## `([^`]+)`$")


class DossierError(RuntimeError):
    """Raised when a requested dossier cannot be assembled safely."""


def git_repo_root(start: Path) -> Path:
    """Repository root via git; error when not inside a repository."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DossierError(f"cannot resolve git repository root from {start}: {exc}") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise DossierError(f"{start} is not inside a git repository")
    return Path(result.stdout.strip()).resolve()


def _relative_to(path: Path, parent: Path) -> Path:
    try:
        return path.relative_to(parent)
    except ValueError as exc:
        raise DossierError(f"path must stay inside repository {parent}: {path}") from exc


def normalize_path(
    repo_root: Path, input_path: str, repository_id: str
) -> tuple[str, Path]:
    """Return the stable project-family path and the physical worktree path.

    Accepts a canonical path (``<repositoryId>/<relative>``), a repo-relative
    path, or an absolute path inside the repository — all normalize to the
    same logical identity so events stay keyed consistently across worktrees.
    """
    root = repo_root.resolve()
    raw = input_path.strip()
    if not raw:
        raise DossierError("file path cannot be empty")

    normalized = raw.replace("\\", "/")
    canonical_prefix = f"{repository_id}/"
    if normalized.startswith(canonical_prefix):
        relative_text = normalized[len(canonical_prefix):]
        source = (root / relative_text).resolve()
        relative = _relative_to(source, root)
    elif Path(raw).is_absolute():
        source = Path(raw).resolve()
        relative = _relative_to(source, root)
    else:
        source = (root / raw).resolve()
        relative = _relative_to(source, root)

    if not relative.parts or any(part in ("", ".", "..") for part in relative.parts):
        raise DossierError(f"invalid repository-relative path: {input_path}")

    canonical = f"{repository_id}/{relative.as_posix()}"
    return canonical, source


def extract_card_section(card_text: str, canonical_path: str) -> str | None:
    """Extract one exact ``## `<path>` `` section from a Markdown card.

    The card's inner fields stay unparsed on purpose — they are a convention
    between contributors, not a machine contract.
    """
    lines = card_text.splitlines()
    heading = f"## `{canonical_path}`"
    try:
        start = lines.index(heading)
    except ValueError:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return "\n".join(lines[start:end]).strip()


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        stdin=subprocess.DEVNULL,
    )


def load_index(index_path: Path) -> dict:
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DossierError(f"file-card index not found: {index_path}") from exc
    except json.JSONDecodeError as exc:
        raise DossierError(f"invalid file-card index JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DossierError("file-card index must be a JSON object")
    return data


def _source_relative(canonical: str, repository_id: str) -> str | None:
    prefix = f"{repository_id}/"
    if not canonical.startswith(prefix):
        return None
    relative = canonical[len(prefix):]
    if not relative or "\\" in relative or any(
        part in ("", ".", "..") for part in relative.split("/")
    ):
        return None
    return relative


def validate_index(repo_root: Path, index_path: Path) -> list[str]:
    """All index/card/source consistency errors, without mutating anything.

    The orphan/duplicate-heading scan covers every Markdown file under the
    index's directory — the original hardcoded ``docs/file-cards``; deriving
    it from the index location keeps the tool repository-agnostic.
    """
    root = repo_root.resolve()
    try:
        index = load_index(index_path)
    except DossierError as exc:
        return [str(exc)]

    errors: list[str] = []
    repository_id = index.get("repositoryId")
    cards = index.get("cards")
    if index.get("schemaVersion") != 1:
        errors.append("schemaVersion must be 1")
    if not isinstance(repository_id, str) or not repository_id:
        errors.append("repositoryId must be a non-empty string")
        return errors
    if not isinstance(cards, list):
        errors.append("cards must be an array")
        return errors

    seen: set[str] = set()
    indexed_headings: set[str] = set()
    for raw_entry in cards:
        if not isinstance(raw_entry, dict):
            errors.append("card entry must be an object")
            continue
        canonical = raw_entry.get("path")
        if not isinstance(canonical, str):
            errors.append("card entry path must be a string")
            continue
        if canonical in seen:
            errors.append(f"duplicate index path: {canonical}")
            continue
        seen.add(canonical)
        indexed_headings.add(canonical)

        relative = _source_relative(canonical, repository_id)
        if relative is None:
            errors.append(f"invalid canonical path: {canonical}")
            continue
        source = (root / relative).resolve()
        try:
            _relative_to(source, root)
        except DossierError:
            errors.append(f"source escapes repository: {canonical}")
            continue
        if not source.is_file():
            errors.append(f"source file missing: {canonical}")
            continue

        if raw_entry.get("tier") not in ("A", "B", "C"):
            errors.append(f"invalid tier for {canonical}: {raw_entry.get('tier')}")
        verified_commit = raw_entry.get("verifiedCommit")
        verified_blob = raw_entry.get("verifiedBlob")
        verified_working_blob = raw_entry.get("verifiedWorkingBlob")
        valid_commit = isinstance(verified_commit, str) and bool(
            HASH_RE.fullmatch(verified_commit)
        )
        valid_blob = isinstance(verified_blob, str) and bool(
            HASH_RE.fullmatch(verified_blob)
        )
        valid_working_blob = verified_working_blob is None or (
            isinstance(verified_working_blob, str)
            and bool(HASH_RE.fullmatch(verified_working_blob))
        )
        if not valid_commit:
            errors.append(f"invalid verifiedCommit for {canonical}")
        if not valid_blob:
            errors.append(f"invalid verifiedBlob for {canonical}")
        if not valid_working_blob:
            errors.append(f"invalid verifiedWorkingBlob for {canonical}")
        if valid_commit and valid_blob:
            commit_check = _run_git(root, "cat-file", "-e", f"{verified_commit}^{{commit}}")
            if commit_check.returncode != 0:
                errors.append(f"verifiedCommit does not exist for {canonical}")
            else:
                commit_blob = _run_git(root, "rev-parse", f"{verified_commit}:{relative}")
                if commit_blob.returncode != 0:
                    errors.append(
                        f"source missing at verifiedCommit for {canonical}: {verified_commit}"
                    )
                elif commit_blob.stdout.strip() != verified_blob:
                    errors.append(
                        f"verified commit/blob mismatch for {canonical}: "
                        f"commit={verified_commit} blob={commit_blob.stdout.strip()} "
                        f"index={verified_blob}"
                    )

        card_relative = raw_entry.get("card")
        if not isinstance(card_relative, str):
            errors.append(f"card path missing for {canonical}")
            continue
        card_path = (root / card_relative).resolve()
        try:
            _relative_to(card_path, root)
        except DossierError:
            errors.append(f"card escapes repository for {canonical}: {card_relative}")
            continue
        if not card_path.is_file():
            errors.append(f"card file missing for {canonical}: {card_relative}")
            continue
        section = extract_card_section(card_path.read_text(encoding="utf-8"), canonical)
        if section is None:
            errors.append(f"card heading missing for {canonical} in {card_relative}")

        actual = _run_git(root, "hash-object", "--", relative)
        expected_working_blob = (
            verified_working_blob
            if valid_working_blob and verified_working_blob
            else verified_blob
        )
        if actual.returncode != 0:
            errors.append(f"cannot hash {canonical}: {actual.stderr.strip()}")
        elif (
            isinstance(expected_working_blob, str)
            and actual.stdout.strip() != expected_working_blob
        ):
            errors.append(
                f"STALE blob for {canonical}: index={expected_working_blob} "
                f"actual={actual.stdout.strip()}"
            )

    cards_root = index_path.parent
    heading_locations: dict[str, list[str]] = {}
    if cards_root.is_dir():
        for card_path in cards_root.rglob("*.md"):
            if card_path == index_path:
                continue
            relative_card = card_path.relative_to(root).as_posix()
            for line_number, line in enumerate(
                card_path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                match = CARD_HEADING_RE.fullmatch(line)
                if not match:
                    continue
                heading = match.group(1)
                heading_locations.setdefault(heading, []).append(
                    f"{relative_card}:{line_number}"
                )
                if heading not in indexed_headings:
                    errors.append(
                        f"orphan card heading {heading} in {relative_card}:{line_number}"
                    )
    for heading, locations in heading_locations.items():
        if len(locations) > 1:
            errors.append(f"duplicate card heading {heading}: {', '.join(locations)}")
    return errors


def engine_memory_loader(repo_root: Path, canonical: str) -> dict[str, str]:
    """Native engine memory sections for one canonical path.

    Replaces the original's ``pjm`` subprocess calls: same three sections
    (precheck / events / context), read directly from the engine.
    """
    from agent_memory_guardrails.engine.context import generate_context
    from agent_memory_guardrails.engine.precheck import precheck_files
    from agent_memory_guardrails.engine.search import format_result, search_events
    from agent_memory_guardrails.engine.storage import discover_mem_dir

    mem = discover_mem_dir(repo_root)
    if mem is None:
        unavailable = f"No memory found above {repo_root.resolve()}"
        return {"precheck": unavailable, "events": unavailable, "context": unavailable}

    report = precheck_files(mem, [canonical], project_root=repo_root)
    results = search_events(mem, canonical)
    events_text = (
        "\n".join(format_result(event) for event in reversed(results))
        or "No matching memory events."
    )
    context = generate_context(mem, token_budget=1200, focus=canonical)["markdown"]
    return {"precheck": report.render_text(), "events": events_text, "context": context}


def _code_block(text: str) -> str:
    return f"```text\n{text}\n```"


def build_dossier(
    *,
    repo_root: Path,
    input_path: str,
    index_path: Path | None,
    repository_id: str,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
    memory_loader: Callable[[str], Mapping[str, str]] | None = None,
) -> str:
    """Build one Markdown dossier from cards, memory, and Git."""
    root = repo_root.resolve()
    canonical, source = normalize_path(root, input_path, repository_id)
    relative = _source_relative(canonical, repository_id)
    if relative is None:
        raise DossierError(f"invalid canonical path: {canonical}")

    entry = None
    if index_path is not None:
        index = load_index(index_path)
        entries = [e for e in index.get("cards", []) if e.get("path") == canonical]
        if len(entries) > 1:
            raise DossierError(f"duplicate file-card index entries for {canonical}")
        entry = entries[0] if entries else None

    card_text = "_UNPROFILED: no static file card exists for this path._"
    if entry is not None:
        card_path = (root / entry["card"]).resolve()
        if not card_path.is_file():
            card_text = f"_STALE: index points to missing card file {entry['card']}._"
        else:
            section = extract_card_section(
                card_path.read_text(encoding="utf-8"), canonical
            )
            card_text = section or f"_STALE: card heading missing from {entry['card']}._"

    branch = _run_git(root, "branch", "--show-current").stdout.strip() or "detached"
    head = _run_git(root, "rev-parse", "--short=12", "HEAD").stdout.strip() or "unknown"
    status_result = _run_git(
        root, "status", "--short", "--untracked-files=all", "--", relative
    )
    status_text = status_result.stdout.strip() or "clean"

    if source.is_file():
        actual_blob = _run_git(root, "hash-object", "--", relative).stdout.strip()
    else:
        actual_blob = "missing"
    head_blob_result = _run_git(root, "rev-parse", f"HEAD:{relative}")
    head_blob = (
        head_blob_result.stdout.strip()
        if head_blob_result.returncode == 0
        else "untracked at HEAD"
    )
    if entry is None:
        freshness = "UNPROFILED" if source.is_file() else "MISSING"
        verification_basis = "no static card"
    else:
        verified_working_blob = entry.get("verifiedWorkingBlob")
        expected_blob = verified_working_blob or entry.get("verifiedBlob")
        freshness = "CURRENT" if actual_blob == expected_blob else "STALE"
        verification_basis = (
            "verified uncommitted working blob"
            if verified_working_blob
            else "verified committed blob"
        )
    if actual_blob == head_blob:
        workspace_relation = "matches HEAD"
    elif head_blob == "untracked at HEAD":
        workspace_relation = "untracked at HEAD"
    else:
        workspace_relation = "DIFFERS FROM HEAD"

    log_result = _run_git(
        root,
        "log",
        "--follow",
        f"-n{history_limit}",
        "--date=short",
        "--pretty=format:- %h | %ad | %s",
        "--",
        relative,
    )
    git_history = log_result.stdout.strip() or "_No commits found for this file._"

    loader = memory_loader or (lambda path: engine_memory_loader(root, path))
    try:
        memory = loader(canonical)
    except Exception as exc:  # keep Git/card evidence available when memory fails
        memory = {
            "precheck": f"memory loader failed: {exc}",
            "events": "memory unavailable",
            "context": "memory unavailable",
        }

    precheck = str(memory.get("precheck", "No precheck result."))
    events = str(memory.get("events", "No matching memory events."))
    context = str(memory.get("context", "No focused memory context."))

    return "\n".join(
        [
            "# File dossier",
            "",
            f"- Canonical path: `{canonical}`",
            f"- Physical worktree: `{root}`",
            "",
            "## Static responsibility card",
            "",
            card_text,
            "",
            "## Freshness and working tree",
            "",
            f"- Branch / HEAD: `{branch}` / `{head}`",
            f"- Card freshness: **{freshness}**",
            f"- Card verification basis: **{verification_basis}**",
            f"- Working file vs HEAD: **{workspace_relation}**",
            f"- Git status: `{status_text}`",
            f"- Working blob: `{actual_blob}`",
            f"- HEAD blob: `{head_blob}`",
            "",
            "## Memory precheck",
            "",
            _code_block(precheck),
            "",
            "## Recorded reasons and events",
            "",
            _code_block(events),
            "",
            "## Focused memory context",
            "",
            context,
            "",
            "## Git timeline",
            "",
            git_history,
            "",
            "> Git messages and diffs prove what changed. Only recorded memory "
            "or explicit commit text proves why; otherwise mark the reason as "
            "unknown or inferred.",
        ]
    )


def schema_template(repository_id: str) -> str:
    """A JSON Schema for a file-card index bound to one repository id.

    The original hardcoded its repository id inside its schema; emitting the
    template per repository keeps any project's index self-describing.
    """
    template = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"{repository_id} file-card index",
        "type": "object",
        "required": ["schemaVersion", "repositoryId", "cards"],
        "properties": {
            "schemaVersion": {"const": 1},
            "repositoryId": {"const": repository_id},
            "cards": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["path", "card", "tier", "verifiedCommit", "verifiedBlob"],
                    "properties": {
                        "path": {
                            "type": "string",
                            "pattern": f"^{repository_id}/.+",
                        },
                        "card": {"type": "string"},
                        "tier": {"enum": ["A", "B", "C"]},
                        "verifiedCommit": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
                        "verifiedBlob": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
                        "verifiedWorkingBlob": {
                            "anyOf": [
                                {"type": "null"},
                                {"type": "string", "pattern": "^[0-9a-f]{40}$"},
                            ]
                        },
                    },
                },
            },
        },
    }
    return json.dumps(template, indent=2, ensure_ascii=False) + "\n"
