from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_memory_guardrails.files import (
    GuardrailsFileError,
    ensure_lines,
    managed_hook_block,
    set_marked_block,
    write_text_atomic,
)


class FileOperationsTests(unittest.TestCase):
    def test_atomic_replacement_preserves_existing_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hook"
            path.write_text("old\n", encoding="utf-8")
            existing_mode = stat.S_IMODE(path.stat().st_mode)

            with patch("agent_memory_guardrails.files.os.chmod") as chmod:
                write_text_atomic(path, "new\n")

            chmod.assert_called_once_with(path, existing_mode)
            self.assertEqual(path.read_text(encoding="utf-8"), "new\n")

    def test_atomic_write_platform_newline_translates(self) -> None:
        import os

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "derived.md"
            write_text_atomic(path, "a\nb\n", newline=None)
            raw = path.read_bytes()
            if os.name == "nt":
                self.assertEqual(raw, b"a\r\nb\r\n")
            else:
                self.assertEqual(raw, b"a\nb\n")

    def test_marked_block_is_idempotent_and_preserves_existing_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "AGENTS.md"
            path.write_text("# Existing\n\nKeep this.\n", encoding="utf-8")
            block = "<!-- start -->\nmanaged\n<!-- end -->\n"

            self.assertTrue(
                set_marked_block(path, "<!-- start -->", "<!-- end -->", block, heading="# New")
            )
            first = path.read_text(encoding="utf-8")
            self.assertIn("Keep this.", first)
            self.assertEqual(first.count("<!-- start -->"), 1)
            self.assertFalse(
                set_marked_block(path, "<!-- start -->", "<!-- end -->", block, heading="# New")
            )
            self.assertEqual(path.read_text(encoding="utf-8"), first)

    def test_marked_block_refuses_incomplete_or_reversed_markers(self) -> None:
        cases = (
            "<!-- start -->\nmissing end\n",
            "<!-- end -->\ncontent\n<!-- start -->\n",
        )
        for content in cases:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "AGENTS.md"
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(GuardrailsFileError):
                    set_marked_block(
                        path,
                        "<!-- start -->",
                        "<!-- end -->",
                        "<!-- start -->\nnew\n<!-- end -->",
                        heading="# AGENTS.md",
                    )

    def test_ensure_lines_only_appends_missing_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".gitignore"
            path.write_text("dist/\n", encoding="utf-8")
            self.assertTrue(ensure_lines(path, ("dist/", ".projectmem/events.jsonl")))
            self.assertFalse(ensure_lines(path, ("dist/", ".projectmem/events.jsonl")))
            self.assertEqual(
                path.read_text(encoding="utf-8").splitlines(),
                ["dist/", ".projectmem/events.jsonl"],
            )

    def test_managed_hook_block_extracts_or_rejects(self) -> None:
        start, end = "# >>> m >>>", "# <<< m <<<"
        self.assertIsNone(managed_hook_block("no markers here", start, end))
        content = f"header\n{start}\nbody\n{end}\nfooter\n"
        block = managed_hook_block(content, start, end)
        self.assertTrue(block.startswith(start))
        self.assertTrue(block.endswith(end))
        with self.assertRaises(GuardrailsFileError):
            managed_hook_block(f"{start}\nbroken\n", start, end)
        with self.assertRaises(GuardrailsFileError):
            managed_hook_block(f"{end}\n{start}\n", start, end)


if __name__ == "__main__":
    unittest.main()
