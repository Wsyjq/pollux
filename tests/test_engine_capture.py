"""Auto-capture tests: bilingual classifier, parent-anchored capture,
dedup, hook installation, and a real `git commit` firing the hook."""
from __future__ import annotations

import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from support import init_memory

from agent_memory_guardrails.engine.capture import (
    capture_commit,
    capture_merge,
    classify_message,
)
from agent_memory_guardrails.engine.hooks import (
    HOOK_MARKER_START,
    install_hooks,
    uninstall_hooks,
)
from agent_memory_guardrails.engine.storage import read_events_lenient


class ClassifierTests(unittest.TestCase):
    def test_chinese_subjects_classify(self) -> None:
        cases = {
            "修复登录崩溃": ("fix", "fix"),
            "修正路径分隔符": ("fix", "fix"),
            "新增自选页导出功能": ("feature", "note"),
            "重构风控模块": ("refactor", "decision"),
            "回滚上一次提交": ("revert", "attempt"),
            "不兼容：更换认证协议": ("breaking", "decision"),
        }
        for subject, (name, event_type) in cases.items():
            matched = classify_message(subject)
            self.assertIsNotNone(matched, subject)
            self.assertEqual(matched["prefix"], name, subject)
            self.assertEqual(matched["event_type"], event_type, subject)

    def test_english_conventional_subjects_classify(self) -> None:
        cases = {
            "fix: correct login redirect": "fix",
            "feat: add watchlist export": "feature",
            "revert: unstable change": "revert",
            "refactor: split risk module": "refactor",
            "docs: update README": "docs",
        }
        for subject, name in cases.items():
            matched = classify_message(subject)
            self.assertIsNotNone(matched, subject)
            self.assertEqual(matched["prefix"], name, subject)

    def test_low_confidence_rules_and_unknown_subjects(self) -> None:
        self.assertEqual(classify_message("docs: update README")["confidence"], "low")
        self.assertIsNone(classify_message("随便改改一些东西"))
        self.assertIsNone(classify_message(""))


class _GitMemoryCase(unittest.TestCase):
    """Family layout: a repo nested under a directory that owns the memory."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.family = Path(self._tmp.name) / "family"
        self.mem = init_memory(self.family, purpose="Capture family.")
        self.repo = self.family / "worktrees" / "repo-a"
        (self.repo / "src").mkdir(parents=True)
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")
        self.main_branch = self._git(
            "symbolic-ref", "--short", "HEAD"
        ).strip()

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

    def _commit(self, message: str, name: str = "src/a.py") -> None:
        (self.repo / name).write_text(f"# {message}\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", message)


class CaptureTests(_GitMemoryCase):
    def test_chinese_commit_captures_into_parent_memory(self) -> None:
        self._commit("修复登录崩溃")
        event = capture_commit(self.repo)
        self.assertIsNotNone(event)
        self.assertEqual(event.type, "fix")
        self.assertTrue(event.auto_captured)
        self.assertEqual(event.capture_source, "git_post_commit")
        self.assertIn("修复登录崩溃", event.summary)
        self.assertIn("src/a.py", event.files)
        # The event landed in the PARENT memory, not the repo (family layout).
        events, _ = read_events_lenient(self.mem)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].id, event.id)

    def test_duplicate_commit_is_not_captured_twice(self) -> None:
        self._commit("修复重复捕获问题")
        first = capture_commit(self.repo)
        self.assertIsNotNone(first)
        self.assertIsNone(capture_commit(self.repo))
        events, _ = read_events_lenient(self.mem)
        self.assertEqual(len(events), 1)

    def test_unmatched_subject_records_nothing(self) -> None:
        self._commit("随便改改")
        self.assertIsNone(capture_commit(self.repo))
        events, _ = read_events_lenient(self.mem)
        self.assertEqual(events, [])

    def test_low_confidence_docs_skipped_like_upstream(self) -> None:
        self._commit("docs: tweak readme")
        self.assertIsNone(capture_commit(self.repo))
        events, _ = read_events_lenient(self.mem)
        self.assertEqual(events, [])

    def test_merge_captures_high_confidence_note(self) -> None:
        self._commit("新增功能分支基础", name="src/base.py")
        capture_commit(self.repo)
        self._git("checkout", "-q", "-b", "feature")
        self._commit("新增分支提交", name="src/feature.py")
        capture_commit(self.repo)
        self._git("checkout", "-q", self.main_branch)
        self._git("merge", "-q", "--no-ff", "-m", "Merge branch 'feature'", "feature")
        event = capture_merge(self.repo)
        self.assertIsNotNone(event)
        self.assertEqual(event.type, "note")
        self.assertEqual(event.capture_confidence, "high")
        self.assertIn("Merge branch 'feature'", event.summary)


class HookTests(_GitMemoryCase):
    def test_install_creates_managed_blocks_and_uninstall_removes(self) -> None:
        written = install_hooks(self.repo)
        self.assertEqual(written, ["pre-commit", "post-commit", "post-merge"])
        post = (self.repo / ".git" / "hooks" / "post-commit").read_text(
            encoding="utf-8"
        )
        self.assertIn(HOOK_MARKER_START, post)
        self.assertIn("capture commit", post)
        # Idempotent refresh keeps user sections.
        (self.repo / ".git" / "hooks" / "post-commit").write_text(
            "# user section\n" + post, encoding="utf-8", newline="\n"
        )
        install_hooks(self.repo)
        refreshed = (self.repo / ".git" / "hooks" / "post-commit").read_text(
            encoding="utf-8"
        )
        self.assertIn("# user section", refreshed)
        # Uninstall removes the managed block but keeps user content alive;
        # a hook with no user content is deleted entirely.
        touched = uninstall_hooks(self.repo)
        self.assertEqual(sorted(touched), ["post-commit", "post-merge", "pre-commit"])
        kept = (self.repo / ".git" / "hooks" / "post-commit").read_text(encoding="utf-8")
        self.assertIn("# user section", kept)
        self.assertNotIn(HOOK_MARKER_START, kept)
        self.assertFalse((self.repo / ".git" / "hooks" / "pre-commit").exists())
        self.assertFalse((self.repo / ".git" / "hooks" / "post-merge").exists())

    def test_real_commit_fires_hook_and_captures(self) -> None:
        install_hooks(self.repo)
        self._git("commit", "-q", "--allow-empty", "-m", "修复真实hook触发")
        # post-commit captures in the background; poll briefly.
        deadline = time.monotonic() + 15.0
        events: list = []
        while time.monotonic() < deadline:
            events, _ = read_events_lenient(self.mem)
            if events:
                break
            time.sleep(0.3)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, "fix")
        self.assertIn("修复真实hook触发", events[0].summary)


if __name__ == "__main__":
    unittest.main()
