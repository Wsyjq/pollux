"""Self-owned memory engine.

The engine keeps the on-disk contract of the ``.projectmem/`` layout that the
third-party ``projectmem`` 0.2.x package established (append-only
``events.jsonl`` with six typed events, derived ``summary.md`` and
``issues/``), so existing memories carry over without migration. It replaces
the upstream engine wherever that engine is architecturally limited:
unindexed precheck scans, full-file rewrites on every event, cwd-only root
resolution for capture, English-only commit classification, and the absence
of archival and cross-process locking.
"""
