"""MCP server integration test: spawn the real server over stdio and drive
it with the real client, proving the 15-tool surface and a write cycle."""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

from support import init_memory


class McpServerTests(unittest.TestCase):
    def test_server_exposes_tools_and_writes(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        with tempfile.TemporaryDirectory() as temp:
            mem = init_memory(Path(temp) / "proj", purpose="MCP smoke.")

            async def scenario() -> dict:
                params = StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "pollux.engine.mcp_server",
                          "--root", str(mem.parent)],
                )
                async with (
                    stdio_client(params) as (read, write),
                    ClientSession(read, write) as session,
                ):
                    await session.initialize()
                    tools = await session.list_tools()
                    names = {tool.name for tool in tools.tools}
                    note = await session.call_tool(
                        "add_note", {"summary": "MCP测试中文笔记"}
                    )
                    issue = await session.call_tool(
                        "log_issue", {"summary": "MCP测试issue"}
                    )
                    attempt = await session.call_tool(
                        "record_attempt",
                        {"summary": "尝试失败", "outcome": "failed"},
                    )
                    fix = await session.call_tool(
                        "record_fix", {"summary": "修复完成"}
                    )
                    search = await session.call_tool(
                        "search_events", {"query": "MCP测试"}
                    )
                    return {
                        "names": names,
                        "note": note.content[0].text,
                        "issue": issue.content[0].text,
                        "attempt": attempt.content[0].text,
                        "fix": fix.content[0].text,
                        "search": search.content[0].text,
                    }

            result = asyncio.run(scenario())
            expected_tools = {
                "get_instructions", "get_summary", "get_project_map", "get_plan",
                "precheck_file", "get_issue", "search_events", "get_score",
                "get_context", "get_global_gotchas", "log_issue",
                "record_attempt", "record_fix", "add_decision", "add_note",
            }
            self.assertTrue(expected_tools.issubset(result["names"]))
            self.assertIn("Recorded note", result["note"])
            self.assertIn("#0001", result["issue"])
            self.assertIn("failed attempt", result["attempt"])
            self.assertIn("closed", result["fix"])
            self.assertIn("MCP测试", result["search"])

            # The events really landed in the memory.
            from pollux.engine.storage import read_events

            events = read_events(mem)
            self.assertEqual([e.type for e in events], ["note", "issue", "attempt", "fix"])


if __name__ == "__main__":
    unittest.main()
