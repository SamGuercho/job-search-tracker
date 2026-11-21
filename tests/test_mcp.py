"""MCP server, exercised through a real MCP client connected in memory."""
import json
import unittest

import anyio
from fastapi.testclient import TestClient
from mcp import Client

from jobtracker import config, registry
from jobtracker.api import app
from jobtracker.mcp_server import build_server
from tests.test_core import reset  # sets the temp data/output env vars first
from tests.test_users import FRIEND, OWNER, set_cv_email


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



HTTP = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


class McpHttpTests(unittest.TestCase):
    """The same tools served by the web app at /mcp, for remote clients (Codex on another machine)."""

    def setUp(self):
        reset()

    def tearDown(self):
        config.TAILSCALE_USERS, config.OWNER = False, ""

    def rpc(self, client, method, params, login=None):
        headers = {**HTTP, "mcp-protocol-version": "2025-06-18"}
        if login:
            headers["Tailscale-User-Login"] = login
        return client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})

    def call(self, client, tool, args=None, login=None):
        response = self.rpc(client, "tools/call", {"name": tool, "arguments": args or {}}, login)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["result"]

    def test_tools_over_http(self):
        with TestClient(app) as client:
            init = self.rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                   "clientInfo": {"name": "test", "version": "1"}})
            self.assertIn("Ezri, the job search tracker", init.json()["result"]["instructions"])
            names = {t["name"] for t in self.rpc(client, "tools/list", {}).json()["result"]["tools"]}
            self.assertIn("company_create", names)
            self.call(client, "company_create", {"name": "Nimbus"})
            self.assertIn("Nimbus", self.call(client, "company_list")["content"][0]["text"])

    def test_each_tailscale_user_gets_their_own_data(self):
        set_cv_email(OWNER)
        config.TAILSCALE_USERS = True
        with TestClient(app) as client:
            self.call(client, "company_create", {"name": "Friend Corp"}, login=FRIEND)
            self.assertIn("Friend Corp", self.call(client, "company_list", login=FRIEND)["content"][0]["text"])
            self.assertNotIn("Friend Corp", self.call(client, "company_list", login=OWNER)["content"][0]["text"])
            self.assertEqual(self.rpc(client, "tools/list", {}).status_code, 403)  # no Tailscale identity


if __name__ == "__main__":
    unittest.main()
