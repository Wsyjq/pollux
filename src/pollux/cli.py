from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from pollux.constants import (
    AGENT_BLOCK,
    AGENT_MARKER_END,
    AGENT_MARKER_START,
    CLAUDE_BLOCK,
    CLAUDE_MARKER_END,
    CLAUDE_MARKER_START,
    CLIENTS,
    LEGACY_AGENT_MARKER2_END,
    LEGACY_AGENT_MARKER2_START,
    LEGACY_AGENT_MARKER_END,
    LEGACY_AGENT_MARKER_START,
    PROFILES,
    RUNTIME_IGNORE_ENTRIES,
    VERSION,
)
from pollux.doctor import run_doctor
from pollux.engine.bootstrap import initialize_memory
from pollux.engine.commands import Memory
from pollux.engine.errors import EngineError
from pollux.engine.hooks import install_hooks
from pollux.engine.storage import (
    discover_mem_dir,
    summary_path,
)
from pollux.engine.summary import regenerate_summary
from pollux.files import (
    PolluxFileError,
    ensure_lines,
    read_text,
    set_marked_block,
)
from pollux.render import render_client_config
from pollux.runtime import (
    discover_memory_root,
    resolve_python,
    validate_roots,
)


def _configure_stream(stream: object) -> None:
    if os.environ.get("PYTHONIOENCODING"):
        return
    isatty = getattr(stream, "isatty", None)
    if callable(isatty) and isatty():
        return
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="backslashreplace")


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _memory_root(args: argparse.Namespace, project_root: Path) -> Path:
    if args.memory_root:
        return _path(args.memory_root)
    if args.profile == "family":
        raise EngineError("--memory-root is required for the family profile.")
    return project_root


def _write_agent_files(project_root: Path, memory_root: Path) -> None:
    targets = {project_root, memory_root}
    marker_generations = (
        (AGENT_MARKER_START, AGENT_MARKER_END),
        (LEGACY_AGENT_MARKER_START, LEGACY_AGENT_MARKER_END),
        (LEGACY_AGENT_MARKER2_START, LEGACY_AGENT_MARKER2_END),
    )
    for root in targets:
        agent_path = root / "AGENTS.md"
        agent_content = read_text(agent_path)
        present = [
            pair
            for pair in marker_generations
            if pair[0] in agent_content or pair[1] in agent_content
        ]
        if len(present) > 1:
            raise PolluxFileError(
                f"Agent rule markers from multiple generations exist in {agent_path}."
            )
        agent_start, agent_end = present[0] if present else marker_generations[0]
        set_marked_block(
            agent_path,
            agent_start,
            agent_end,
            AGENT_BLOCK,
            heading="# AGENTS.md",
        )
        set_marked_block(
            root / "CLAUDE.md",
            CLAUDE_MARKER_START,
            CLAUDE_MARKER_END,
            CLAUDE_BLOCK,
            heading="# CLAUDE.md",
        )


def _command_init(args: argparse.Namespace) -> int:
    project_root = _path(args.path)
    if not project_root.is_dir():
        raise EngineError(f"Project root does not exist: {project_root}")
    memory_root = _memory_root(args, project_root)
    if not memory_root.is_dir():
        raise EngineError(f"Memory root does not exist: {memory_root}")
    validate_roots(args.profile, project_root, memory_root)

    python = resolve_python(args.python)

    # Governance blocks first so any later structure scan sees a governed tree.
    _write_agent_files(project_root, memory_root)

    # In-process engine bootstrap (idempotent; never overwrites existing files).
    initialize_memory(memory_root)
    print(f"Initialized memory at {memory_root / '.projectmem'}")

    # Restore/refresh the stricter managed blocks (nothing upstream rewrites
    # them anymore, but a refresh keeps hand-edited drift out).
    _write_agent_files(project_root, memory_root)

    ignore_root = memory_root
    if args.profile == "private":
        ensure_lines(ignore_root / ".gitignore", (".projectmem/",))
    else:
        ensure_lines(ignore_root / ".gitignore", RUNTIME_IGNORE_ENTRIES)

    if args.enable_hooks:
        # Our hooks resolve the memory by walking up from the git root, so
        # the family topology is supported (unlike the historical engine).
        written = install_hooks(project_root)
        print(f"Installed managed hook blocks: {', '.join(written)}")

    if args.client:
        print("\nMCP client configuration:\n")
        print(render_client_config(args.client, python, memory_root), end="")

    report = run_doctor(
        project_root,
        python=python,
        profile=args.profile,
        memory_root=memory_root,
    )
    print("\n" + report.render_text())
    return 0 if report.ok else 1


def _command_doctor(args: argparse.Namespace) -> int:
    project_root = _path(args.path)
    python = resolve_python(args.python)
    memory_root = _path(args.memory_root) if args.memory_root else None
    report = run_doctor(
        project_root,
        python=python,
        profile=None if args.profile == "auto" else args.profile,
        memory_root=memory_root,
        scan_secrets_enabled=not args.no_secret_scan,
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.render_text())
    return 0 if report.ok else 1


def _command_render(args: argparse.Namespace) -> int:
    project_root = _path(args.path)
    python = resolve_python(args.python)
    memory_root = (
        _path(args.memory_root) if args.memory_root else discover_memory_root(project_root)
    )
    if memory_root is None:
        raise EngineError("No memory root found. Pass --memory-root or run pollux init.")
    print(render_client_config(args.client, python, memory_root), end="")
    return 0


def _memory_for(args: argparse.Namespace) -> Memory:
    start = _path(getattr(args, "root", ".") or ".")
    mem = discover_mem_dir(start)
    if mem is None:
        raise EngineError(
            f"No .projectmem directory found in {start} or any parent. Run pollux init first."
        )
    return Memory(mem)


def _command_log(args: argparse.Namespace) -> int:
    event = _memory_for(args).log_issue(args.text, location=args.at)
    print(f"Opened issue #{event.issue_id} ({event.id})")
    return 0


def _command_attempt(args: argparse.Namespace) -> int:
    outcomes = [name for name, flag in (
        ("worked", args.worked), ("failed", args.failed), ("partial", args.partial),
    ) if flag]
    if len(outcomes) != 1:
        raise EngineError("Exactly one of --worked/--failed/--partial is required.")
    event = _memory_for(args).record_attempt(
        args.text,
        outcome=outcomes[0],
        location=args.at,
        issue_id=args.issue,
        auto_issue=args.auto_issue,
    )
    print(f"Recorded {outcomes[0]} attempt ({event.id}) on issue #{event.issue_id}")
    return 0


def _command_fix(args: argparse.Namespace) -> int:
    event = _memory_for(args).record_fix(args.text, location=args.at, issue_id=args.issue)
    print(f"Recorded fix ({event.id}); issue #{event.issue_id} closed")
    return 0


def _command_decision(args: argparse.Namespace) -> int:
    event = _memory_for(args).add_decision(
        args.text, location=args.at, supersedes=args.supersedes
    )
    suffix = f", superseding {event.supersedes}" if event.supersedes else ""
    print(f"Recorded decision ({event.id}){suffix}")
    return 0


def _command_note(args: argparse.Namespace) -> int:
    event = _memory_for(args).add_note(args.text, location=args.at)
    print(f"Recorded note ({event.id})")
    return 0


def _command_show(args: argparse.Namespace) -> int:
    mem = _memory_for(args).mem_dir
    print(summary_path(mem).read_text(encoding="utf-8"), end="")
    return 0


def _command_regenerate(args: argparse.Namespace) -> int:
    mem = _memory_for(args).mem_dir
    stats = regenerate_summary(mem)
    print(
        f"Regenerated: summary {'rewritten' if stats.summary_written else 'unchanged'}, "
        f"{stats.issues_written} issue file(s) rewritten, "
        f"{stats.issues_removed} removed, {stats.issue_files_untouched} untouched"
    )
    if stats.removed_paths:
        for name in stats.removed_paths:
            print(f"  removed: issues/{name}")
    return 0


def _command_search(args: argparse.Namespace) -> int:
    from pollux.engine.search import format_result, search_events

    mem = _memory_for(args).mem_dir
    results = search_events(
        mem,
        args.query,
        regex=args.regex,
        failed_only=args.failed_only,
        include_archived=args.all,
        ranked=args.ranked,
    )
    if not results:
        print(f"No matches for '{args.query}'.")
        return 0
    ordered = results if args.ranked else list(reversed(results))
    for event in ordered:  # ranked: best first; log order: newest first
        print(format_result(event))
    print(f"\n{len(results)} match(es){' (ranked)' if args.ranked else ''}")
    if not args.all:
        from pollux.engine.archive import archive_files

        if archive_files(mem):
            print("note: archived events excluded — add --all to include them",
                  file=sys.stderr)
    return 0


def _command_archive(args: argparse.Namespace) -> int:
    from pollux.engine.archive import (
        archive_status,
        run_archive,
        run_restore,
    )

    mem = _memory_for(args).mem_dir
    if args.restore:
        report = run_restore(mem)
        print(
            f"Restored {report.restored_events} event(s) from "
            f"{len(report.files_consumed)} archive file(s); summary regenerated."
        )
        return 0
    if args.status:
        status = archive_status(mem)
        print(f"active events:      {status.active_events}")
        print(f"archived events:    {status.archived_events}")
        print(f"archive files:      {', '.join(status.archive_files) or 'none'}")
        print(f"closed issues:      {status.closed_issues_total}")
        for window, count in status.closed_issues_archivable_before.items():
            print(f"archivable (< {window} cutoff): {count}")
        return 0
    if not args.before and not args.decisions_before:
        raise EngineError(
            "Provide --before (issues) and/or --decisions-before, e.g. --before 2026-01-01."
        )
    from datetime import date as date_type

    def parse_cutoff(name: str, value: str | None) -> date_type | None:
        if not value:
            return None
        try:
            return date_type.fromisoformat(value)
        except ValueError as exc:
            raise EngineError(f"Invalid {name} date: {value}") from exc

    before = parse_cutoff("--before", args.before)
    decisions_before = parse_cutoff("--decisions-before", args.decisions_before)
    plan = run_archive(
        mem,
        before,
        closed_only=not args.include_open,
        dry_run=args.dry_run,
        decisions_before=decisions_before,
    )
    mode = "would archive" if args.dry_run else "archived"
    parts = []
    if plan.archived_issues:
        parts.append(f"{len(plan.archived_issues)} issue group(s)")
    if plan.archived_decisions:
        parts.append(f"{plan.archived_decisions} decision(s)")
    print(f"{mode} {', '.join(parts) or 'nothing'} — {plan.archived_count} event(s)")
    if args.dry_run:
        for issue_id in plan.archived_issues[:20]:
            print(f"  #{issue_id}")
        if len(plan.archived_issues) > 20:
            print(f"  ... and {len(plan.archived_issues) - 20} more")
    return 0


def _command_precheck(args: argparse.Namespace) -> int:
    from pollux.engine.gitmeta import staged_files
    from pollux.engine.precheck import precheck_files

    mem = _memory_for(args).mem_dir
    files = [path.replace("\\", "/") for path in args.files]
    if not files:
        files = staged_files(mem.parent)
        if not files:
            print("No files to check (nothing staged, none given).")
            return 0
    report = precheck_files(mem, files, project_root=_path(args.repo_root or "."))
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(report.render_text())
    return 1 if report.max_severity(args.level) else 0


def _command_context(args: argparse.Namespace) -> int:
    from pollux.engine.context import generate_context

    mem = _memory_for(args).mem_dir
    result = generate_context(
        mem, token_budget=args.tokens, focus=args.focus, recent_days=args.recent
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(result["markdown"])
        print(
            f"\n[-- {result['events_included']} events, "
            f"~{result['tokens_used']} tokens used]",
            file=sys.stderr,
        )
    return 0


def _command_capture(args: argparse.Namespace) -> int:
    from pollux.engine.capture import capture_commit, capture_merge

    repo_root = _path(args.repo_root)
    capture = capture_commit if args.trigger == "commit" else capture_merge
    event = capture(repo_root)
    if event is None:
        print("Nothing captured (no memory above this repo, duplicate commit, "
              "unmatched subject, or below confidence threshold).")
    else:
        print(f"Auto-captured {event.type}: {event.summary}")
    return 0


def _command_hooks(args: argparse.Namespace) -> int:
    from pollux.engine.hooks import install_hooks, uninstall_hooks

    repo_root = _path(args.repo)
    if args.action == "install":
        written = install_hooks(repo_root)
        print(f"Installed managed hook blocks: {', '.join(written)}")
    else:
        touched = uninstall_hooks(repo_root)
        print(f"Removed managed hook blocks: {', '.join(touched) or 'none'}")
    return 0


def _command_dossier(args: argparse.Namespace) -> int:
    from pollux.engine.dossier import (
        DossierError,
        build_dossier,
        git_repo_root,
        schema_template,
        validate_index,
    )

    repo_root = _path(args.repo_root) if args.repo_root else git_repo_root(Path.cwd())
    repository_id = args.repository_id or repo_root.name
    index_path = (
        _path(args.index)
        if args.index
        else repo_root / "docs" / "file-cards" / "index.json"
    )

    if args.emit_schema:
        print(schema_template(repository_id), end="")
        return 0
    if args.validate:
        errors = validate_index(repo_root, index_path)
        if errors:
            print("# File-card validation: FAILED\n")
            for error in errors:
                print(f"- {error}")
            return 1
        from pollux.engine.dossier import load_index

        index = load_index(index_path)
        print("# File-card validation: PASSED\n")
        print(f"- Indexed cards: {len(index.get('cards', []))}")
        print("- Canonical paths: unique")
        print("- Markdown headings: present and unique")
        print("- Source files: present")
        print("- Verified commit/blob pairs: consistent")
        print("- Working-tree blobs: current")
        return 0
    if not args.path:
        raise EngineError("path is required unless --validate or --emit-schema is used")
    if args.history_limit < 1 or args.history_limit > 50:
        raise EngineError("--history-limit must be between 1 and 50")
    try:
        print(
            build_dossier(
                repo_root=repo_root,
                input_path=args.path,
                index_path=index_path if index_path.exists() else None,
                repository_id=repository_id,
                history_limit=args.history_limit,
            )
        )
    except DossierError as exc:
        print(f"pollux: error: {exc}", file=sys.stderr)
        return 2
    return 0


def _command_mcp(_args: argparse.Namespace) -> int:
    from pollux.engine.mcp_server import main as mcp_main

    mcp_main()  # blocks, serving stdio JSON-RPC
    return 0


def _command_backup(args: argparse.Namespace) -> int:
    from pollux.engine.backup import default_backup_dir, run_backup, verify_backup

    if args.verify:
        manifest = verify_backup(_path(args.verify))
        print(json.dumps(manifest, indent=2, ensure_ascii=False))
        return 0
    mem = _memory_for(args).mem_dir
    dest = _path(args.to) if args.to else default_backup_dir(mem.parent)
    report = run_backup(mem, dest)
    print(
        f"Backup written: {report.zip_path}\n"
        f"  files: {report.file_count}, events: {report.events}, "
        f"events sha256: {report.events_sha256[:12]}…\n"
        f"  integrity: verified (zip CRC + manifest hash)"
    )
    if report.skipped:
        print(f"  skipped runtime entries: {', '.join(report.skipped)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pollux",
        description="Safe setup and diagnostics for local-first AI project memory.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Initialize memory with conservative defaults.")
    init.add_argument("path", nargs="?", default=".")
    init.add_argument("--profile", choices=PROFILES, default="team")
    init.add_argument("--memory-root")
    init.add_argument("--python", default=sys.executable)
    init.add_argument("--client", choices=CLIENTS)
    init.add_argument("--enable-hooks", action="store_true")
    init.set_defaults(handler=_command_init)

    doctor = subparsers.add_parser("doctor", help="Audit memory safety and configuration.")
    doctor.add_argument("path", nargs="?", default=".")
    doctor.add_argument("--profile", choices=("auto", *PROFILES), default="auto")
    doctor.add_argument("--memory-root")
    doctor.add_argument("--python", default=sys.executable)
    doctor.add_argument("--no-secret-scan", action="store_true")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(handler=_command_doctor)

    render = subparsers.add_parser("render", help="Render a client-specific MCP block.")
    render.add_argument("client", choices=CLIENTS)
    render.add_argument("path", nargs="?", default=".")
    render.add_argument("--memory-root")
    render.add_argument("--python", default=sys.executable)
    render.set_defaults(handler=_command_render)

    log = subparsers.add_parser("log", help="Open a new issue in the memory.")
    log.add_argument("text")
    log.add_argument("--at", help="Location (e.g. file:line).")
    log.add_argument("--root", default=".", help="Project dir to discover memory from.")
    log.set_defaults(handler=_command_log)

    attempt = subparsers.add_parser("attempt", help="Record a fix attempt.")
    attempt.add_argument("text")
    attempt.add_argument("--worked", action="store_true")
    attempt.add_argument("--failed", action="store_true")
    attempt.add_argument("--partial", action="store_true")
    attempt.add_argument("--at", help="Location (e.g. file:line).")
    attempt.add_argument("--issue", help="Issue id to attach to (e.g. 0042).")
    attempt.add_argument(
        "--auto-issue",
        action="store_true",
        help="Open a parent issue from this attempt when none is active.",
    )
    attempt.add_argument("--root", default=".", help="Project dir to discover memory from.")
    attempt.set_defaults(handler=_command_attempt)

    fix = subparsers.add_parser("fix", help="Record a fix and close the issue.")
    fix.add_argument("text")
    fix.add_argument("--at", help="Location (e.g. file:line).")
    fix.add_argument("--issue", help="Issue id to close (defaults to the active one).")
    fix.add_argument("--root", default=".", help="Project dir to discover memory from.")
    fix.set_defaults(handler=_command_fix)

    decision = subparsers.add_parser("decision", help="Record a decision.")
    decision.add_argument("text")
    decision.add_argument("--at", help="Location (e.g. file:line).")
    decision.add_argument(
        "--supersedes",
        help="Event id (or unique prefix) of a prior decision this one retires.",
    )
    decision.add_argument("--root", default=".", help="Project dir to discover memory from.")
    decision.set_defaults(handler=_command_decision)

    note = subparsers.add_parser("note", help="Record a free-form note.")
    note.add_argument("text")
    note.add_argument("--at", help="Location (e.g. file:line).")
    note.add_argument("--root", default=".", help="Project dir to discover memory from.")
    note.set_defaults(handler=_command_note)

    show = subparsers.add_parser("show", help="Print the current summary.md.")
    show.add_argument("--root", default=".", help="Project dir to discover memory from.")
    show.set_defaults(handler=_command_show)

    regenerate = subparsers.add_parser(
        "regenerate", help="Rebuild summary.md and issue files from the event log."
    )
    regenerate.add_argument("--root", default=".", help="Project dir to discover memory from.")
    regenerate.set_defaults(handler=_command_regenerate)

    search = subparsers.add_parser("search", help="Search the event log.")
    search.add_argument("query")
    search.add_argument("--regex", action="store_true", help="Treat query as a regex.")
    search.add_argument("--failed-only", action="store_true")
    search.add_argument(
        "--all", action="store_true", help="Include archived events in the search."
    )
    search.add_argument(
        "--ranked", action="store_true",
        help="Rank by relevance (open issues, failures, recency, path hits).",
    )
    search.add_argument("--root", default=".", help="Project dir to discover memory from.")
    search.set_defaults(handler=_command_search)

    archive = subparsers.add_parser(
        "archive", help="Manage event lifecycle (archive closed old issues)."
    )
    archive.add_argument("--before", help="Cutoff date for closed issues, e.g. 2026-01-01.")
    archive.add_argument(
        "--decisions-before",
        help="Additionally retire decision events older than this date (opt-in).",
    )
    archive.add_argument(
        "--include-open",
        action="store_true",
        help="Also archive open-but-stale issue groups (default: closed only).",
    )
    archive.add_argument("--dry-run", action="store_true")
    archive.add_argument("--status", action="store_true", help="Show archive statistics.")
    archive.add_argument(
        "--restore", action="store_true", help="Merge all archived events back."
    )
    archive.add_argument("--root", default=".", help="Project dir to discover memory from.")
    archive.set_defaults(handler=_command_archive)

    backup = subparsers.add_parser(
        "backup", help="Snapshot the whole memory into a verified zip."
    )
    backup.add_argument("--to", help="Destination directory (default: ~/.pollux/backups/<name>).")
    backup.add_argument(
        "--verify",
        help="Verify an existing backup zip and print its manifest.",
    )
    backup.add_argument("--root", default=".", help="Project dir to discover memory from.")
    backup.set_defaults(handler=_command_backup)

    capture = subparsers.add_parser(
        "capture", help="Auto-capture the latest git action (used by hooks)."
    )
    capture.add_argument("trigger", choices=("commit", "merge"))
    capture.add_argument("--repo-root", default=".", help="Git repo root to read.")
    capture.set_defaults(handler=_command_capture)

    hooks = subparsers.add_parser("hooks", help="Install or remove managed git hooks.")
    hooks.add_argument("action", choices=("install", "uninstall"))
    hooks.add_argument("--repo", default=".", help="Git repository path.")
    hooks.set_defaults(handler=_command_hooks)

    dossier = subparsers.add_parser(
        "dossier", help="Build or validate per-file engineering dossiers."
    )
    dossier.add_argument("path", nargs="?", help="repo-relative, canonical, or absolute")
    dossier.add_argument("--validate", action="store_true")
    dossier.add_argument("--emit-schema", action="store_true")
    dossier.add_argument("--repo-root", help="Git repository root (default: git toplevel).")
    dossier.add_argument("--index", help="Override index.json path.")
    dossier.add_argument("--repository-id", help="Logical path prefix (default: repo dir name).")
    dossier.add_argument("--history-limit", type=int, default=8)
    dossier.set_defaults(handler=_command_dossier)

    mcp_cmd = subparsers.add_parser(
        "mcp", help="Run the MCP server over stdio (15 tools)."
    )
    mcp_cmd.set_defaults(handler=_command_mcp)

    precheck = subparsers.add_parser(
        "precheck", help="Check files against memory before changing them."
    )
    precheck.add_argument("files", nargs="*", help="Files to check (default: staged).")
    precheck.add_argument(
        "--level", choices=("info", "warn", "block"), default="block",
        help="Minimum severity that exits non-zero (default: block).",
    )
    precheck.add_argument("--repo-root", help="Git repo root for staleness/churn.")
    precheck.add_argument("--json", action="store_true")
    precheck.add_argument("--root", default=".", help="Project dir to discover memory from.")
    precheck.set_defaults(handler=_command_precheck)

    context = subparsers.add_parser(
        "context", help="Token-budgeted memory context for prompts."
    )
    context.add_argument("--tokens", type=int, default=2000)
    context.add_argument("--focus", help="Focus area (e.g. src/auth/).")
    context.add_argument("--recent", type=int, default=30, help="Recent window in days.")
    context.add_argument("--json", action="store_true")
    context.add_argument("--root", default=".", help="Project dir to discover memory from.")
    context.set_defaults(handler=_command_context)
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stream(sys.stdout)
    _configure_stream(sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (PolluxFileError, EngineError, ValueError) as exc:
        print(f"pollux: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
