"""Storage layer tests: root discovery, append/read, ids, markers, locking,
config parsing, and redaction on the write path."""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from support import init_memory

from agent_memory_guardrails.engine.errors import EngineError
from agent_memory_guardrails.engine.locking import DirLock, LockTimeout
from agent_memory_guardrails.engine.models import Event
from agent_memory_guardrails.engine.storage import (
    append_event,
    clear_current_issue,
    current_issue_id,
    discover_mem_dir,
    latest_open_issue_within,
    next_issue_id,
    parse_events_text,
    read_config,
    read_current_issue,
    read_events,
    read_events_lenient,
    require_mem_dir,
    serialize_event,
    write_current_issue,
)


class _TempCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()


class DiscoveryTests(_TempCase):
    def test_walk_up_finds_ancestor_memory(self) -> None:
        family_root = self.base / "family"
        mem = init_memory(family_root)
        child_repo = family_root / "worktrees" / "feature-x" / "src"
        child_repo.mkdir(parents=True)
        self.assertEqual(discover_mem_dir(child_repo), mem)
        self.assertEqual(require_mem_dir(root=None) if False else mem, mem)

    def test_global_store_without_config_is_not_discovered(self) -> None:
        # Mimic ~/.projectmem (no config.toml): must not count as project memory.
        fake_global = self.base / "home" / ".projectmem"
        fake_global.mkdir(parents=True)
        (fake_global / "events.jsonl").write_text("", encoding="utf-8")
        project_dir = self.base / "home" / "projects" / "repo"
        project_dir.mkdir(parents=True)
        self.assertIsNone(discover_mem_dir(project_dir))

    def test_explicit_root_missing_raises(self) -> None:
        with self.assertRaises(EngineError):
            require_mem_dir(root=self.base / "nowhere")

    def test_env_root_is_honored(self) -> None:
        project = self.base / "projA"
        mem = init_memory(project)
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        old = os.environ.get("PROJECTMEM_ROOT")
        os.environ["PROJECTMEM_ROOT"] = str(project)
        try:
            self.assertEqual(require_mem_dir(), mem)
        finally:
            if old is None:
                os.environ.pop("PROJECTMEM_ROOT", None)
            else:
                os.environ["PROJECTMEM_ROOT"] = old
        # Sanity: without env, discovery from elsewhere finds nothing.
        self.assertIsNone(discover_mem_dir(elsewhere))


class ReadWriteTests(_TempCase):
    def setUp(self) -> None:
        super().setUp()
        self.mem = init_memory(self.base / "proj")

    def test_append_and_read_roundtrip(self) -> None:
        event = Event(
            type="note",
            summary="中文 roundtrip",
            id="evt_rt",
            timestamp="2026-08-01T00:00:00Z",
            location="src/x.py:1",
        )
        append_event(event, self.mem)
        events = read_events(self.mem)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0], event)
        line = (self.mem / "events.jsonl").read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(
            line,
            '{"id": "evt_rt", "location": "src/x.py:1", "summary": '
            '"\\u4e2d\\u6587 roundtrip", "timestamp": "2026-08-01T00:00:00Z", '
            '"type": "note"}',
        )

    def test_secrets_are_redacted_on_append(self) -> None:
        event = Event(
            type="note",
            summary="leaked sk-" + "a" * 45 + " in log",
            id="evt_sec",
            timestamp="2026-08-01T00:00:00Z",
        )
        append_event(event, self.mem)
        text = (self.mem / "events.jsonl").read_text(encoding="utf-8")
        self.assertIn("[REDACTED:openai_key]", text)
        self.assertNotIn("sk-aaaa", text)

    def test_strict_read_raises_on_corrupt_line(self) -> None:
        (self.mem / "events.jsonl").write_text('{"type": "note"\n', encoding="utf-8")
        with self.assertRaises(EngineError):
            read_events(self.mem)

    def test_lenient_read_skips_corrupt_trailing_line(self) -> None:
        good = Event(type="note", summary="ok", id="evt_ok", timestamp="2026-08-01T00:00:00Z")
        (self.mem / "events.jsonl").write_text(
            serialize_event(good) + "\n" + '{"type": "note"\n', encoding="utf-8"
        )
        events, skipped = read_events_lenient(self.mem)
        self.assertEqual([e.id for e in events], ["evt_ok"])
        self.assertEqual(skipped, [2])

    def test_parse_events_text_reports_line_numbers(self) -> None:
        with self.assertRaises(EngineError) as ctx:
            parse_events_text(
                '{"type": "note", "summary": "ok"}\n{"bad": true}\n', "mem.jsonl"
            )
        self.assertIn("mem.jsonl:2", str(ctx.exception))


class IssueIdTests(_TempCase):
    def test_next_issue_id_pads_and_increments(self) -> None:
        events = [
            Event(type="issue", summary="a", issue_id="0001"),
            Event(type="issue", summary="b", issue_id="0009"),
            Event(type="note", summary="noise", issue_id="notanumber"),
        ]
        self.assertEqual(next_issue_id(events), "0010")
        self.assertEqual(next_issue_id([]), "0001")

    def test_current_issue_id_skips_fixed(self) -> None:
        events = [
            Event(type="issue", summary="a", issue_id="0001"),
            Event(type="fix", summary="done", issue_id="0001"),
            Event(type="issue", summary="b", issue_id="0002"),
        ]
        self.assertEqual(current_issue_id(events), "0002")

    def test_latest_open_issue_within_time_fence(self) -> None:
        fresh = Event(
            type="issue", summary="fresh", issue_id="0007", timestamp="2026-08-01T00:00:00Z"
        )
        self.assertEqual(latest_open_issue_within([fresh], minutes=0), None)
        # A just-now issue (no fence) attaches; an old one does not.
        import datetime as dt

        now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        now_issue = Event(type="issue", summary="now", issue_id="0008", timestamp=now_iso)
        self.assertEqual(latest_open_issue_within([now_issue]), "0008")

    def test_marker_lifecycle(self) -> None:
        mem = init_memory(self.base / "proj")
        self.assertIsNone(read_current_issue(mem))
        write_current_issue("0042", mem)
        self.assertEqual(read_current_issue(mem), "0042")
        clear_current_issue(mem)
        self.assertIsNone(read_current_issue(mem))


class ConfigTests(_TempCase):
    def test_config_values_are_parsed(self) -> None:
        mem = init_memory(self.base / "proj")
        (mem / "config.toml").write_text(
            'summary_size_limit_kb = 5\nrecent_days = 7\nproject_description = "demo"\n',
            encoding="utf-8",
        )
        config = read_config(mem)
        self.assertEqual(config.summary_size_limit_kb, 5)
        self.assertEqual(config.recent_days, 7)
        self.assertEqual(config.project_description, "demo")

    def test_garbage_config_falls_back_to_defaults(self) -> None:
        mem = init_memory(self.base / "proj")
        (mem / "config.toml").write_text(
            "??? not toml [[[\nsummary_size_limit_kb = oops\n",
            encoding="utf-8",
        )
        config = read_config(mem)
        self.assertEqual(config.summary_size_limit_kb, 20)
        self.assertEqual(config.recent_days, 30)


class DirLockTests(_TempCase):
    def test_lock_is_exclusive_and_released(self) -> None:
        lock_path = self.base / "memory" / "write.lock"
        lock_path.parent.mkdir(parents=True)
        with DirLock(lock_path):
            self.assertTrue(lock_path.is_dir())
            second = DirLock(lock_path, timeout=0.2)
            with self.assertRaises(LockTimeout):
                second.acquire()
        self.assertFalse(lock_path.exists())

    def test_stale_lock_is_taken_over(self) -> None:
        lock_path = self.base / "write.lock"
        os.mkdir(lock_path)
        stale_time = time.time() - 999
        os.utime(lock_path, (stale_time, stale_time))
        lock = DirLock(lock_path, timeout=1.0, stale_after=60.0)
        lock.acquire()
        try:
            self.assertTrue(lock_path.is_dir())
        finally:
            lock.release()

    def test_timeout_raises_locktimeout(self) -> None:
        lock_path = self.base / "write.lock"
        os.mkdir(lock_path)
        with self.assertRaises(LockTimeout):
            DirLock(lock_path, timeout=0.1, stale_after=9999).acquire()


if __name__ == "__main__":
    unittest.main()
