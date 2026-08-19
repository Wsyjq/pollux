"""Event model contract tests: serialization, validation, references."""
from __future__ import annotations

import json
import unittest

from agent_memory_guardrails.engine.errors import EngineError
from agent_memory_guardrails.engine.models import (
    Event,
    normalize_timestamp,
    resolve_event_ref,
    superseded_ids,
)


class SerializationTests(unittest.TestCase):
    def test_to_dict_drops_empty_values_and_sorts_keys(self) -> None:
        event = Event(
            type="note",
            summary="hello",
            id="evt_fixed",
            timestamp="2026-08-01T00:00:00Z",
        )
        line = json.dumps(event.to_dict(), sort_keys=True)
        self.assertEqual(
            line,
            '{"id": "evt_fixed", "summary": "hello", '
            '"timestamp": "2026-08-01T00:00:00Z", "type": "note"}',
        )

    def test_full_event_round_trips_through_jsonl(self) -> None:
        event = Event(
            type="attempt",
            summary="tried X",
            id="evt_round",
            timestamp="2026-08-01T01:02:03Z",
            issue_id="0042",
            outcome="partial",
            files=["src/a.py", "src/b.py"],
            location="src/a.py:10",
            git_commit="abc1234",
            auto_captured=True,
            capture_source="git_post_commit",
            capture_confidence="medium",
            git_message="fix: thing",
            supersedes="evt_old",
        )
        parsed = Event.from_dict(json.loads(event.to_jsonl()))
        self.assertEqual(parsed, event)

    def test_chinese_summary_escapes_to_ascii_json(self) -> None:
        event = Event(
            type="note", summary="中文笔记", id="evt_cn", timestamp="2026-08-01T00:00:00Z"
        )
        line = event.to_jsonl()
        self.assertIn("\\u4e2d\\u6587", line)
        parsed = Event.from_dict(json.loads(line))
        self.assertEqual(parsed.summary, "中文笔记")


class ValidationTests(unittest.TestCase):
    def test_rejects_unknown_type(self) -> None:
        with self.assertRaises(ValueError):
            Event(type="incident", summary="x")

    def test_rejects_unknown_outcome(self) -> None:
        with self.assertRaises(ValueError):
            Event(type="attempt", summary="x", outcome="meh")

    def test_rejects_blank_summary(self) -> None:
        with self.assertRaises(ValueError):
            Event(type="note", summary="   ")

    def test_rejects_unknown_capture_fields(self) -> None:
        with self.assertRaises(ValueError):
            Event(
                type="note",
                summary="x",
                capture_source="telepathy",
            )


class TimestampTests(unittest.TestCase):
    def test_git_style_timestamp_normalizes_to_zulu(self) -> None:
        self.assertEqual(
            normalize_timestamp("2026-05-12 21:07:46 -0600"),
            "2026-05-13T03:07:46Z",
        )

    def test_iso_with_offset_normalizes(self) -> None:
        self.assertEqual(
            normalize_timestamp("2026-08-01T10:00:00+08:00"),
            "2026-08-01T02:00:00Z",
        )

    def test_unparseable_timestamp_passes_through(self) -> None:
        self.assertEqual(normalize_timestamp("not-a-date"), "not-a-date")


class ReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events = [
            Event(type="note", summary="one", id="evt_aaaa1111", timestamp="2026-01-01T00:00:00Z"),
            Event(type="note", summary="two", id="evt_aaab2222", timestamp="2026-01-01T00:00:00Z"),
        ]

    def test_full_id_resolves(self) -> None:
        self.assertEqual(resolve_event_ref(self.events, "evt_aaaa1111").summary, "one")

    def test_unique_prefix_resolves(self) -> None:
        self.assertEqual(resolve_event_ref(self.events, "aaaa1").summary, "one")

    def test_ambiguous_prefix_raises(self) -> None:
        with self.assertRaises(EngineError):
            resolve_event_ref(self.events, "aaa")

    def test_no_match_raises(self) -> None:
        with self.assertRaises(EngineError):
            resolve_event_ref(self.events, "zzzz")

    def test_superseded_ids_computed_at_read_time(self) -> None:
        events = self.events + [
            Event(
                type="decision",
                summary="new",
                id="evt_dec",
                timestamp="2026-02-01T00:00:00Z",
                supersedes="evt_aaaa1111",
            )
        ]
        self.assertEqual(superseded_ids(events), {"evt_aaaa1111"})


if __name__ == "__main__":
    unittest.main()
