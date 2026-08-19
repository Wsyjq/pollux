"""High-level Memory operation tests: attachment precedence, marker
lifecycle, supersedes validation, and derived-file side effects."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from support import init_memory

from pollux.engine.commands import Memory
from pollux.engine.errors import EngineError
from pollux.engine.storage import read_current_issue, read_events


class _MemoryCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        self.mem = init_memory(self.root)
        self.memory = Memory(self.mem)

    def tearDown(self) -> None:
        self._tmp.cleanup()


class WriteOperationTests(_MemoryCase):
    def test_log_assigns_first_id_and_sets_marker(self) -> None:
        event = self.memory.log_issue("First bug", location="src/a.py:1")
        self.assertEqual(event.issue_id, "0001")
        self.assertEqual(read_current_issue(self.mem), "0001")
        self.assertTrue((self.mem / "summary.md").exists())
        self.assertTrue((self.mem / "issues" / "0001-first-bug.md").exists())

    def test_attempt_attaches_to_active_marker(self) -> None:
        self.memory.log_issue("Bug")
        event = self.memory.record_attempt("try A", outcome="failed")
        self.assertEqual(event.issue_id, "0001")
        events = read_events(self.mem)
        self.assertEqual(
            [e.type for e in events], ["issue", "attempt"]
        )

    def test_attempt_requires_outcome(self) -> None:
        with self.assertRaises(EngineError):
            self.memory.record_attempt("try", outcome="maybe")

    def test_attempt_without_any_issue_fails_closed(self) -> None:
        with self.assertRaises(EngineError):
            self.memory.record_attempt("orphan attempt", outcome="failed")

    def test_attempt_auto_issue_creates_parent(self) -> None:
        event = self.memory.record_attempt(
            "orphan attempt", outcome="failed", auto_issue=True
        )
        self.assertEqual(event.issue_id, "0001")
        events = read_events(self.mem)
        self.assertEqual([e.type for e in events], ["issue", "attempt"])
        self.assertEqual(read_current_issue(self.mem), "0001")

    def test_attempt_rejects_unknown_explicit_issue(self) -> None:
        self.memory.log_issue("Bug")
        with self.assertRaises(EngineError):
            self.memory.record_attempt("try", outcome="failed", issue_id="0099")

    def test_fix_closes_issue_and_clears_marker(self) -> None:
        self.memory.log_issue("Bug")
        event = self.memory.record_fix("Fixed it", location="src/a.py:9")
        self.assertEqual(event.issue_id, "0001")
        self.assertIsNone(read_current_issue(self.mem))
        summary = (self.mem / "summary.md").read_text(encoding="utf-8")
        self.assertIn("- [DONE] #0001 Bug -> Fixed it [src/a.py:9] (fixed)", summary)

    def test_fix_without_issue_fails(self) -> None:
        with self.assertRaises(EngineError):
            self.memory.record_fix("nothing to close")

    def test_decision_supersedes_by_prefix(self) -> None:
        first = self.memory.add_decision("Old decision")
        second = self.memory.add_decision(
            "New decision", supersedes=first.id.removeprefix("evt_")[:6]
        )
        self.assertEqual(second.supersedes, first.id)
        summary = (self.mem / "summary.md").read_text(encoding="utf-8")
        self.assertIn("- New decision", summary)
        self.assertNotIn("- Old decision", summary)

    def test_decision_cannot_supersede_non_decision(self) -> None:
        note = self.memory.add_note("plain note")
        with self.assertRaises(EngineError):
            self.memory.add_decision("decision", supersedes=note.id)

    def test_note_appends_and_renders(self) -> None:
        event = self.memory.add_note("something happened", location="src/b.py:2")
        events = read_events(self.mem)
        self.assertEqual(events[0].id, event.id)
        summary = (self.mem / "summary.md").read_text(encoding="utf-8")
        self.assertIn("- something happened [src/b.py:2]", summary)


class AttachPrecedenceTests(_MemoryCase):
    def test_explicit_issue_beats_marker(self) -> None:
        self.memory.log_issue("first")
        self.memory.log_issue("second")  # marker now 0002
        event = self.memory.record_attempt("try", outcome="failed", issue_id="0001")
        self.assertEqual(event.issue_id, "0001")

    def test_time_fenced_fallback_attaches_recent_issue(self) -> None:
        self.memory.log_issue("recent")
        clear_marker = self.mem / ".current_issue"
        clear_marker.unlink(missing_ok=True)
        event = self.memory.record_attempt("try", outcome="failed")
        self.assertEqual(event.issue_id, "0001")


class DiscoveryTests(_MemoryCase):
    def test_memory_discover_walks_up(self) -> None:
        child = self.root / "packages" / "sub"
        child.mkdir(parents=True)
        self.assertEqual(Memory.discover(child).mem_dir, self.mem)

    def test_memory_discover_fails_cleanly(self) -> None:
        orphan = Path(self._tmp.name) / "no-memory-here"
        orphan.mkdir()
        with self.assertRaises(EngineError):
            Memory.discover(orphan)


if __name__ == "__main__":
    unittest.main()
