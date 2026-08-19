"""Summary regeneration tests: golden output, CJK slugs, incremental sync."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from support import fixed_event, init_memory, write_events

from agent_memory_guardrails.engine.summary import (
    build_summary,
    regenerate_summary,
    slugify,
)


def _corpus() -> list:
    return [
        fixed_event("issue", "First issue", "evt_i1", issue_id="0001", location="src/a.py:1"),
        fixed_event("attempt", "Attempt one", "evt_a1", issue_id="0001", outcome="failed"),
        fixed_event(
            "attempt", "Attempt two", "evt_a2", issue_id="0001", outcome="failed",
            location="src/a.py:2",
        ),
        fixed_event("attempt", "Attempt three", "evt_a3", issue_id="0001", outcome="partial"),
        fixed_event("attempt", "Attempt four", "evt_a4", issue_id="0001", outcome="failed"),
        fixed_event(
            "fix", "Fixed with grid", "evt_f1", issue_id="0001", location="src/a.py:9"
        ),
        fixed_event("issue", "Second issue", "evt_i2", issue_id="0002"),
        fixed_event("decision", "Use sqlite", "evt_d1", location="docs/x.md:1"),
        fixed_event("decision", "Use files", "evt_d2"),
    ] + [
        fixed_event(
            "note",
            f"Note {index:02d}" + (" touched web/src/api/client.ts:33" if index == 12 else ""),
            f"evt_n{index:02d}",
        )
        for index in range(1, 13)
    ]


class SlugTests(unittest.TestCase):
    def test_ascii_slug_matches_historical_behavior(self) -> None:
        self.assertEqual(slugify("Fix login crash"), "fix-login-crash")
        self.assertEqual(slugify("  --Weird__spacing--  "), "weird-spacing")
        self.assertEqual(slugify("404 page not found"), "404-page-not-found")

    def test_cjk_characters_are_preserved(self) -> None:
        self.assertEqual(slugify("登录页在Windows下崩溃"), "登录页在windows下崩溃")
        # Fully non-alphanumeric input still falls back instead of vanishing.
        self.assertEqual(slugify("，。！"), "issue")

    def test_long_summaries_truncate_at_limit(self) -> None:
        self.assertLessEqual(len(slugify("x" * 200)), 48)


class BuildSummaryGoldenTests(unittest.TestCase):
    def test_golden_summary_matches_expected_text(self) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        expected = f"""# projectmem - golden

_Last updated: {today}_

## Project purpose
Golden corpus.

## Recent issues
- [OPEN] #0002 Second issue (open)
- [DONE] #0001 First issue [src/a.py:1] -> Fixed with grid [src/a.py:9] (fixed)
  - Failed attempt: Attempt two [src/a.py:2]
  - Partial attempt: Attempt three
  - Failed attempt: Attempt four

## Decisions
- Use sqlite [docs/x.md:1]
- Use files

## Notes
- Note 03
- Note 04
- Note 05
- Note 06
- Note 07
- Note 08
- Note 09
- Note 10
- Note 11
- Note 12 touched web/src/api/client.ts:33

## Key files
- `web/src/api/client.ts:33`

## Open questions
- None logged yet.
"""
        actual = build_summary(_corpus(), "golden", project_purpose="Golden corpus.")
        self.assertEqual(actual, expected)

    def test_superseded_decision_drops_out_of_summary(self) -> None:
        events = [
            fixed_event("decision", "Old choice", "evt_old"),
            fixed_event("decision", "New choice", "evt_new", supersedes="evt_old"),
        ]
        text = build_summary(events, "demo", project_purpose="p")
        self.assertIn("- New choice", text)
        self.assertNotIn("Old choice", text)


class RegenerateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        self.mem = init_memory(self.root, purpose="Regen corpus.")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_regenerate_writes_summary_and_issue_files(self) -> None:
        write_events(self.mem, _corpus())
        stats = regenerate_summary(self.mem)
        self.assertTrue(stats.summary_written)
        self.assertEqual(stats.issues_written, 2)
        summary = (self.mem / "summary.md").read_text(encoding="utf-8")
        self.assertIn("# projectmem - proj", summary)
        self.assertIn("- [DONE] #0001 First issue", summary)
        issue_text = (self.mem / "issues" / "0001-first-issue.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("# #0001 First issue", issue_text)
        self.assertIn("(failed)", issue_text)
        self.assertIn("(partial)", issue_text)

    def test_second_regenerate_is_zero_churn(self) -> None:
        write_events(self.mem, _corpus())
        regenerate_summary(self.mem)
        stats = regenerate_summary(self.mem)
        self.assertFalse(stats.summary_written)
        self.assertEqual(stats.issues_written, 0)
        self.assertEqual(stats.issues_removed, 0)
        self.assertEqual(stats.issue_files_untouched, 2)

    def test_orphan_issue_files_are_removed(self) -> None:
        write_events(self.mem, _corpus())
        orphan = self.mem / "issues" / "0099-stale-leftover.md"
        orphan.write_text("# stale\n", encoding="utf-8")
        stats = regenerate_summary(self.mem)
        self.assertEqual(stats.issues_removed, 1)
        self.assertFalse(orphan.exists())

    def test_cjk_issue_file_replaces_collapsed_name(self) -> None:
        events = [fixed_event("issue", "登录页在Windows下崩溃", "evt_cn", issue_id="0003")]
        write_events(self.mem, events)
        collapsed = self.mem / "issues" / "0003-windows.md"
        collapsed.write_text("# collapsed\n", encoding="utf-8")
        regenerate_summary(self.mem)
        cjk_file = self.mem / "issues" / "0003-登录页在windows下崩溃.md"
        self.assertTrue(cjk_file.exists())
        self.assertIn("# #0003 登录页在Windows下崩溃", cjk_file.read_text(encoding="utf-8"))
        self.assertFalse(collapsed.exists())

    def test_purpose_falls_back_to_existing_summary(self) -> None:
        write_events(self.mem, _corpus())
        (self.mem / "PROJECT_MAP.md").write_text(
            "# Project Map - proj\n\n## Project purpose\nNot described yet.\n",
            encoding="utf-8",
        )
        (self.mem / "summary.md").write_text(
            "# projectmem - proj\n\n## Project purpose\nLegacy purpose text.\n",
            encoding="utf-8",
        )
        regenerate_summary(self.mem)
        summary = (self.mem / "summary.md").read_text(encoding="utf-8")
        self.assertIn("Legacy purpose text.", summary)

    def test_placeholder_purpose_uses_default_template(self) -> None:
        write_events(self.mem, _corpus())
        (self.mem / "PROJECT_MAP.md").write_text(
            "# Project Map - proj\n\n## Project purpose\nNot described yet.\n",
            encoding="utf-8",
        )
        regenerate_summary(self.mem)
        summary = (self.mem / "summary.md").read_text(encoding="utf-8")
        self.assertIn("Replace this placeholder", summary)

    def test_recent_issues_limit_caps_and_discloses(self) -> None:
        write_events(self.mem, _corpus())
        (self.mem / "config.toml").write_text(
            "recent_issues_limit = 1\n", encoding="utf-8"
        )
        regenerate_summary(self.mem)
        summary = (self.mem / "summary.md").read_text(encoding="utf-8")
        self.assertIn("#0002 Second issue", summary)  # most recent kept
        self.assertNotIn("#0001 First issue", summary)  # older capped out
        self.assertIn("1 older issue(s) not listed", summary)


if __name__ == "__main__":
    unittest.main()
