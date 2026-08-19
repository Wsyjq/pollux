"""Iteration batch tests: backup, summary digest, decision lifecycle,
ranked search."""
from __future__ import annotations

import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from support import init_memory, write_events

from agent_memory_guardrails.engine.archive import (
    archive_files,
    read_archived_events,
    run_archive,
    run_restore,
)
from agent_memory_guardrails.engine.backup import run_backup, verify_backup
from agent_memory_guardrails.engine.models import Event
from agent_memory_guardrails.engine.search import search_events
from agent_memory_guardrails.engine.storage import read_events_lenient
from agent_memory_guardrails.engine.summary import (
    build_summary,
    build_summary_digest,
    get_summary_view,
    regenerate_summary,
)


def _ts(days_ago: float = 0.0) -> str:
    moment = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class BackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        self.mem = init_memory(self.root, purpose="Backup source.")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_backup_roundtrip_is_complete_and_verified(self) -> None:
        write_events(self.mem, [
            Event(type="note", summary="备份测试数据", location="src/a.py:1",
                  timestamp=_ts(1)),
        ])
        regenerate_summary(self.mem)
        dest = Path(self._tmp.name) / "backups"
        report = run_backup(self.mem, dest)
        self.assertTrue(report.zip_path.is_file())
        self.assertEqual(report.events, 1)
        with zipfile.ZipFile(report.zip_path) as archive:
            names = archive.namelist()
            self.assertIn("events.jsonl", names)
            self.assertIn("summary.md", names)
            self.assertIn("PROJECT_MAP.md", names)
            self.assertIn("MANIFEST.json", names)
        manifest = verify_backup(report.zip_path)
        self.assertEqual(manifest["events"], 1)

    def test_backup_skips_runtime_entries(self) -> None:
        (self.mem / "cache").mkdir(exist_ok=True)
        (self.mem / "cache" / "idx.json").write_text("{}", encoding="utf-8")
        report = run_backup(self.mem, Path(self._tmp.name) / "backups")
        self.assertIn("cache/idx.json", report.skipped)
        with zipfile.ZipFile(report.zip_path) as archive:
            self.assertNotIn("cache/idx.json", archive.namelist())
        # The lock directory itself must not be inside the zip either.
        self.assertTrue(all("write.lock" not in name for name in archive.namelist()))


class DigestTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        self.mem = init_memory(self.root, purpose="Digest source.")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_small_summary_returns_full_file(self) -> None:
        write_events(self.mem, [
            Event(type="note", summary="small", timestamp=_ts()),
        ])
        regenerate_summary(self.mem)
        view = get_summary_view(self.mem)
        self.assertIn("# projectmem - proj", view)
        self.assertNotIn("digest", view)

    def test_large_summary_switches_to_digest(self) -> None:
        events = []
        for index in range(1, 151):
            issue_id = f"{index:04d}"
            events.append(Event(
                type="issue", summary=f"digest 测试 issue 编号 {index} " + "背景描述" * 12,
                issue_id=issue_id, timestamp=_ts(30),
            ))
            events.append(Event(
                type="fix", summary=f"fixed {issue_id}", issue_id=issue_id,
                timestamp=_ts(29),
            ))
        events.append(Event(
            type="issue", summary="仍然开放的中文问题", issue_id="0151", timestamp=_ts(1),
        ))
        write_events(self.mem, events)
        regenerate_summary(self.mem)
        self.assertGreater(
            len((self.mem / "summary.md").read_text(encoding="utf-8")),
            12_000,
            "corpus must exceed the digest threshold for this test to mean anything",
        )
        view = get_summary_view(self.mem)
        self.assertIn("summary digest", view)
        self.assertIn("仍然开放的中文问题", view)  # open issues are always visible
        self.assertIn("`amguard show`", view)  # pointer to the full file
        self.assertLess(len(view), 12_000)

    def test_digest_bounds_every_section(self) -> None:
        events = [
            Event(type="issue", summary="open one", issue_id="0001", timestamp=_ts(1))
        ] + [
            Event(type="decision", summary=f"决策 {i}", timestamp=_ts(40))
            for i in range(20)
        ] + [
            Event(type="note", summary=f"笔记 {i}", timestamp=_ts(40))
            for i in range(15)
        ]
        digest = build_summary_digest(events, "proj", project_purpose="p")
        self.assertIn("open one", digest)
        self.assertIn("latest 12 of 20", digest)
        self.assertIn("latest 10", digest)


class DecisionLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        self.mem = init_memory(self.root, purpose="Decision lifecycle.")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_decisions_limit_caps_section_with_disclosure(self) -> None:
        events = [
            Event(type="decision", summary=f"决策 {i}", timestamp=_ts(50))
            for i in range(5)
        ]
        (self.mem / "config.toml").write_text(
            "decisions_limit = 2\n", encoding="utf-8"
        )
        text = build_summary(events, "proj", project_purpose="p", decisions_limit=2)
        self.assertIn("决策 4", text)  # latest kept
        self.assertNotIn("决策 1\n", text.split("## Notes")[0])  # oldest capped
        self.assertIn("3 older decision(s) not listed", text)

    def test_archive_decisions_before_moves_only_old_decisions(self) -> None:
        write_events(self.mem, [
            Event(type="decision", summary="老决策要退役", timestamp=_ts(90)),
            Event(type="decision", summary="新决策保留", timestamp=_ts(1)),
            Event(type="note", summary="笔记永不归档", timestamp=_ts(90)),
            Event(type="issue", summary="开放问题保留", issue_id="0001", timestamp=_ts(90)),
        ])
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=30)
        plan = run_archive(self.mem, decisions_before=cutoff)
        self.assertEqual(plan.archived_decisions, 1)
        active, _ = read_events_lenient(self.mem)
        self.assertEqual(
            sorted(e.summary for e in active),
            ["开放问题保留", "新决策保留", "笔记永不归档"],
        )
        archived = read_archived_events(self.mem)
        self.assertEqual([e.summary for e in archived], ["老决策要退役"])
        # Summary reflects the retirement.
        summary = (self.mem / "summary.md").read_text(encoding="utf-8")
        self.assertIn("新决策保留", summary)
        self.assertNotIn("老决策要退役", summary)
        # Restore brings it back byte-set complete.
        run_restore(self.mem)
        active, _ = read_events_lenient(self.mem)
        self.assertEqual(len(active), 4)

    def test_decision_only_archive_without_before(self) -> None:
        write_events(self.mem, [
            Event(type="issue", summary="古老已关闭问题", issue_id="0001",
                  timestamp=_ts(120)),
            Event(type="fix", summary="修了", issue_id="0001", timestamp=_ts(119)),
            Event(type="decision", summary="老决策", timestamp=_ts(120)),
        ])
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=30)
        plan = run_archive(self.mem, decisions_before=cutoff)  # no --before
        self.assertEqual(plan.archived_issues, [])  # issue groups untouched
        self.assertEqual(plan.archived_decisions, 1)
        self.assertEqual(len(archive_files(self.mem)), 1)


class RankedSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.mem = init_memory(Path(self._tmp.name) / "proj")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_open_recent_outranks_old_note(self) -> None:
        write_events(self.mem, [
            Event(type="note", summary="老的 grid 布局笔记", timestamp=_ts(90)),
            Event(type="issue", summary="grid 布局坏了", issue_id="0001",
                  timestamp=_ts(2)),
            Event(type="attempt", summary="grid 第一次尝试失败", issue_id="0001",
                  outcome="failed", timestamp=_ts(2)),
        ])
        ranked = search_events(self.mem, "grid", ranked=True)
        # The two recent events on the open issue lead (a failed attempt on an
        # open issue may legitimately edge out the issue itself — both score
        # higher than anything resolved or old); the stale note sinks last.
        self.assertEqual(
            {ranked[0].type, ranked[1].type}, {"issue", "attempt"}
        )
        self.assertEqual(ranked[-1].type, "note")

    def test_path_query_prefers_location_hits(self) -> None:
        write_events(self.mem, [
            Event(type="note", summary="提到 src/auth.py 的一句话", timestamp=_ts(90)),
            Event(type="attempt", summary="改这里失败", issue_id="0001", outcome="failed",
                  location="src/auth.py:10", timestamp=_ts(1)),
        ])
        ranked = search_events(self.mem, "src/auth.py", ranked=True)
        self.assertEqual(ranked[0].location, "src/auth.py:10")

    def test_default_order_stays_log_order(self) -> None:
        write_events(self.mem, [
            Event(type="note", summary="老的 grid 笔记", timestamp=_ts(90)),
            Event(type="issue", summary="grid 坏了", issue_id="0001", timestamp=_ts(2)),
        ])
        results = search_events(self.mem, "grid")
        self.assertEqual([e.type for e in results], ["note", "issue"])


if __name__ == "__main__":
    unittest.main()
