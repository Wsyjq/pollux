"""Secret redaction on the engine write path.

Events land verbatim in a local ``events.jsonl`` that may be committed to git
(team profile), so high-confidence secret patterns are scrubbed before a line
ever touches disk. False positives are worse than false negatives here — a
mangled debugging note costs more than a missed exotic token — so every
pattern is anchored to a recognizable prefix or structural shape.

The pattern set and the ``[REDACTED:<kind>]`` replacement follow the
historical writer so redacted memories stay recognizable across engines.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterable

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{40,}\b")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{82}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("stripe_key", re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{24,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{10,}\b")),
    ("bearer_token", re.compile(r"(?<![A-Za-z])[Bb]earer\s+[A-Za-z0-9._~+/=\-]{20,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

# User-supplied text fields only; structural fields never carry secrets.
REDACTABLE_FIELDS: tuple[str, ...] = (
    "summary",
    "notes",
    "command",
    "git_message",
    "location",
)

REDACTED_PREFIX = "[REDACTED:"


def redact(text: str) -> tuple[str, list[str]]:
    """Return the scrubbed text plus the pattern names that fired."""
    if not text:
        return text, []
    matched: list[str] = []

    def _replace(name: str):
        def _repl(_match: re.Match[str]) -> str:
            matched.append(name)
            return f"{REDACTED_PREFIX}{name}]"

        return _repl

    out = text
    for name, pattern in _PATTERNS:
        out = pattern.sub(_replace(name), out)
    return out, matched


def is_redaction_enabled() -> bool:
    for var in ("POLLUX_NO_REDACT", "PROJECTMEM_NO_REDACT"):
        if os.environ.get(var, "").strip() in {"1", "true", "yes"}:
            return False
    return True


def redact_event_fields(obj: object, fields: Iterable[str] = REDACTABLE_FIELDS) -> list[str]:
    """Scrub redactable string fields on ``obj`` in place; return fired names."""
    if not is_redaction_enabled():
        return []
    fired: list[str] = []
    for field_name in fields:
        value = getattr(obj, field_name, None)
        if isinstance(value, str) and value:
            new_value, matched = redact(value)
            if matched:
                setattr(obj, field_name, new_value)
                fired.extend(matched)
    return fired
