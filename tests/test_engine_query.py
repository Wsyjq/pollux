"""Query-layer tests: inverted index, search, precheck, context."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from support import init_memory, write_events

from pollux.engine.context import generate_context
from pollux.engine.index import MemoryIndex
from pollux.engine.models import Event
from pollux.engine.precheck import precheck_files
from pollux.engine.search import search_events


def _ts(days_ago: float = 0.0) -> str:
    moment = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class IndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events = [
            Event(type="issue", summary="auth broken", issue_id="0001",
                  location="src/auth.py:10", timestamp=_ts(5)),
            Event(type="attempt", summary="tried containment", issue_id="0001",
                  outcome="failed", files=["src/auth.py"], timestamp=_ts(4)),
            Event(type="fix", summary="fixed auth", issue_id="0001",
                  location="src/auth.py:12", timestamp=_ts(3)),
            Event(type="note", summary="see src/util.rs for helpers", timestamp=_ts(2)),
            Event(type="issue", summary="still open", issue_id="0002",
                  location="src/open.py:1", timestamp=_ts(1)),
            Event(type="attempt", summary="worked on open", issue_id="0002",
                  outcome="worked", timestamp=_ts(1)),
        ]
        self.index = MemoryIndex.build(self.events)

    def test_events_for_file_matches_all_three_ways(self) -> None:
        by_files = self.index.events_for_file("src/auth.py")
        self.assertEqual(len(by_files), 3)  # location + files entry... location appears twice
        by_summary = self.index.events_for_file("src/util.rs")
        self.assertEqual([e.summary for e in by_summary], ["see src/util.rs for helpers"])

    def test_events_for_file_deduplicates_and_keeps_log_order(self) -> None:
        matched = self.index.events_for_file("src/auth.py")
        ids = [id(e) for e in matched]
        self.assertEqual(len(ids), len(set(ids)))
        order = [i for i, e in enumerate(self.events) if id(e) in ids]
        self.assertEqual(order, sorted(order))

    def test_open_issues_excludes_resolved(self) -> None:
        self.assertEqual([e.issue_id for e in self.index.open_issues()], ["0002"])

    def test_by_commit_maps_hash_to_event(self) -> None:
        events = self.events + [
            Event(type="note", summary="captured", git_commit="abc1234", timestamp=_ts())
        ]
        index = MemoryIndex.build(events)
        self.assertEqual(index.by_commit["abc1234"].summary, "captured")


class SearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.mem = init_memory(Path(self._tmp.name) / "proj")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self) -> None:
        write_events(self.mem, [
            Event(type="note", summary="Fixed the登录页 Login page",
                  location="web/Login.tsx:5", git_commit="deadbee", timestamp=_ts()),
            Event(type="attempt", summary="tried grid layout", issue_id="0001",
                  outcome="failed", timestamp=_ts()),
            Event(type="issue", summary="grid layout broken", issue_id="0001", timestamp=_ts()),
        ])

    def test_substring_is_case_insensitive_and_matches_fields(self) -> None:
        self._seed()
        self.assertEqual(len(search_events(self.mem, "LOGIN")), 1)      # summary
        self.assertEqual(len(search_events(self.mem, "login.tsx")), 1)  # location
        self.assertEqual(len(search_events(self.mem, "grid")), 2)       # summary ×2

    def test_git_commit_is_searchable(self) -> None:
        self._seed()
        results = search_events(self.mem, "deadbee")
        self.assertEqual([e.type for e in results], ["note"])

    def test_regex_mode_and_bad_regex_fallback(self) -> None:
        self._seed()
        self.assertEqual(len(search_events(self.mem, "grid|grid layout", regex=True)), 2)
        self.assertEqual(len(search_events(self.mem, "([bad", regex=True)), 0)

    def test_failed_only_filters(self) -> None:
        self._seed()
        results = search_events(self.mem, "grid", failed_only=True)
        self.assertEqual([e.type for e in results], ["attempt"])


class _GitCase(unittest.TestCase):
    """Test case with a throwaway git repository (commits are real)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")
        self.mem = init_memory(self.repo)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        return result.stdout

    def _commit_file(self, name: str, content: str) -> None:
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self._git("add", name)
        self._git("commit", "-q", "-m", f"update {name}")


class PrecheckTests(_GitCase):
    def test_open_issue_and_failed_attempts_surface(self) -> None:
        write_events(self.mem, [
            Event(type="issue", summary="bug A", issue_id="0001",
                  location="src/a.py:1", timestamp=_ts(2)),
            Event(type="attempt", summary="try 1", issue_id="0001",
                  outcome="failed", files=["src/a.py"], timestamp=_ts(2)),
            Event(type="issue", summary="resolved bug", issue_id="0002",
                  location="src/b.py:1", timestamp=_ts(2)),
            Event(type="fix", summary="fixed", issue_id="0002", timestamp=_ts(1)),
        ])
        self._commit_file("src/a.py", "print(1)\n")
        report = precheck_files(self.mem, ["src/a.py", "src/b.py"], project_root=self.repo)
        a = next(r for r in report.files if r.path == "src/a.py")
        self.assertEqual(a.severity, "warn")
        self.assertEqual([i.issue_id for i in a.open_issues], ["0001"])
        self.assertEqual(len(a.failed_attempts), 1)
        b = next(r for r in report.files if r.path == "src/b.py")
        self.assertEqual(b.severity, "info")  # resolved issue does not warn

    def test_three_failed_attempts_escalate_to_block(self) -> None:
        write_events(self.mem, [
            Event(type="issue", summary="stubborn bug", issue_id="0001",
                  location="src/c.py:1", timestamp=_ts(2)),
            *[
                Event(type="attempt", summary=f"try {i}", issue_id="0001",
                      outcome="failed", location="src/c.py:1", timestamp=_ts(2))
                for i in range(3)
            ],
        ])
        self._commit_file("src/c.py", "x = 1\n")
        report = precheck_files(self.mem, ["src/c.py"], project_root=self.repo)
        self.assertEqual(report.files[0].severity, "block")
        self.assertTrue(report.max_severity("block"))

    def test_stale_decision_flagged_after_threshold_commits(self) -> None:
        write_events(self.mem, [
            Event(type="decision", summary="chose plain files",
                  location="src/d.py:1", timestamp=_ts(3)),
        ])
        self._commit_file("src/d.py", "v1\n")
        self._commit_file("src/d.py", "v2\n")
        self._commit_file("src/d.py", "v3\n")
        self._commit_file("src/d.py", "v4\n")
        report = precheck_files(self.mem, ["src/d.py"], project_root=self.repo)
        stale = report.files[0].stale_events
        self.assertEqual(len(stale), 1)
        self.assertGreaterEqual(stale[0][2], 3)

    def test_missing_file_reported_as_stale(self) -> None:
        write_events(self.mem, [
            Event(type="decision", summary="about deleted file",
                  location="src/gone.py:1", timestamp=_ts(3)),
        ])
        self._commit_file("README.md", "x\n")
        report = precheck_files(self.mem, ["src/gone.py"], project_root=self.repo)
        self.assertEqual(report.files[0].stale_events[0][2], -1)

    def test_non_git_root_reports_unavailable_not_stale(self) -> None:
        write_events(self.mem, [
            Event(type="decision", summary="decided something",
                  location="src/x.py:1", timestamp=_ts(1)),
        ])
        self._commit_file("README.md", "x\n")
        nowhere = Path(self._tmp.name) / "not-a-repo"
        nowhere.mkdir()
        report = precheck_files(self.mem, ["src/x.py"], project_root=nowhere)
        self.assertFalse(report.git_available)
        self.assertEqual(report.files[0].stale_events, [])


class ContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.mem = init_memory(Path(self._tmp.name) / "proj")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_budget_is_respected_and_failed_attempts_first(self) -> None:
        write_events(self.mem, [
            Event(type="attempt", summary="failed try one", outcome="failed",
                  timestamp=_ts(1)),
            Event(type="attempt", summary="failed try two", outcome="failed",
                  timestamp=_ts(40)),  # beyond cutoff but still included
            Event(type="decision", summary="picked sqlite over files", timestamp=_ts(2)),
            Event(type="note", summary="minor filler note", timestamp=_ts(3)),
        ])
        result = generate_context(self.mem, token_budget=200)
        self.assertLessEqual(result["tokens_used"], 220)  # small overshoot from header
        self.assertIn("Failed attempts", result["markdown"])
        self.assertIn("failed try two", result["markdown"])
        self.assertGreaterEqual(result["events_included"], 2)

    def test_focus_promotes_matching_files(self) -> None:
        write_events(self.mem, [
            Event(type="note", summary="unrelated ui note",
                  location="src/ui/page.tsx:3", timestamp=_ts(1)),
            Event(type="note", summary="auth refactor note",
                  location="src/auth/handler.py:3", timestamp=_ts(1)),
        ])
        result = generate_context(self.mem, token_budget=1000, focus="src/auth/")
        markdown = result["markdown"]
        self.assertIn("auth refactor note", markdown)
        # Focus boosts relevance (1.0 vs 0.3); it does not exclude others —
        # with budget left over, lower-scored notes may follow.
        if "unrelated ui note" in markdown:
            self.assertLess(
                markdown.index("auth refactor note"),
                markdown.index("unrelated ui note"),
            )


if __name__ == "__main__":
    unittest.main()
