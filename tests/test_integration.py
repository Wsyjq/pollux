"""End-to-end integration against the own engine (no upstream dependency).

These used to drive the real third-party ``projectmem`` CLI; they now drive
pollux's in-process bootstrap and the full write→regenerate→doctor cycle.
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from pollux.cli import main
from pollux.doctor import run_doctor
from pollux.engine.hooks import pollux_entry_path


def file_hashes(root: Path, paths: tuple[Path, ...]) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }


class IntegrationTests(unittest.TestCase):
    def run_init(self, *args: str) -> str:
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = main(["init", *args])
        self.assertEqual(code, 0, output.getvalue() + error.getvalue())
        return output.getvalue()

    def test_team_init_with_hooks_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(
                ["git", "init", "-q"],
                cwd=root,
                check=True,
                stdin=subprocess.DEVNULL,
                timeout=20,
            )

            first_output = self.run_init(
                str(root), "--profile", "team", "--enable-hooks"
            )
            pinned = pollux_entry_path()
            hooks = tuple(
                root / ".git" / "hooks" / name
                for name in ("pre-commit", "post-commit", "post-merge")
            )
            for hook in hooks:
                content = hook.read_text("utf-8")
                self.assertIn(f'POLLUX_BIN="${{POLLUX_BIN:-{pinned}}}"', content)
                if os.name != "nt":
                    self.assertTrue(os.access(hook, os.X_OK), f"Hook is not executable: {hook}")

            managed = (
                root / "AGENTS.md",
                root / "CLAUDE.md",
                root / ".gitignore",
                root / ".projectmem" / "config.toml",
                root / ".projectmem" / "PROJECT_MAP.md",
                *hooks,
            )
            first_hashes = file_hashes(root, managed)
            second_output = self.run_init(
                str(root), "--profile", "team", "--enable-hooks"
            )

            self.assertIn("Result: PASS (0 errors, 0 warnings)", first_output)
            self.assertIn("Result: PASS (0 errors, 0 warnings)", second_output)
            self.assertEqual(file_hashes(root, managed), first_hashes)

    def test_engine_write_cycle_after_init(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.run_init(str(root), "--profile", "team")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    main(["log", "集成测试 issue", "--at", "src/x.py:1", "--root", str(root)]),
                    0,
                )
                self.assertEqual(
                    main(["attempt", "第一次尝试失败", "--failed", "--root", str(root)]),
                    0,
                )
                self.assertEqual(
                    main(["fix", "修复完成", "--root", str(root)]), 0
                )
                self.assertEqual(main(["show", "--root", str(root)]), 0)
            summary = (root / ".projectmem" / "summary.md").read_text("utf-8")
            self.assertIn("- [DONE] #0001 集成测试 issue", summary)
            self.assertIn("  - Failed attempt: 第一次尝试失败", summary)
            report = run_doctor(root, python=Path(sys.executable))
            self.assertTrue(report.ok, report.render_text())

    def test_private_and_family_profiles_pass_real_doctor(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            private = workspace / "private"
            private.mkdir()
            self.run_init(str(private), "--profile", "private")
            self.assertIn(".projectmem/", (private / ".gitignore").read_text("utf-8").splitlines())
            private_report = run_doctor(
                private,
                python=Path(sys.executable),
                profile="private",
            )
            self.assertTrue(private_report.ok, private_report.render_text())

            family = workspace / "family"
            project = family / "repo"
            project.mkdir(parents=True)
            self.run_init(
                str(project),
                "--profile",
                "family",
                "--memory-root",
                str(family),
            )
            family_report = run_doctor(project, python=Path(sys.executable))
            self.assertTrue(family_report.ok, family_report.render_text())
            self.assertEqual(family_report.profile, "family")
            self.assertEqual(family_report.memory_root, family)


if __name__ == "__main__":
    unittest.main()
