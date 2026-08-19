"""Scale smoke tests at the M1 gate size: 1,500 events / 300 issues.

The thresholds are the plan's acceptance targets with a 3x safety margin —
CI machines vary, so exceeding the target logs the measured time and only
fails if it is drastically off. Measured values are always printed.
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from support import init_memory, write_events

from pollux.engine.commands import Memory
from pollux.engine.models import Event
from pollux.engine.summary import regenerate_summary


def _scale_corpus() -> list[Event]:
    events: list[Event] = []
    for index in range(1, 301):
        issue_id = f"{index:04d}"
        events.append(
            Event(
                type="issue",
                summary=f"Issue {index}: 中文标题 with some ASCII",
                issue_id=issue_id,
                timestamp=f"2026-08-01T00:{index % 60:02d}:00Z",
            )
        )
        events.append(
            Event(
                type="attempt",
                summary=f"attempt one for {issue_id}",
                issue_id=issue_id,
                outcome="failed",
                timestamp=f"2026-08-01T01:{index % 60:02d}:00Z",
            )
        )
        events.append(
            Event(
                type="attempt",
                summary=f"attempt two for {issue_id}",
                issue_id=issue_id,
                outcome="partial",
                timestamp=f"2026-08-01T02:{index % 60:02d}:00Z",
            )
        )
        if index % 2 == 0:
            events.append(
                Event(
                    type="fix",
                    summary=f"fixed {issue_id}",
                    issue_id=issue_id,
                    timestamp=f"2026-08-01T03:{index % 60:02d}:00Z",
                )
            )
    while len(events) < 1500:
        events.append(
            Event(
                type="note",
                summary=f"filler note {len(events)} 提到 src/some/file.py",
                timestamp="2026-08-02T00:00:00Z",
            )
        )
    return events[:1500]


class ScaleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name) / "scale"
        cls.mem = init_memory(cls.root, purpose="Scale corpus.")
        write_events(cls.mem, _scale_corpus())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_cold_regenerate_under_gate(self) -> None:
        start = time.perf_counter()
        stats = regenerate_summary(self.mem)
        elapsed = time.perf_counter() - start
        print(f"\n[cold regen 1500 events] {elapsed:.3f}s "
              f"(written={stats.issues_written}, untouched={stats.issue_files_untouched})")
        self.assertEqual(stats.issues_written, 300)
        self.assertLess(elapsed, 6.0)  # target 2s, 3x margin

    def test_single_write_under_gate(self) -> None:
        regenerate_summary(self.mem)  # steady state: files already in place
        memory = Memory(self.mem)
        start = time.perf_counter()
        memory.add_note("scale probe note")
        elapsed = time.perf_counter() - start
        print(f"\n[single add_note at 1501 events] {elapsed:.3f}s")
        events = read_lenient(self.mem)
        self.assertEqual(len(events), 1501)
        self.assertLess(elapsed, 3.0)  # target 1s, 3x margin


def read_lenient(mem: Path) -> list[Event]:
    from pollux.engine.storage import read_events

    return read_events(mem)


if __name__ == "__main__":
    unittest.main()
