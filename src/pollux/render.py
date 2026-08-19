from __future__ import annotations

import json
from pathlib import Path

from pollux.constants import CLIENTS


def _command(python: Path, memory_root: Path) -> list[str]:
    return [
        str(python),
        "-m",
        "pollux.engine.mcp_server",
        "--root",
        str(memory_root),
    ]


def _dsh_patch(python: Path, memory_root: Path) -> str:
    """A cordis.patch.yml insert entry for a DeepSeek Harness profile.

    DSH composes plugin trees from patch layers; the mcp-pollux row makes
    the engine's tools appear as ``mcp__pollux__<name>``. The interpreter
    must be the one the package is installed in (usually this venv).
    """
    command = _command(python, memory_root)
    args = "".join(f"      - {json.dumps(item)}\n" for item in command[1:])
    return (
        "# Append to (or replace) cordis.patch.yml in the dsh profile dir\n"
        "# ($DSH_HOME/profiles/web/). Tools appear as mcp__pollux__<name>.\n"
        "- insert:\n"
        "    - id: mcp-pollux\n"
        "      name: '@deepseek-ai/dsh-mcp-client'\n"
        "      config:\n"
        "        serverName: pollux\n"
        "        transport: stdio\n"
        f"        command: {json.dumps(command[0])}\n"
        f"        args:\n{args}"
    )


def render_client_config(client: str, python: Path, memory_root: Path) -> str:
    if client not in CLIENTS:
        raise ValueError(f"Unsupported client: {client}. Choose from {', '.join(CLIENTS)}.")
    command = _command(python, memory_root)
    if client == "opencode":
        data = {
            "$schema": "https://opencode.ai/config.json",
            "mcp": {
                "pollux": {
                    "type": "local",
                    "command": command,
                    "enabled": True,
                }
            },
        }
        return json.dumps(data, indent=2) + "\n"
    if client == "zcode":
        data = {
            "mcp": {
                "servers": {
                    "pollux": {
                        "type": "stdio",
                        "command": command[0],
                        "args": command[1:],
                    }
                }
            }
        }
        return json.dumps(data, indent=2) + "\n"
    if client in ("claude", "cursor"):
        data = {
            "mcpServers": {
                "pollux": {
                    "command": command[0],
                    "args": command[1:],
                }
            }
        }
        return json.dumps(data, indent=2) + "\n"
    if client == "dsh":
        return _dsh_patch(python, memory_root)

    quote = json.dumps
    args = ", ".join(quote(item) for item in command[1:])
    return (
        "[mcp_servers.pollux]\n"
        f"command = {quote(command[0])}\n"
        f"args = [{args}]\n"
        f"cwd = {quote(str(memory_root))}\n"
    )
