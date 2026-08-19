"""Archive lifecycle tests: planning, dry-run, reversibility, disclosure."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from support import init_memory, write_events

from agent_memory_guardrails.engine.archive import (
    archive_files,
    archive_status,
    plan_archive,
    read_archived_events,
    run_archive,
    run_restore,
)
from agent_memory_guardrails.engine.errors import EngineError
from agent_memory_guardrails.engine.models import Event
from agent_memory_guardrails.engine.search import search_events
from agent_memory_guardrails.engine.storage import serialize_event


def _ts(days_ago: float) -> str:
    moment = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _corpus() -> list:
    return [
        # 0001: closed 60 days ago -> archivable
        Event(type="issue", summary="old closed bug", issue_id="0001", timestamp=_ts(70)),
        Event(type="attempt", summary="old try", issue_id="0001", outcome="failed",
              timestamp=_ts(65)),
        Event(type="fix", summary="old fix", issue_id="0001", timestamp=_ts(60)),
        # 0002: closed 5 days ago -> stays active
        Event(type="issue", summary="recent closed bug", issue_id="0002", timestamp=_ts(6)),
        Event(type="fix", summary="recent fix", issue_id="0002", timestamp=_ts(5)),
        # 0003: open, old -> stays active (closed_only default)
        Event(type="issue", summary="old open bug", issue_id="0003", timestamp=_ts(80)),
        Event(type="attempt", summary="stuck try", issue_id="0003", outcome="failed",
              timestamp=_ts(70)),
        # unattached long-lived memory -> never archived
        Event(type="decision", summary="long-lived decision", timestamp=_ts(100)),
        Event(type="note", summary="long-lived note", timestamp=_ts(100)),
    ]


class ArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        self.mem = init_memory(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_plan_archives_only_old_closed_issues(self) -> None:
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=30)
        plan = plan_archive(_corpus(), before=cutoff)
        self.assertEqual(plan.archived_issues, ["0001"])
        self.assertEqual(len(plan.archived), 3)  # issue + attempt + fix
        kept_types = [e.issue_id for e in plan.keep]
        self.assertNotIn("0001", kept_types)
        self.assertIn("0002", kept_types)
        self.assertIn("0003", kept_types)

    def test_include_open_would_take_stale_open_issues(self) -> None:
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=30)
        plan = plan_archive(_corpus(), before=cutoff, closed_only=False)
        self.assertEqual(plan.archived_issues, ["0001", "0003"])

    def test_dry_run_touches_nothing(self) -> None:
        write_events(self.mem, _corpus())
        before_text = (self.mem / "events.jsonl").read_text(encoding="utf-8")
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=30)
        plan = run_archive(self.mem, cutoff, dry_run=True)
        self.assertEqual(plan.archived_issues, ["0001"])
        self.assertEqual((self.mem / "events.jsonl").read_text(encoding="utf-8"), before_text)
        self.assertEqual(archive_files(self.mem), [])

    def test_archive_and_restore_roundtrip_is_lossless(self) -> None:
        write_events(self.mem, _corpus())
        original = sorted(
            line
            for line in (self.mem / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        )
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=30)
        plan = run_archive(self.mem, cutoff)
        self.assertEqual(plan.archived_count, 3)
        self.assertEqual(len(read_archived_events(self.mem)), 3)

        # Summary no longer lists the archived issue.
        summary = (self.mem / "summary.md").read_text(encoding="utf-8")
        self.assertNotIn("#0001 old closed bug", summary)
        self.assertIn("#0003 old open bug", summary)
        # Archived issue file is gone from active issues/.
        self.assertFalse(list((self.mem / "issues").glob("0001-*")))

        report = run_restore(self.mem)
        self.assertEqual(report.restored_events, 3)
        self.assertEqual(archive_files(self.mem), [])
        restored = sorted(
            line
            for line in (self.mem / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        )
        self.assertEqual(restored, original)  # byte-level multiset equality
        # Summary sees the issue again.
        summary = (self.mem / "summary.md").read_text(encoding="utf-8")
        self.assertIn("#0001 old closed bug", summary)

    def test_restore_without_archive_fails_cleanly(self) -> None:
        with self.assertRaises(EngineError):
            run_restore(self.mem)

    def test_search_include_archived(self) -> None:
        write_events(self.mem, _corpus())
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=30)
        run_archive(self.mem, cutoff)
        # Active window no longer sees the archived issue's own event.
        self.assertEqual(search_events(self.mem, "old closed bug"), [])
        found = search_events(self.mem, "old closed bug", include_archived=True)
        self.assertEqual([e.type for e in found], ["issue"])
        # A broader term reaches every archived event of the group.
        broad = search_events(self.mem, "old ", include_archived=True)
        self.assertEqual(
            sorted(e.issue_id for e in broad), ["0001", "0001", "0001", "0003"]
        )

    def test_status_counts(self) -> None:
        write_events(self.mem, _corpus())
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=30)
        run_archive(self.mem, cutoff)
        status = archive_status(self.mem)
        self.assertEqual(status.archived_events, 3)
        self.assertEqual(status.active_events, 6)
        self.assertEqual(status.closed_issues_total, 1)  # only 0002 remains closed active
        self.assertEqual(len(status.archive_files), 1)

    def test_serialization_roundtrip_preserves_lines(self) -> None:
        for event in _corpus():
            line = serialize_event(event)
            self.assertEqual(serialize_event(event), line)
            self.assertIn(event.type, line)


if __name__ == "__main__":
    unittest.main()
