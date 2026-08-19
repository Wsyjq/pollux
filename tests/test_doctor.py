from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pollux.constants import (
    AGENT_BLOCK,
    EXPECTED_MEMORY_FILES,
    RUNTIME_IGNORE_ENTRIES,
)
from pollux.doctor import run_doctor
from pollux.engine.hooks import (
    HOOK_MARKER_END,
    HOOK_MARKER_START,
    pollux_entry_path,
)


def create_memory(root: Path) -> None:
    memory = root / ".projectmem"
    memory.mkdir(parents=True)
    for name in EXPECTED_MEMORY_FILES:
        (memory / name).write_text("\n", encoding="utf-8")
    (root / "AGENTS.md").write_text(AGENT_BLOCK, encoding="utf-8")
    (root / ".gitignore").write_text(
        "\n".join(RUNTIME_IGNORE_ENTRIES) + "\n", encoding="utf-8"
    )


class DoctorTests(unittest.TestCase):
    def test_explicit_profile_root_mismatch_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            project = workspace / "repo"
            project.mkdir()
            create_memory(workspace)
            (project / "AGENTS.md").write_text(AGENT_BLOCK, encoding="utf-8")

            report = run_doctor(
                project,
                python=Path(sys.executable),
                profile="team",
                memory_root=workspace,
            )

            self.assertIn("profile-root-mismatch", {item.code for item in report.findings})

    def test_family_audits_shared_root_secrets_and_git_tracking(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            subprocess.run(
                ["git", "init", "-q"],
                cwd=workspace,
                check=True,
                stdin=subprocess.DEVNULL,
                timeout=20,
            )
            project = workspace / "repo"
            project.mkdir()
            create_memory(workspace)
            (project / "AGENTS.md").write_text(AGENT_BLOCK, encoding="utf-8")
            # Split at runtime so the literal token shape never sits in the repo
            # (GitHub secret scanning and gitleaks flag plausible-looking fixtures).
            (workspace / "CLAUDE.md").write_text(
                "synthetic api_key = " + "a" * 26 + "\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "add", "-f", ".projectmem/events.jsonl"],
                cwd=workspace,
                check=True,
                stdin=subprocess.DEVNULL,
                timeout=20,
            )

            report = run_doctor(project, python=Path(sys.executable))
            codes = {item.code for item in report.findings}

            self.assertIn("possible-secret", codes)
            self.assertIn("raw-events-tracked", codes)

    def test_legacy_projectmem_hook_block_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_memory(root)
            hooks = root / ".git" / "hooks"
            hooks.mkdir(parents=True)
            (hooks / "post-commit").write_text(
                "# >>> projectmem auto-capture >>>\n"
                'PJM_BIN="D:/global/pjm.exe"\n'
                "# <<< projectmem auto-capture <<<\n",
                encoding="utf-8",
            )

            report = run_doctor(root, python=Path(sys.executable))

            self.assertIn("hook-legacy-projectmem", {item.code for item in report.findings})

    def test_hook_runtime_mismatch_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_memory(root)
            hooks = root / ".git" / "hooks"
            hooks.mkdir(parents=True)
            (hooks / "pre-commit").write_text(
                f"{HOOK_MARKER_START}\n"
                'POLLUX_BIN="${POLLUX_BIN:-D:/global/pollux.exe}"\n'
                f"{HOOK_MARKER_END}\n",
                encoding="utf-8",
            )

            report = run_doctor(root, python=Path(sys.executable))

            self.assertIn("hook-runtime-mismatch", {item.code for item in report.findings})

    def test_installed_hook_with_current_runtime_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_memory(root)
            hooks = root / ".git" / "hooks"
            hooks.mkdir(parents=True)
            pinned = pollux_entry_path()
            (hooks / "post-commit").write_text(
                f"{HOOK_MARKER_START}\n"
                f'POLLUX_BIN="${{POLLUX_BIN:-{pinned}}}"\n'
                f"{HOOK_MARKER_END}\n",
                encoding="utf-8",
            )

            report = run_doctor(root, python=Path(sys.executable))

            self.assertNotIn(
                "hook-runtime-mismatch", {item.code for item in report.findings}
            )
            self.assertTrue(report.ok, report.render_text())

    def test_complete_team_profile_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_memory(root)
            report = run_doctor(root, python=Path(sys.executable))
            self.assertTrue(report.ok, report.render_text())
            self.assertEqual(report.profile, "team")
            self.assertEqual(report.error_count, 0)

    def test_missing_memory_file_and_secret_are_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_memory(root)
            (root / ".projectmem" / "PROJECT_MAP.md").unlink()
            # Split at runtime so the literal token shape never sits in the repo.
            (root / ".projectmem" / "summary.md").write_text(
                "token = " + "sk-" + "a" * 31 + "\n", encoding="utf-8"
            )

            report = run_doctor(root, python=Path(sys.executable))

            codes = {finding.code for finding in report.findings}
            self.assertFalse(report.ok)
            self.assertIn("memory-file-missing", codes)
            self.assertIn("possible-secret", codes)

    def test_private_profile_requires_full_ignore(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_memory(root)
            report = run_doctor(root, python=Path(sys.executable), profile="private")
            self.assertIn("private-ignore-missing", {item.code for item in report.findings})

            with (root / ".gitignore").open("a", encoding="utf-8") as handle:
                handle.write(".projectmem/\n")
            report = run_doctor(root, python=Path(sys.executable), profile="private")
            self.assertTrue(report.ok, report.render_text())

    def test_family_profile_uses_parent_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            project = workspace / "repo"
            project.mkdir()
            create_memory(workspace)
            (project / "AGENTS.md").write_text(AGENT_BLOCK, encoding="utf-8")

            report = run_doctor(project, python=Path(sys.executable))

            self.assertTrue(report.ok, report.render_text())
            self.assertEqual(report.profile, "family")
            self.assertEqual(report.memory_root, workspace)

    def test_opencode_root_mismatch_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_memory(root)
            (root / "opencode.json").write_text(
                json.dumps(
                    {
                        "mcp": {
                            "pollux": {
                                "command": [
                                    "python",
                                    "-m",
                                    "pollux.engine.mcp_server",
                                    "--root",
                                    "/wrong",
                                ]
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            report = run_doctor(root, python=Path(sys.executable))
            self.assertIn("opencode-root-mismatch", {item.code for item in report.findings})

    def test_malformed_opencode_command_is_reported_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_memory(root)
            (root / "opencode.json").write_text(
                json.dumps({"mcp": {"pollux": {"command": 123}}}),
                encoding="utf-8",
            )

            report = run_doctor(root, python=Path(sys.executable))

            self.assertIn("opencode-config-invalid", {item.code for item in report.findings})


if __name__ == "__main__":
    unittest.main()
