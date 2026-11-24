"""The monitor app: its API over the telemetry, and who may open it."""
import os
import tempfile
import unittest
from pathlib import Path

import openai
from fastapi.testclient import TestClient

from jobtracker import config, registry
from jobtracker.telemetry import store
from monitor import app as monitor
from tests.test_assistant import FakeOpenAI, call
from tests.test_core import reset  # sets the temp data/output env vars first
from tests.test_users import OWNER, set_cv_email

DB_DIR = Path(tempfile.mkdtemp(prefix="jobtracker-monitor-tests-"))


class MonitorTests(unittest.TestCase):
    def setUp(self):
        reset()
        os.environ["OPENAI_API_KEY"] = "test"
        self._openai, openai.OpenAI = openai.OpenAI, FakeOpenAI
        self._db, self._on = config.TELEMETRY_DB, config.TELEMETRY
        config.TELEMETRY_DB, config.TELEMETRY = DB_DIR / f"{self.id().rsplit('.', 1)[-1]}.db", True
        FakeOpenAI.sent = []
        self.client = TestClient(monitor.app)

    def tearDown(self):
        store.flush()
        openai.OpenAI = self._openai
        config.TELEMETRY_DB, config.TELEMETRY = self._db, self._on
        config.TAILSCALE_USERS = False
        os.environ["OPENAI_API_KEY"] = ""

    def chat(self):
        FakeOpenAI.script = [([call("company-list", {}, "c1"), call("company-list", {}, "c2")], ""), ([], "Two lists.")]
        chat = registry.run("chat.send", {"message": "List twice"})
        FakeOpenAI.script = [([], "Bye.")]
        registry.run("chat.send", {"message": "Thanks", "chat_id": chat["id"]})
        self.assertTrue(store.flush())
        return chat

    def test_empty_database(self):
        body = self.client.get("/api/overview").json()
        self.assertEqual(body["totals"]["turns"], 0)
        self.assertEqual(self.client.get("/api/conversations").json(), [])

    def test_overview_conversation_and_turn(self):
        chat = self.chat()
        overview = self.client.get("/api/overview?days=7&user=*").json()
        self.assertEqual((overview["totals"]["turns"], overview["totals"]["conversations"]), (2, 1))
        self.assertEqual(overview["totals"]["tool_calls"], 2)
        self.assertEqual(overview["statuses"], {"completed": 2})
        self.assertEqual(overview["by_tool"][0]["action"], "company.list")
        self.assertEqual(len(overview["per_day"]), 7)
        self.assertEqual(sum(sum(d["by_user"].values()) for d in overview["per_day"]), 2)

        conversations = self.client.get("/api/conversations").json()
        self.assertEqual([c["conversation_id"] for c in conversations], [chat["id"]])
        conversation = self.client.get(f"/api/conversations/{chat['id']}").json()
        self.assertEqual([t["turn_index"] for t in conversation["turns"]], [0, 1])
        self.assertEqual(conversation["turns"][0]["user_text"], "List twice")

        turn = self.client.get(f"/api/turns/{chat['id']}:0").json()
        self.assertEqual([s["kind"] for s in turn["spans"]][:3], ["turn", "history", "llm_call"])
        self.assertEqual(sum(s["kind"] == "tool" for s in turn["spans"]), 2)
        self.assertEqual(turn["turn_indexes"], [0, 1])
        self.assertTrue(turn["summary"]["consistent"])
        self.assertEqual(turn["scores"], [])
        self.assertEqual(self.client.get("/api/turns/nope:0").status_code, 404)
        self.assertIn("Ezri Monitor", self.client.get("/").text)

    def test_owner_only_behind_tailscale(self):
        set_cv_email(OWNER)
        config.TAILSCALE_USERS = True
        self.assertEqual(self.client.get("/api/overview").status_code, 403)  # not through tailscale serve
        friend = self.client.get("/api/overview", headers={"Tailscale-User-Login": "friend@example.com"})
        self.assertEqual(friend.status_code, 403)
        owner = self.client.get("/api/overview", headers={"Tailscale-User-Login": OWNER})
        self.assertEqual(owner.status_code, 200)

    def test_password_needs_the_app_login(self):
        from jobtracker import auth
        original, config.PASSWORD = config.PASSWORD, "secret"
        try:
            self.assertEqual(self.client.get("/api/overview").status_code, 401)
            self.client.cookies.set(auth.COOKIE, auth.make_token())
            self.assertEqual(self.client.get("/api/overview").status_code, 200)
        finally:
            config.PASSWORD = original


if __name__ == "__main__":
    unittest.main()
