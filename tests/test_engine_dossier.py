"""Generalized file-dossier tests, ported from the original tool suite.

Each test builds a throwaway git repository with real commits, cards, and an
index — nothing touches any real repository or memory.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import init_memory

from pollux.engine.dossier import (
    DossierError,
    build_dossier,
    extract_card_section,
    normalize_path,
    schema_template,
    validate_index,
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        stdin=subprocess.DEVNULL,
    )


class DossierRepoCase(unittest.TestCase):
    """A git repo with one profiled file and a one-card index."""

    REPO_ID = "demo-repo"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.repo = base / self.REPO_ID
        (self.repo / "src").mkdir(parents=True)
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "t@example.com")
        _git(self.repo, "config", "user.name", "T")
        (self.repo / "src" / "app.py").write_text("print('v1')\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "新增初始版本")
        self.commit = _git(
            self.repo, "rev-parse", "HEAD"
        ).stdout.strip()
        self.blob = _git(
            self.repo, "rev-parse", "HEAD:src/app.py"
        ).stdout.strip()
        self.cards_dir = self.repo / "docs" / "file-cards"
        self.cards_dir.mkdir(parents=True)
        self.index_path = self.cards_dir / "index.json"
        self._write_card()
        self._write_index()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_card(self, canonical: str | None = None) -> None:
        canonical = canonical or f"{self.REPO_ID}/src/app.py"
        card = (
            f"# Cards\n\n## `{canonical}`\n\n"
            "- 状态：active\n- 作用：示例入口文件。\n- 验证：python -m unittest。\n"
        )
        (self.cards_dir / "cards.md").write_text(card, encoding="utf-8")

    def _write_index(self, **overrides) -> None:
        entry = {
            "path": f"{self.REPO_ID}/src/app.py",
            "card": "docs/file-cards/cards.md",
            "tier": "A",
            "verifiedCommit": self.commit,
            "verifiedBlob": self.blob,
        }
        entry.update(overrides)
        index = {
            "schemaVersion": 1,
            "repositoryId": self.REPO_ID,
            "cards": [entry],
        }
        self.index_path.write_text(
            json.dumps(index, indent=2), encoding="utf-8"
        )

    def _loader(self, canonical: str) -> dict[str, str]:
        return {
            "precheck": "[OK] fake precheck",
            "events": "2026-08-01 [evt_x] note: fake event",
            "context": "## fake context",
        }


class PathNormalizationTests(DossierRepoCase):
    def test_three_input_forms_normalize_identically(self) -> None:
        repo_id = self.REPO_ID
        results = {
            normalize_path(self.repo, "src/app.py", repo_id),
            normalize_path(self.repo, f"{repo_id}/src/app.py", repo_id),
            normalize_path(self.repo, str(self.repo / "src" / "app.py"), repo_id),
        }
        self.assertEqual(len(results), 1)
        canonical, source = results.pop()
        self.assertEqual(canonical, f"{repo_id}/src/app.py")
        self.assertEqual(source, (self.repo / "src" / "app.py").resolve())

    def test_paths_outside_repository_are_rejected(self) -> None:
        with self.assertRaises(DossierError):
            normalize_path(self.repo, "../outside.py", self.REPO_ID)
        with self.assertRaises(DossierError):
            normalize_path(self.repo, str(Path(self._tmp.name) / "other"), self.REPO_ID)

    def test_repository_id_defaults_to_repo_dir_name_concept(self) -> None:
        # The CLI derives repository_id from the repo dir; here we verify a
        # different id works when passed explicitly.
        canonical, _ = normalize_path(self.repo, "src/app.py", "other-id")
        self.assertEqual(canonical, "other-id/src/app.py")


class CardSectionTests(DossierRepoCase):
    def test_extraction_is_exact_and_stops_at_next_heading(self) -> None:
        card = (
            "# Cards\n\n"
            "## `demo-repo/src/app.py`\n\n- mine\n\n"
            "## `demo-repo/src/other.py`\n\n- not mine\n"
        )
        section = extract_card_section(card, "demo-repo/src/app.py")
        self.assertIsNotNone(section)
        self.assertIn("mine", section)
        self.assertNotIn("not mine", section)
        self.assertIsNone(extract_card_section(card, "demo-repo/missing.py"))


class ValidateIndexTests(DossierRepoCase):
    def test_valid_index_passes(self) -> None:
        self.assertEqual(validate_index(self.repo, self.index_path), [])

    def test_stale_working_blob_is_flagged(self) -> None:
        (self.repo / "src" / "app.py").write_text("print('v2')\n", encoding="utf-8")
        errors = validate_index(self.repo, self.index_path)
        self.assertTrue(any("STALE blob" in e for e in errors))

    def test_verified_working_blob_accepts_uncommitted_state(self) -> None:
        (self.repo / "src" / "app.py").write_text("print('v2')\n", encoding="utf-8")
        actual = _git(self.repo, "hash-object", "--", "src/app.py").stdout.strip()
        self._write_index(verifiedWorkingBlob=actual)
        self.assertEqual(validate_index(self.repo, self.index_path), [])

    def test_commit_blob_mismatch_is_flagged(self) -> None:
        self._write_index(verifiedBlob="0" * 40)
        errors = validate_index(self.repo, self.index_path)
        self.assertTrue(any("commit/blob mismatch" in e for e in errors))

    def test_orphan_and_duplicate_headings_are_flagged(self) -> None:
        extra = self.cards_dir / "extra.md"
        extra.write_text(
            f"# Extra\n\n"
            f"## `{self.REPO_ID}/src/app.py`\n\n- duplicate of indexed\n\n"
            f"## `{self.REPO_ID}/src/ghost.py`\n\n- orphan, not in index\n",
            encoding="utf-8",
        )
        errors = validate_index(self.repo, self.index_path)
        self.assertTrue(any("orphan card heading" in e for e in errors))
        self.assertTrue(any("duplicate card heading" in e for e in errors))

    def test_duplicate_index_paths_are_flagged(self) -> None:
        index = json.loads(self.index_path.read_text(encoding="utf-8"))
        index["cards"].append(dict(index["cards"][0]))
        self.index_path.write_text(json.dumps(index), encoding="utf-8")
        errors = validate_index(self.repo, self.index_path)
        self.assertTrue(any("duplicate index path" in e for e in errors))


class BuildDossierTests(DossierRepoCase):
    def test_dossier_combines_card_memory_freshness_and_git(self) -> None:
        text = build_dossier(
            repo_root=self.repo,
            input_path="src/app.py",
            index_path=self.index_path,
            repository_id=self.REPO_ID,
            memory_loader=self._loader,
        )
        self.assertIn("# File dossier", text)
        self.assertIn(f"- Canonical path: `{self.REPO_ID}/src/app.py`", text)
        self.assertIn("## Static responsibility card", text)
        self.assertIn("- 作用：示例入口文件。", text)
        self.assertIn("Card freshness: **CURRENT**", text)
        self.assertIn("[OK] fake precheck", text)
        self.assertIn("2026-08-01 [evt_x] note: fake event", text)
        self.assertIn("新增初始版本", text)  # git timeline entry
        self.assertIn("proves why", text)  # epigraph

    def test_unprofiled_file_degrades_explicitly(self) -> None:
        (self.repo / "src" / "new.py").write_text("x = 1\n", encoding="utf-8")
        text = build_dossier(
            repo_root=self.repo,
            input_path="src/new.py",
            index_path=self.index_path,
            repository_id=self.REPO_ID,
            memory_loader=self._loader,
        )
        self.assertIn("UNPROFILED", text)
        self.assertIn("untracked at HEAD", text)

    def test_memory_loader_failure_keeps_git_evidence(self) -> None:
        def broken(_canonical: str) -> dict[str, str]:
            raise RuntimeError("boom")

        text = build_dossier(
            repo_root=self.repo,
            input_path="src/app.py",
            index_path=self.index_path,
            repository_id=self.REPO_ID,
            memory_loader=broken,
        )
        self.assertIn("memory loader failed: boom", text)
        self.assertIn("新增初始版本", text)


class EngineMemoryIntegrationTests(DossierRepoCase):
    def test_native_loader_reads_parent_memory(self) -> None:
        from pollux.engine.dossier import engine_memory_loader

        base = Path(self._tmp.name)
        mem = init_memory(base)  # parent of the repo owns the memory
        from support import write_events

        from pollux.engine.models import Event

        write_events(
            mem,
            [
                Event(
                    type="note",
                    summary=f"about {self.REPO_ID}/src/app.py import",
                    timestamp="2026-08-01T00:00:00Z",
                )
            ],
        )
        sections = engine_memory_loader(self.repo, f"{self.REPO_ID}/src/app.py")
        self.assertIn(f"about {self.REPO_ID}/src/app.py import", sections["events"])
        self.assertIn("precheck", sections)
        self.assertIn("context", sections)


class SchemaTemplateTests(unittest.TestCase):
    def test_schema_binds_repository_id(self) -> None:
        schema = json.loads(schema_template("my-repo"))
        self.assertEqual(schema["properties"]["repositoryId"]["const"], "my-repo")
        pattern = schema["properties"]["cards"]["items"]["properties"]["path"]["pattern"]
        self.assertEqual(pattern, "^my-repo/.+")


if __name__ == "__main__":
    unittest.main()
