from __future__ import annotations


class EngineError(RuntimeError):
    """Base class for engine failures surfaced through the CLI and MCP."""
