"""MCP server, exercised through a real MCP client connected in memory."""
import json
import unittest

import anyio
from mcp import Client

from jobtracker import registry
from jobtracker.mcp_server import build_server
from tests.test_core import reset  # sets the temp data/output env vars first


def with_client(fn):
    """Run `await fn(client)` against a fresh in-process server."""
    async def main():
        async with Client(build_server()) as client:
            return await fn(client)
    return anyio.run(main)


class McpTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_every_action_is_a_tool_except_chat(self):
        tools = with_client(lambda c: c.list_tools()).tools
        names = {t.name for t in tools}
        expected = {a.replace(".", "_") for a in registry.ACTIONS if not a.startswith("chat.")}
        self.assertEqual(names, expected)

    def test_hints_follow_the_registry(self):
        tools = {t.name: t for t in with_client(lambda c: c.list_tools()).tools}
        self.assertTrue(tools["company_list"].annotations.read_only_hint)
        self.assertTrue(tools["company_delete"].annotations.destructive_hint)
        self.assertIn("ask the user", tools["company_delete"].description)
        self.assertFalse(tools["company_create"].annotations.destructive_hint)
        self.assertEqual(tools["process_set_state"].input_schema["required"], ["company", "step", "state"])

    def test_calls_run_the_real_actions(self):
        async def scenario(client):
            created = await client.call_tool("company_create", {"name": "Nimbus", "position": "AI Engineer"})
            done = await client.call_tool("process_set_state", {"company": "Nimbus", "step": "Applied", "state": "done"})
            return created, done
        created, done = with_client(scenario)
        self.assertFalse(created.is_error)
        self.assertEqual(json.loads(created.content[0].text)["status"], "Not applied")
        self.assertEqual(json.loads(done.content[0].text)["status"], "Applied")

    def test_errors_are_reported_not_raised(self):
        bad = with_client(lambda c: c.call_tool("process_get", {"company": "Nobody"}))
        self.assertTrue(bad.is_error)
        self.assertIn("No application matches", bad.content[0].text)
        invalid = with_client(lambda c: c.call_tool("company_create", {"oops": 1}))
        self.assertTrue(invalid.is_error)


if __name__ == "__main__":
    unittest.main()
