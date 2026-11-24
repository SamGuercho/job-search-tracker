"""Flags: thumbs up/down in the chat, detectors, the owner's flags and the review queue in the monitor."""
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import openai
from fastapi.testclient import TestClient

from jobtracker import config, registry
from jobtracker.services import assistant, companies
from jobtracker.telemetry import detect, store
from monitor import app as monitor
from tests.test_assistant import FakeOpenAI, call
from tests.test_core import reset  # sets the temp data/output env vars first

DB_DIR = Path(tempfile.mkdtemp(prefix="jobtracker-review-tests-"))


class ReviewTests(unittest.TestCase):
    def setUp(self):
        reset()
        os.environ["OPENAI_API_KEY"] = "test"
        self._openai, openai.OpenAI = openai.OpenAI, FakeOpenAI
        self._db, self._on = config.TELEMETRY_DB, config.TELEMETRY
        config.TELEMETRY_DB, config.TELEMETRY = DB_DIR / f"{self.id().rsplit('.', 1)[-1]}.db", True
        config.TELEMETRY_DB.unlink(missing_ok=True)
        FakeOpenAI.sent = []
        self.client = TestClient(monitor.app)

    def tearDown(self):
        store.flush()
        openai.OpenAI = self._openai
        config.TELEMETRY_DB, config.TELEMETRY = self._db, self._on
        os.environ["OPENAI_API_KEY"] = ""

    def flags(self, **where):
        self.assertTrue(store.flush())
        with closing(store.connect(config.TELEMETRY_DB)) as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM flags ORDER BY id")]
        return [r for r in rows if all(r[k] == v for k, v in where.items())]

    # ---------------------------------------------------------------- chat feedback

    def test_thumbs_down_becomes_flags_and_can_be_changed(self):
        FakeOpenAI.script = [([], "Here you go.")]
        chat = registry.run("chat.send", {"message": "Hi"})
        reply = chat["messages"][-1]
        self.assertEqual(reply["turn_id"], f"{chat['id']}:0")
        self.assertIsNone(reply["feedback"])

        chat = registry.run("chat.feedback", {"chat_id": chat["id"], "turn_id": reply["turn_id"], "rating": "down",
                                              "reasons": ["misunderstood", "tone", "not-a-reason"],
                                              "comment": "I asked for Nimbus"})
        self.assertEqual(chat["messages"][-1]["feedback"]["reasons"], ["misunderstood", "tone"])
        user = self.flags(source="user")
        self.assertEqual(sorted(f["category"] for f in user), ["wrong_intent", "wrong_style"])
        self.assertTrue(all(f["status"] == "open" and "I asked for Nimbus" in f["note"] for f in user))

        registry.run("chat.feedback", {"chat_id": chat["id"], "turn_id": reply["turn_id"], "rating": "up"})
        self.assertEqual(self.flags(source="user"), [])
        chat = registry.run("chat.feedback", {"chat_id": chat["id"], "turn_id": reply["turn_id"]})
        self.assertIsNone(chat["messages"][-1]["feedback"])
        with self.assertRaises(ValueError):
            assistant.give_feedback(chat["id"], "nope:3", "up")

    def test_a_thumbs_down_without_reason_is_other(self):
        FakeOpenAI.script = [([], "Done.")]
        chat = registry.run("chat.send", {"message": "Hi"})
        registry.run("chat.feedback", {"chat_id": chat["id"], "turn_id": f"{chat['id']}:0", "rating": "down"})
        [flag] = self.flags(source="user")
        self.assertEqual((flag["category"], flag["note"]), ("other", "👎 Something else"))

    # ---------------------------------------------------------------- detectors

    def test_detectors_suggest_redundant_and_failed_calls(self):
        FakeOpenAI.script = [([call("company-list", {}, "c1"), call("company-list", {}, "c2"),
                               call("company-get", {"id": "missing"}, "c3")], ""), ([], "Done.")]
        chat = registry.run("chat.send", {"message": "List twice"})
        suggested = self.flags(source="detector", status="suggested")
        self.assertEqual(sorted(f["category"] for f in suggested), ["redundant_call", "wrong_argument"])
        self.assertTrue(all(f["turn_id"] == f"{chat['id']}:0" and f["span_id"] for f in suggested))
        self.assertTrue(all(f["prompt_version"] == assistant.prompt_version() for f in suggested))

    def test_rejected_action_is_suggested_on_the_turn_that_proposed_it(self):
        company = companies.create_company(name="Nimbus")
        FakeOpenAI.script = [([call("company-delete", {"id": company.id})], ""), ([], "Waiting for you.")]
        chat = registry.run("chat.send", {"message": "Delete Nimbus"})
        pending = next(m["tool"] for m in chat["messages"] if m["tool"] and m["tool"]["status"] == "pending")
        FakeOpenAI.script = [([], "OK, kept.")]
        registry.run("chat.resolve", {"chat_id": chat["id"], "event_id": pending["id"], "approve": False})
        [flag] = self.flags(category="wrong_intent")
        self.assertEqual(flag["turn_id"], f"{chat['id']}:0")

    def test_cost_and_stop_rules(self):
        events = [
            {"type": "turn.started", "span_id": "t", "parent_span_id": "", "llm_call_id": "", "ts_ns": 0, "payload": {},
             "turn_id": "c:0", "conversation_id": "c", "turn_index": 0},
            {"type": "llm_call.started", "span_id": "l", "parent_span_id": "t", "llm_call_id": "x", "ts_ns": 1,
             "payload": {}, "turn_id": "c:0"},
            {"type": "llm_call.ended", "span_id": "l", "parent_span_id": "t", "llm_call_id": "x", "ts_ns": 2,
             "status": "completed", "payload": {"cost_usd": 0.5}, "turn_id": "c:0"},
            {"type": "turn.ended", "span_id": "t", "parent_span_id": "", "llm_call_id": "", "ts_ns": 90 * 10**9,
             "status": "max_steps", "payload": {}, "turn_id": "c:0"},
        ]
        self.assertEqual(sorted(s["category"] for s in detect.suggest(events)),
                         ["incomplete_task", "too_costly", "too_slow"])

    # ---------------------------------------------------------------- the monitor

    def test_owner_flags_and_the_review_queue(self):
        FakeOpenAI.script = [([call("company-list", {}, "c1")], ""), ([], "Done.")]
        chat = registry.run("chat.send", {"message": "List"})
        store.flush()
        turn_id = f"{chat['id']}:0"
        turn = self.client.get(f"/api/turns/{turn_id}").json()
        tool = next(s for s in turn["spans"] if s["kind"] == "tool")

        bad = self.client.post("/api/flags", json={"turn_id": turn_id, "category": "nope"})
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(self.client.post("/api/flags", json={"turn_id": "x:9", "category": "other"}).status_code, 404)
        flag = self.client.post("/api/flags", json={"turn_id": turn_id, "category": "wrong_tool", "note": "should read the CV",
                                                    "span_id": tool["span_id"], "llm_call_id": tool["llm_call_id"]}).json()
        self.assertEqual((flag["status"], flag["source"], flag["user_id"]), ("open", "owner", ""))

        queue = self.client.get("/api/review/queue").json()
        self.assertEqual([f["id"] for f in queue["flags"]], [flag["id"]])
        self.assertEqual(queue["flags"][0]["span_label"], "tool company.list")
        self.assertIn(turn_id, queue["turns"])
        self.assertEqual(self.client.get(f"/api/turns/{turn_id}").json()["flags"][0]["category"], "wrong_tool")

        changed = self.client.patch(f"/api/flags/{flag['id']}", json={"status": "fixed", "fixed_in": "abc123"}).json()
        self.assertEqual((changed["status"], changed["fixed_in"]), ("fixed", "abc123"))
        self.assertEqual(self.client.get("/api/review/queue").json()["flags"], [])
        self.assertEqual(self.client.patch(f"/api/flags/{flag['id']}", json={"status": "gone"}).status_code, 422)
        self.assertEqual(self.client.patch("/api/flags/999", json={"status": "open"}).status_code, 404)
        overview = self.client.get("/api/overview?user=*").json()
        self.assertEqual(overview["review"]["flags"], {"fixed": 1})
        self.assertEqual(len(self.client.get("/api/flags/catalog").json()["categories"]), 18)

    def test_old_database_gets_the_new_tables_and_columns(self):
        store.connect(config.TELEMETRY_DB).close()
        with closing(sqlite3.connect(config.TELEMETRY_DB)) as conn:  # as a database from before flags existed
            conn.execute("ALTER TABLE turns DROP COLUMN prompt_version")
            conn.execute("DROP TABLE flags")
            conn.commit()
        self.assertEqual(self.client.get("/api/flags").json(), [])
        with closing(sqlite3.connect(config.TELEMETRY_DB)) as conn:
            columns = {r[1] for r in conn.execute("PRAGMA table_info(turns)")}
        self.assertIn("prompt_version", columns)


if __name__ == "__main__":
    unittest.main()
