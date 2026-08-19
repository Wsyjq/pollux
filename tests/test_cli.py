from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from pollux.cli import (
    _configure_stream,
    _write_agent_files,
    build_parser,
    main,
)
from pollux.constants import (
    AGENT_MARKER_END,
    AGENT_MARKER_START,
    CLAUDE_MARKER_START,
    LEGACY_AGENT_MARKER2_END,
    LEGACY_AGENT_MARKER2_START,
    LEGACY_AGENT_MARKER_END,
    LEGACY_AGENT_MARKER_START,
)
from pollux.files import PolluxFileError


class CliTests(unittest.TestCase):
    def test_init_does_not_offer_agent_rule_bypass(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["init", "--no-agent-files"])

    def test_init_writes_agent_rules_before_engine_bootstrap(self) -> None:
        from pollux.engine.bootstrap import initialize_memory

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            def bootstrap(memory_root: Path) -> Path:
                self.assertIn(AGENT_MARKER_START, (root / "AGENTS.md").read_text("utf-8"))
                self.assertIn(CLAUDE_MARKER_START, (root / "CLAUDE.md").read_text("utf-8"))
                return initialize_memory(memory_root)

            with (
                patch(
                    "pollux.cli.initialize_memory", side_effect=bootstrap
                ),
                redirect_stdout(io.StringIO()),
            ):
                code = main(["init", str(root), "--profile", "team"])

            self.assertEqual(code, 0)

    def test_init_creates_memory_skeleton_and_governance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with redirect_stdout(io.StringIO()):
                code = main(["init", str(root), "--profile", "team"])
            self.assertEqual(code, 0)
            mem = root / ".projectmem"
            for name in (
                "config.toml",
                "events.jsonl",
                "summary.md",
                "PROJECT_MAP.md",
                "plan.md",
                "AI_INSTRUCTIONS.md",
            ):
                self.assertTrue((mem / name).is_file(), name)
            self.assertTrue((mem / "issues").is_dir())
            self.assertIn(AGENT_MARKER_START, (root / "AGENTS.md").read_text("utf-8"))
            gitignore = (root / ".gitignore").read_text("utf-8")
            self.assertIn(".projectmem/events.jsonl", gitignore)
            self.assertIn(".projectmem/cache/", gitignore)

    def test_init_is_idempotent_across_repeats(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["init", str(root)]), 0)
            first = (root / ".projectmem" / "config.toml").read_text("utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["init", str(root)]), 0)
            self.assertEqual(
                (root / ".projectmem" / "config.toml").read_text("utf-8"), first
            )

    def test_non_tty_stream_defaults_to_utf8_for_automation(self) -> None:
        class ReconfigurableStream(io.StringIO):
            configured: dict[str, str] | None = None

            def reconfigure(self, **kwargs: str) -> None:
                self.configured = kwargs

        stream = ReconfigurableStream()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PYTHONIOENCODING", None)
            _configure_stream(stream)

        self.assertEqual(stream.configured, {"encoding": "utf-8", "errors": "backslashreplace"})

    def test_stream_keeps_explicit_pythonioencoding(self) -> None:
        class ReconfigurableStream(io.StringIO):
            configured = False

            def reconfigure(self, **_kwargs: str) -> None:
                self.configured = True

        stream = ReconfigurableStream()
        with patch.dict(os.environ, {"PYTHONIOENCODING": "gbk"}):
            _configure_stream(stream)

        self.assertFalse(stream.configured)

    def test_stream_keeps_interactive_console_encoding(self) -> None:
        class InteractiveStream(io.StringIO):
            configured = False

            def isatty(self) -> bool:
                return True

            def reconfigure(self, **_kwargs: str) -> None:
                self.configured = True

        stream = InteractiveStream()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PYTHONIOENCODING", None)
            _configure_stream(stream)

        self.assertFalse(stream.configured)

    def test_render_discovers_memory_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            memory = root / ".projectmem"
            memory.mkdir()
            (memory / "config.toml").write_text("\n", encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["render", "opencode", str(root), "--python", sys.executable])
            self.assertEqual(code, 0)
            rendered = json.loads(output.getvalue())
            self.assertEqual(rendered["mcp"]["pollux"]["command"][-1], str(root))
            self.assertEqual(rendered["mcp"]["pollux"]["type"], "local")

    def test_agent_writer_migrates_legacy_marker_without_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "AGENTS.md"
            path.write_text(
                "# AGENTS.md\n\n"
                f"{LEGACY_AGENT_MARKER_START}\nold rules\n{LEGACY_AGENT_MARKER_END}\n",
                encoding="utf-8",
            )

            _write_agent_files(root, root)
            first = path.read_text(encoding="utf-8")
            _write_agent_files(root, root)

            self.assertNotIn(LEGACY_AGENT_MARKER_START, first)
            self.assertEqual(first.count(AGENT_MARKER_START), 1)
            self.assertEqual(path.read_text(encoding="utf-8"), first)

    def test_agent_writer_rejects_mixed_legacy_and_current_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "AGENTS.md"
            original = (
                "# AGENTS.md\n\n"
                f"{LEGACY_AGENT_MARKER_START}\nlegacy\n{LEGACY_AGENT_MARKER_END}\n\n"
                f"{AGENT_MARKER_START}\ncurrent\n{AGENT_MARKER_END}\n"
            )
            path.write_text(original, encoding="utf-8")

            with self.assertRaises(PolluxFileError):
                _write_agent_files(root, root)

            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_agent_writer_migrates_oldest_legacy_marker_without_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "AGENTS.md"
            path.write_text(
                "# AGENTS.md\n\n"
                f"{LEGACY_AGENT_MARKER2_START}\nold rules\n{LEGACY_AGENT_MARKER2_END}\n",
                encoding="utf-8",
            )

            _write_agent_files(root, root)
            first = path.read_text(encoding="utf-8")
            _write_agent_files(root, root)

            self.assertNotIn(LEGACY_AGENT_MARKER2_START, first)
            self.assertEqual(first.count(AGENT_MARKER_START), 1)
            self.assertEqual(path.read_text(encoding="utf-8"), first)

    def test_agent_writer_rejects_mixed_legacy_generations(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "AGENTS.md"
            original = (
                "# AGENTS.md\n\n"
                f"{LEGACY_AGENT_MARKER_START}\nnewer legacy\n{LEGACY_AGENT_MARKER_END}\n\n"
                f"{LEGACY_AGENT_MARKER2_START}\nolder legacy\n{LEGACY_AGENT_MARKER2_END}\n"
            )
            path.write_text(original, encoding="utf-8")

            with self.assertRaises(PolluxFileError):
                _write_agent_files(root, root)

            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_family_init_supports_hooks_in_project_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            project = workspace / "repo"
            project.mkdir()
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "init",
                        str(project),
                        "--profile",
                        "family",
                        "--memory-root",
                        str(workspace),
                        "--enable-hooks",
                    ]
                )
            self.assertEqual(code, 0)
            post = (project / ".git" / "hooks" / "post-commit").read_text("utf-8")
            self.assertIn("capture commit", post)

    def test_render_without_memory_returns_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            error = io.StringIO()
            with redirect_stderr(error):
                code = main(["render", "codex", temp])
            self.assertEqual(code, 2)
            self.assertIn("No memory root found", error.getvalue())


if __name__ == "__main__":
    unittest.main()
