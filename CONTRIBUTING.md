# Contributing

Contributions are welcome once the public repository is available.

## Development setup

```bash
python -m venv .venv
python -m pip install -e .
python -m pip install ruff build
```

## Required checks

```bash
python -m unittest discover -s tests -v
python -m ruff check .
python -m build
```

## Design expectations

- Prefer conservative, explicit behavior over silent configuration changes.
- Preserve existing user content outside marked blocks.
- Do not claim a hook, MCP root, or privacy policy works without executable evidence.
- Keep the event engine behind an adapter boundary; v0.1 supports projectmem 0.2.x.
- Never include real credentials or private project memory in fixtures.
- Add a regression test for every bug fix.

## Commit and review scope

Keep changes focused. Explain why a behavior changes, rejected alternatives, and the evidence used
to verify it. Do not open a pull request that combines engine forking with guardrail changes.
