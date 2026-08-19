# Third-Party Notices

## projectmem (format compatibility and design inspiration)

Since 0.2.0, Agent Memory Guardrails does **not** depend on or vendor
`projectmem`; the runtime dependency was removed when the engine became
self-owned. The project remains an important acknowledgment:

- Project: https://github.com/riponcm/projectmem
- Author: Ripon Chandra Malo and projectmem contributors
- License: MIT

The on-disk memory format (`.projectmem/` layout, six typed events, derived
`summary.md` and `issues/`) intentionally matches what `projectmem` 0.2.x
writes so memories move between engines without migration. The secret
redaction patterns and the scoring/context heuristics were adapted from its
MIT-licensed implementation, as was the general workflow design. Agent Memory
Guardrails is not endorsed by the projectmem maintainers.

## mcp

- Project: https://github.com/modelcontextprotocol/python-sdk
- License: MIT

Pinned to `>=1.2,<2` (the v2 SDK removed `mcp.server.fastmcp`).
