# Security Policy

## Supported versions

This project is pre-release. Security fixes target the latest alpha branch only.

## Reporting a vulnerability

Do not open a public issue containing credentials, private memory, or an exploitable path. Once a
public host is configured, use its private security-advisory channel. Until then, keep the report
local and provide only a redacted reproduction.

## Security boundaries

- `amguard` executes the selected Python interpreter and projectmem module locally.
- It does not upload source code or memory.
- It writes only the selected project/memory roots and uses atomic replacement for managed text.
- It does not merge global AI-client configuration automatically.
- Secret detection is heuristic and must not replace a dedicated scanner such as gitleaks.

See [docs/threat-model.md](./docs/threat-model.md) for the detailed model.
