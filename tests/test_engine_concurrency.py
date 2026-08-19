"""Cross-process concurrency proof: two writers hammering one memory must
produce a complete, valid, uniquely-numbered event log with no lock residue.

Each writer process runs the real CLI entry (`python -m pollux`)
against the same project root, exactly like two worktrees would.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import init_memory

from pollux.engine.storage import read_events

_WORKER = """
import sys

from pollux.cli import main

root = sys.argv[1]
worker = sys.argv[2]
for i in range(25):
    rc = main(["note", f"worker {worker} note {i}", "--root", root])
    assert rc == 0, rc
for i in range(2):
    rc = main(["log", f"worker {worker} issue {i}", "--root", root])
    assert rc == 0, rc
"""


class ConcurrencyTests(unittest.TestCase):
    def test_two_processes_concurrent_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "family"
            mem = init_memory(root)
            workers = [
                subprocess.Popen(
                    [sys.executable, "-c", _WORKER, str(root), name],
                    cwd=tmp,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                for name in ("A", "B")
            ]
            for worker in workers:
                stdout, stderr = worker.communicate(timeout=120)
                self.assertEqual(
                    worker.returncode, 0, f"worker failed: {stdout} {stderr}"
                )

            events = read_events(mem)  # strict read: any torn line fails here
            notes = [e for e in events if e.type == "note"]
            issues = [e for e in events if e.type == "issue"]
            self.assertEqual(len(events), 54)  # 50 notes + 4 issues
            self.assertEqual(len(notes), 50)
            self.assertEqual(len(issues), 4)

            issue_ids = {e.issue_id for e in issues}
            self.assertEqual(len(issue_ids), 4)  # no duplicate id allocation
            self.assertEqual(sorted(issue_ids), ["0001", "0002", "0003", "0004"])

            # Notes carry no issue attachment; only the four issue events do.
            self.assertTrue(all(e.issue_id is None for e in notes))

            # Derived files agree with the log, and no lock directory remains.
            self.assertFalse((mem / "write.lock").exists())
            issue_files = sorted(p.name for p in (mem / "issues").glob("*.md"))
            self.assertEqual(len(issue_files), 4)
            summary = (mem / "summary.md").read_text(encoding="utf-8")
            for issue_id in issue_ids:
                self.assertIn(f"#{issue_id} ", summary)


if __name__ == "__main__":
    unittest.main()
