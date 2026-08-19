# Threat Model

## Assets

- Raw project memory and debugging history.
- Distilled summaries, issue files, plans, and maps.
- Local source paths and technology metadata.
- AI-client MCP configuration.
- Credentials accidentally pasted into memory.

## Trust boundaries

- The selected Python executable and installed projectmem package are trusted local code.
- The target project and memory roots are user-selected writable locations.
- Git remotes, cloud dashboards, and hosted memory services are outside v0.1.
- AI clients launch projectmem as a local stdio subprocess.

## Addressed threats

| Threat | Mitigation |
|---|---|
| Raw event history committed accidentally | Ignore policy plus tracked-file doctor check |
| Existing Agent instructions overwritten | Marker-scoped atomic replacement |
| Corrupt/partial marker silently accepted | Fail-closed marker validation |
| Wrong project receives memory writes | Explicit `--root` rendering and root audit |
| Parent-root hooks silently do nothing | Family-mode hook rejection and doctor finding |
| Hook invokes a different projectmem install | Selected-runtime pinning and drift audit |
| Existing hook code is rewritten | Changes are limited to one complete projectmem marker block |
| POSIX hook loses its executable bit | Atomic replacement preserves existing file mode |
| Shared family memory escapes checks | Agent, secret, and Git-tracking audits cover both roots |
| Credential appears in memory | Bounded pattern scan that never echoes the value |
| Dependency behavior changes silently | Narrow projectmem compatibility range |

## Residual risks

- Regex scans have false negatives and false positives.
- A malicious Python interpreter or projectmem installation can execute arbitrary local code.
- Concurrent writers are governed by projectmem's own locking behavior.
- Local users with filesystem access can read private-mode memory.
- Rendered client configuration still requires a human-safe merge into existing config.

## Operational guidance

- Use an isolated Python environment.
- Review `doctor --json` in CI before publishing distilled memory.
- Run a dedicated secret scanner before release.
- Re-verify package names, licenses, and engine behavior before every public release.
