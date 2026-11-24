"""Chat telemetry: the events a turn produces, their links (turn, span, LLM call), and the latency math."""
import json
import lzma
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

import openai

from jobtracker import config, registry
from jobtracker.services import assistant, claude, companies
from jobtracker.telemetry import backup, metrics, store
from tests.test_assistant import FakeOpenAI, call
from tests.test_core import reset  # sets the temp data/output env vars first
from tests.test_fallback import QUOTA, FakeClaude, RaisingOpenAI, text

DB_DIR = Path(tempfile.mkdtemp(prefix="jobtracker-telemetry-tests-"))


def slow(ms: int = 150, label: str = ""):
    """A tool that takes a while (registered only during the parallel test)."""
    time.sleep(ms / 1000)
    return {"slept": ms, "label": label}


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        reset()
        os.environ["OPENAI_API_KEY"] = "test"
        self._openai, openai.OpenAI = openai.OpenAI, RaisingOpenAI
        self._chat = claude.chat
        self._db, self._on = config.TELEMETRY_DB, config.TELEMETRY
        config.TELEMETRY_DB, config.TELEMETRY = DB_DIR / f"{self.id().rsplit('.', 1)[-1]}.db", True
        config.TELEMETRY_DB.unlink(missing_ok=True)
        FakeOpenAI.sent, FakeClaude.sent = [], []

    def tearDown(self):
        store.flush()
        openai.OpenAI, claude.chat = self._openai, self._chat
        config.TELEMETRY_DB, config.TELEMETRY = self._db, self._on
        os.environ["OPENAI_API_KEY"] = os.environ["ANTHROPIC_API_KEY"] = ""

    def events(self, turn_id=None):
        self.assertTrue(store.flush())
        sql, args = ("SELECT * FROM events WHERE turn_id = ? ORDER BY seq", (turn_id,)) if turn_id \
            else ("SELECT * FROM events ORDER BY seq", ())
        with closing(store.connect(config.TELEMETRY_DB)) as conn:
            return [store.row_to_event(r) for r in conn.execute(sql, args)]

    def turn_row(self, turn_id):
        store.flush()
        with closing(store.connect(config.TELEMETRY_DB)) as conn:
            row = conn.execute("SELECT * FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
        return dict(row) if row else None

    def test_plain_answer(self):
        FakeOpenAI.script = [([], "Hello there, how can I help?")]
        chat = registry.run("chat.send", {"message": "Hi"})
        events = self.events()
        self.assertEqual([e["type"] for e in events], [
            "turn.started", "conversation.started", "history.started", "history.ended", "llm_call.started",
            "llm_call.first_token", "llm.text", "llm_call.ended", "turn.ended"])
        self.assertTrue(all(e["turn_id"] == f"{chat['id']}:0" and e["conversation_id"] == chat["id"] for e in events))
        call_id = events[4]["llm_call_id"]
        self.assertTrue(call_id)
        self.assertEqual({e["llm_call_id"] for e in events[4:8]}, {call_id})
        self.assertEqual(events[6]["payload"]["text"], "Hello there, how can I help?")
        self.assertEqual(events[6]["payload"]["deltas"], 7)
        turn = events[0]["span_id"]
        self.assertEqual({e["parent_span_id"] for e in events
                          if e["type"] in ("history.started", "llm_call.started")}, {turn})
        self.assertTrue(all(b["ts_ns"] >= a["ts_ns"] for a, b in zip(events, events[1:], strict=False)))
        row = self.turn_row(f"{chat['id']}:0")
        self.assertEqual((row["status"], row["trigger"], row["llm_calls"], row["consistent"]),
                         ("completed", "user_message", 1, 1))
        self.assertIsNotNone(row["ttft_ms"])

    def test_second_message_is_the_next_turn(self):
        FakeOpenAI.script = [([], "A."), ([], "B.")]
        chat = registry.run("chat.send", {"message": "one"})
        registry.run("chat.send", {"message": "two", "chat_id": chat["id"]})
        events = self.events()
        self.assertEqual(sorted({e["turn_index"] for e in events}), [0, 1])
        self.assertEqual(sum(e["type"] == "conversation.started" for e in events), 1)

    def test_parallel_tools_overlap_and_keep_the_call_order(self):
        registry.action("test.slow")(slow)
        try:
            FakeOpenAI.script = [([call("test-slow", {"ms": 200, "label": x}, call_id=f"c{x}") for x in "abc"], ""),
                                 ([], "Done.")]
            chat = registry.run("chat.send", {"message": "Run three"})
        finally:
            del registry.ACTIONS["test.slow"]
        self.assertEqual([m["tool"]["args"]["label"] for m in chat["messages"] if m["role"] == "tool"], ["a", "b", "c"])
        self.assertEqual([i["call_id"] for i in FakeOpenAI.sent[1]["input"]], ["ca", "cb", "cc"])

        events = self.events()
        spans = metrics.spans(events)
        tools = [s for s in spans.values() if s.kind == "tool"]
        self.assertEqual(len(tools), 3)
        self.assertLess(max(s.start_ns for s in tools), min(s.end_ns for s in tools))  # they overlapped
        first_call = next(e for e in events if e["type"] == "llm_call.started")
        self.assertEqual({s.llm_call_id for s in tools}, {first_call["llm_call_id"]})
        self.assertEqual(sum(e["type"] == "tool_call.requested" for e in events), 3)
        step = metrics.steps(spans)[0]
        self.assertGreater(step["parallelism"], 2)
        self.assertLess(step["tool_wall_ms"], step["tool_sum_ms"] / 2)
        row = self.turn_row(f"{chat['id']}:0")
        self.assertEqual((row["tool_calls"], row["consistent"]), (3, 1))
        self.assertLess(row["tool_wall_ms"], 450)  # ~200 ms of wall time, not 600

    def test_tool_error_and_result_size(self):
        FakeOpenAI.script = [([call("company-get", {"id": "nope"})], ""), ([], "Not found.")]
        registry.run("chat.send", {"message": "Open nope"})
        ended = next(e for e in self.events() if e["type"] == "tool.ended")
        self.assertEqual(ended["status"], "error")
        self.assertIn("NotFound", ended["payload"]["error"])
        self.assertGreater(ended["payload"]["result_chars"], 0)
        self.assertTrue(ended["payload"]["tokens_estimated"])

    def test_approval_links_both_turns(self):
        target = companies.list_companies()[0]
        FakeOpenAI.script = [([call("company-delete", {"id": target.id})], ""), ([], "Please approve.")]
        chat = registry.run("chat.send", {"message": "Delete it"})
        event = next(m["tool"] for m in chat["messages"] if m["role"] == "tool")
        first = self.events(f"{chat['id']}:0")
        requested = next(e for e in first if e["type"] == "approval.requested")
        self.assertEqual(requested["payload"]["event_id"], event["id"])
        self.assertEqual(next(e for e in first if e["type"] == "tool.ended")["status"], "pending_approval")
        self.assertEqual(self.turn_row(f"{chat['id']}:0")["status"], "waiting_approval")

        FakeOpenAI.script = [([], "Deleted.")]
        registry.run("chat.resolve", {"chat_id": chat["id"], "event_id": event["id"], "approve": True})
        second = self.events(f"{chat['id']}:1")
        resolved = next(e for e in second if e["type"] == "approval.resolved")
        self.assertEqual(resolved["payload"]["requested_turn_id"], f"{chat['id']}:0")
        self.assertTrue(resolved["payload"]["approved"])
        tool = next(e for e in second if e["type"] == "tool.ended")
        self.assertEqual((tool["status"], tool["payload"]["event_id"] if "event_id" in tool["payload"] else event["id"]),
                         ("done", event["id"]))
        self.assertEqual(self.turn_row(f"{chat['id']}:1")["trigger"], "approval")

    def test_fallback_to_claude(self):
        os.environ["ANTHROPIC_API_KEY"] = "test"
        claude.chat = FakeClaude.chat
        FakeOpenAI.script = [QUOTA]
        FakeClaude.script = [([text("Hi from Claude.")], "end_turn")]
        registry.run("chat.send", {"message": "Hi"})
        events = self.events()
        calls = [e for e in events if e["type"] == "llm_call.ended"]
        self.assertEqual([c["status"] for c in calls], ["error", "completed"])
        self.assertEqual(next(e for e in events if e["type"] == "provider.fallback")["payload"]["to_provider"], "claude")
        started = [e["payload"]["provider"] for e in events if e["type"] == "llm_call.started"]
        self.assertEqual(started, ["openai", "anthropic"])
        self.assertIn("error", [e["type"] for e in events])
        self.assertEqual(next(e for e in events if e["type"] == "history.ended" and
                              e["payload"].get("chars") is not None)["payload"]["messages"], 1)

    def test_max_steps(self):
        original, assistant.MAX_STEPS = assistant.MAX_STEPS, 2
        try:
            FakeOpenAI.script = [([call("company-list", {})], ""), ([call("company-list", {})], "")]
            chat = registry.run("chat.send", {"message": "Loop"})
        finally:
            assistant.MAX_STEPS = original
        self.assertEqual(self.turn_row(f"{chat['id']}:0")["status"], "max_steps")

    def test_titles(self):
        companies.create_company(name="Nimbus")
        FakeOpenAI.script = [([call("chat-set_title", {"title": "Plans"})], ""), ([], "Ok.")]
        chat = registry.run("chat.send", {"message": "Hello"})
        registry.run("chat.rename", {"id": chat["id"], "title": "Mine"})
        FakeOpenAI.script = [([], "Sure.")]
        assistant.get_chat(chat["id"])
        titles = [(e["payload"]["by"], e["payload"]["new"], e["turn_id"]) for e in self.events() if e["type"] == "title.set"]
        self.assertEqual(titles, [("model", "Plans", f"{chat['id']}:0"), ("user", "Mine", "")])

    def test_provider_crash_ends_the_turn_with_an_error(self):
        FakeOpenAI.script = [RuntimeError("network gone")]
        with self.assertRaises(RuntimeError):
            registry.run("chat.send", {"message": "Hi"})
        events = self.events()
        self.assertEqual(events[-1]["type"], "turn.ended")
        self.assertEqual(events[-1]["status"], "error")
        errors = [e for e in events if e["type"] == "error"]
        self.assertTrue(errors and errors[0]["payload"]["message"] == "network gone")

    def test_storage_failure_never_breaks_the_chat(self):
        config.TELEMETRY_DB = DB_DIR / "a-folder"
        config.TELEMETRY_DB.mkdir(exist_ok=True)
        FakeOpenAI.script = [([], "Still fine.")]
        chat = registry.run("chat.send", {"message": "Hi"})
        self.assertEqual(chat["messages"][-1]["text"], "Still fine.")
        self.assertTrue(store.flush())

    def test_no_copy_in_the_data_folder_and_backup_only_when_asked(self):
        FakeOpenAI.script = [([], "One."), ([], "Two.")]
        registry.run("chat.send", {"message": "Hi"})
        store.flush()
        self.assertEqual(list(config.DATA_DIR.glob("telemetry*")), [])  # often a synced folder: never there
        self.assertIsNone(config.TELEMETRY_BACKUP_DIR)
        folder = DB_DIR / f"backup-{self.id().rsplit('.', 1)[-1]}"
        original, config.TELEMETRY_BACKUP_DIR = config.TELEMETRY_BACKUP_DIR, folder
        backup._next_check = 0
        try:
            registry.run("chat.send", {"message": "Again"})  # the writer starts the first backup in the background
            store.flush()
            backup.wait()
        finally:
            config.TELEMETRY_BACKUP_DIR = original
        [made] = backup.backups(folder)
        self.assertFalse(backup.due(folder))  # the next one in a week
        self.assertTrue(backup.due(folder, now=made.stat().st_mtime + 7 * 86400))
        self.assertEqual([p.name for p in folder.iterdir()], [made.name])  # no temporary file left
        restored = folder / "restored.db"
        with lzma.open(made) as src:
            restored.write_bytes(src.read())
        with closing(sqlite3.connect(restored)) as conn:
            self.assertGreater(conn.execute("SELECT count(*) FROM events").fetchone()[0], 0)

    def test_backups_keep_only_the_last_three(self):
        folder = DB_DIR / "rotation"
        folder.mkdir(exist_ok=True)
        for day in ("2026-01-01", "2026-01-08", "2026-01-15"):
            (folder / f"{backup.PREFIX}{day}_030000{backup.SUFFIX}").write_bytes(b"old")
        store.flush()
        store.connect(config.TELEMETRY_DB).close()  # an (empty) database to copy
        newest = backup.make(folder=folder)
        self.assertEqual([p.name for p in backup.backups(folder)],
                         [f"{backup.PREFIX}2026-01-08_030000{backup.SUFFIX}",
                          f"{backup.PREFIX}2026-01-15_030000{backup.SUFFIX}", newest.name])

    def test_live_listener_gets_the_tokens(self):
        FakeOpenAI.script = [([], "Streaming works.")]
        got = []
        assistant.send("Hi", listener=got.append)
        deltas = "".join(e["text"] for e in got if e["type"] == "live.delta")
        self.assertEqual(deltas, "Streaming works.")
        self.assertIn("turn.ended", [e["type"] for e in got])

    def test_listener_works_with_telemetry_off(self):
        config.TELEMETRY = False
        FakeOpenAI.script = [([], "Hi.")]
        got = []
        assistant.send("Hi", listener=got.append)
        self.assertIn("live.delta", [e["type"] for e in got])
        self.assertFalse(config.TELEMETRY_DB.exists())


class ChatStreamTests(unittest.TestCase):
    """POST /api/chat/stream: the turn as server-sent events, ending with the whole chat."""

    def setUp(self):
        reset()
        os.environ["OPENAI_API_KEY"] = "test"
        self._openai, openai.OpenAI = openai.OpenAI, FakeOpenAI
        FakeOpenAI.sent = []

    def tearDown(self):
        openai.OpenAI = self._openai
        os.environ["OPENAI_API_KEY"] = ""

    def stream(self, payload):
        from fastapi.testclient import TestClient

        from jobtracker.api import app
        with TestClient(app).stream("POST", "/api/chat/stream", json=payload) as response:
            self.assertEqual(response.headers["content-type"].split(";")[0], "text/event-stream")
            body = "".join(response.iter_text())
        return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]

    def test_streams_tokens_tools_then_the_chat(self):
        FakeOpenAI.script = [([call("company-list", {})], ""), ([], "You have companies.")]
        events = self.stream({"message": "List them"})
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "step")
        self.assertLess(types.index("tool.started"), types.index("tool.ended"))
        self.assertEqual("".join(e["text"] for e in events if e["type"] == "delta"), "You have companies.")
        self.assertEqual(events[-1]["type"], "chat")
        chat = events[-1]["chat"]
        self.assertEqual([m["role"] for m in chat["messages"]], ["user", "tool", "assistant"])

        # approvals stream too
        target = companies.list_companies()[0]
        FakeOpenAI.script = [([call("company-delete", {"id": target.id})], ""), ([], "Approve?")]
        chat = self.stream({"message": "Delete", "chat_id": chat["id"]})[-1]["chat"]
        event = next(m["tool"] for m in chat["messages"] if m["tool"] and m["tool"]["status"] == "pending")
        FakeOpenAI.script = [([], "Kept.")]
        events = self.stream({"chat_id": chat["id"], "event_id": event["id"], "approve": False})
        self.assertEqual(events[-1]["chat"]["messages"][-1]["text"], "Kept.")

    def test_errors_arrive_as_an_event(self):
        events = self.stream({"chat_id": "nope", "event_id": "x", "approve": True})
        self.assertEqual(events, [{"type": "error", "status": 404, "detail": "Chat 'nope' not found"}])


def ev(type_, span, ts_ms, parent="", llm="", status=""):
    return {"type": type_, "span_id": span, "parent_span_id": parent, "llm_call_id": llm, "status": status,
            "ts_ns": int(ts_ms * 1e6), "payload": {}, "turn_id": "c:0", "conversation_id": "c", "turn_index": 0}


class MetricsTests(unittest.TestCase):
    def test_union_counts_overlap_once(self):
        ms = 1_000_000
        self.assertEqual(metrics.union_ms([(0, 100 * ms), (50 * ms, 150 * ms), (200 * ms, 210 * ms)]), 160)
        self.assertEqual(metrics.union_ms([]), 0)

    def timeline(self, tool_b_end=260):
        return [ev("turn.started", "t", 0), ev("llm_call.started", "l", 10, "t", "L"),
                ev("tool_call.requested", "l", 90, "t", "L"), ev("tool_call.requested", "l", 95, "t", "L"),
                ev("llm_call.ended", "l", 100, "t", "L"),
                ev("tool.started", "a", 110, "t", "L"), ev("tool.started", "b", 112, "t", "L"),
                ev("tool.ended", "a", 250, "t", "L"), ev("tool.ended", "b", tool_b_end, "t", "L"),
                ev("turn.ended", "t", 300, status="completed")]

    def test_parallel_tools_are_not_summed_as_wall_time(self):
        summary = metrics.turn_summary(self.timeline())
        self.assertEqual(summary["tool_sum_ms"], 140 + 148)
        self.assertEqual(summary["tool_wall_ms"], 150)
        self.assertEqual(summary["wall_ms"], 300)
        self.assertTrue(summary["consistent"])
        self.assertAlmostEqual(summary["steps"][0]["parallelism"], 1.92)

    def test_inconsistent_timestamps_are_flagged(self):
        summary = metrics.turn_summary(self.timeline(tool_b_end=400))  # a tool ending after its turn
        self.assertFalse(summary["consistent"])
        failed = [c["check"] for c in summary["checks"] if not c["ok"]]
        self.assertIn("children inside their parent", failed)


if __name__ == "__main__":
    unittest.main()
