from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent_memory_guardrails.render import render_client_config


class RenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.python = Path("C:/tools/amguard-venv/python.exe")
        self.root = Path("C:/work/example")

    def test_json_clients_render_valid_shapes(self) -> None:
        opencode = json.loads(render_client_config("opencode", self.python, self.root))
        self.assertEqual(opencode["mcp"]["amguard"]["command"][-1], str(self.root))
        self.assertEqual(opencode["mcp"]["amguard"]["type"], "local")
        self.assertIn(
            "agent_memory_guardrails.engine.mcp_server",
            opencode["mcp"]["amguard"]["command"],
        )

        zcode = json.loads(render_client_config("zcode", self.python, self.root))
        self.assertEqual(zcode["mcp"]["servers"]["amguard"]["args"][-1], str(self.root))

        for client in ("claude", "cursor"):
            with self.subTest(client=client):
                rendered = json.loads(render_client_config(client, self.python, self.root))
                self.assertEqual(
                    rendered["mcpServers"]["amguard"]["args"][-1], str(self.root)
                )

    def test_codex_renders_toml_with_explicit_root_and_cwd(self) -> None:
        rendered = render_client_config("codex", self.python, self.root)
        self.assertIn("[mcp_servers.amguard]", rendered)
        self.assertIn('"--root"', rendered)
        self.assertIn(f"cwd = {json.dumps(str(self.root))}", rendered)

    def test_dsh_renders_cordis_patch_entry(self) -> None:
        rendered = render_client_config("dsh", self.python, self.root)
        self.assertIn("- insert:", rendered)
        self.assertIn("name: '@deepseek-ai/dsh-mcp-client'", rendered)
        self.assertIn("serverName: amguard", rendered)
        self.assertIn("transport: stdio", rendered)
        self.assertIn(json.dumps(str(self.python)), rendered)
        self.assertIn(json.dumps(str(self.root)), rendered)

    def test_unknown_client_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported client"):
            render_client_config("unknown", self.python, self.root)


if __name__ == "__main__":
    unittest.main()
