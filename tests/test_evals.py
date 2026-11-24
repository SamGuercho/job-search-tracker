"""The fixing process: eval cases on the server (monitor API), drafting from a flagged turn, replaying a case,
code checks and verdicts."""
import json
import os
import tempfile
import unittest
from pathlib import Path

import openai
from fastapi.testclient import TestClient

from jobtracker import actions, config, registry, storage  # noqa: F401  (actions: registers them)
from jobtracker.evals import checks, fixture, runner, worker
from jobtracker.telemetry import store
from monitor import app as monitor
from tests.test_assistant import FakeOpenAI, call
from tests.test_core import reset  # sets the temp data/output env vars first

DB_DIR = Path(tempfile.mkdtemp(prefix="jobtracker-evals-tests-"))


class EvalTests(unittest.TestCase):
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

    # ---------------------------------------------------------------- checks

    def test_checks(self):
        result = {"status": "completed", "reply": "Nimbus is next.", "cost_usd": 0.01, "tool_calls": [
            {"action": "company.get", "args": {"id": "Nimbus"}, "status": "done"},
            {"action": "company.get", "args": {"id": "Nimbus"}, "status": "error", "error": "boom",
             "result": '{"title": "Staff Data Scientist"}'}]}
        got = [c["ok"] for c in checks.evaluate([
            {"type": "status"}, {"type": "called", "action": "company.get", "args": {"id": "nimbus"}, "max": 1},
            {"type": "not_called", "action": "company.delete"}, {"type": "no_duplicate_calls"}, {"type": "no_tool_errors"},
            {"type": "max_cost_usd", "value": 0.05}, {"type": "reply_contains", "text": "NIMBUS"},
            {"type": "reply_not_contains", "text": "sorry"}, {"type": "reply_matches", "pattern": r"next\.$"},
            {"type": "max_reply_chars", "value": 5}, {"type": "max_tool_calls", "value": 2}, {"type": "nope"},
            {"type": "max_cost_usd"},
            {"type": "tool_result_contains", "action": "company.get", "text": "Staff"},
            {"type": "tool_result_not_contains", "action": "company.get", "text": "Staff"},
            {"type": "tool_result_contains", "action": "cv.generate", "text": "Staff"}], result)]
        self.assertEqual(got, [True, False, True, False, False, True, True, True, True, False, True, False, False,
                               True, False, False])
        graded = {"checks": [{"ok": True}], "rubric": [{"verdict": "yes"}, {"verdict": None}]}
        self.assertEqual(runner.verdict(graded), "pending")
        graded["rubric"][1]["verdict"] = "no"
        self.assertEqual(runner.verdict(graded), "fail")
        graded["rubric"][1]["verdict"] = "yes"
        self.assertEqual(runner.verdict(graded), "pass")

    # ---------------------------------------------------------------- cases on the server

    def test_cases_api(self):
        FakeOpenAI.script = [([], "Hi.")]
        chat = registry.run("chat.send", {"message": "Hi"})
        store.flush()
        flag = self.client.post("/api/flags", json={"turn_id": f"{chat['id']}:0", "category": "wrong_style"}).json()
        case = {"id": "case-1", "title": "Too formal", "flag_ids": [flag["id"]], "snapshot": {"message": "Hi"},
                "checks": [{"type": "status"}], "rubric": ["Is it casual?"]}
        self.assertEqual(self.client.post("/api/cases", json={"title": "no id"}).status_code, 422)
        saved = self.client.post("/api/cases", json=case).json()
        self.assertEqual((saved["status"], saved["rubric"]), ("draft", ["Is it casual?"]))
        flags = self.client.get("/api/flags?status=*").json()
        self.assertEqual((flags[0]["status"], flags[0]["case_id"]), ("in_case", "case-1"))
        self.client.post("/api/cases", json={**case, "title": "Too formal, again"})
        frozen = self.client.post("/api/cases", json={**case, "status": "frozen"}).json()
        self.assertEqual((frozen["status"], frozen["title"]), ("frozen", "Too formal"))
        self.assertEqual(self.client.post("/api/cases", json=case).status_code, 409)  # frozen: can't change
        self.assertEqual(self.client.patch("/api/cases/case-1", json={"status": "draft"}).status_code, 409)
        self.assertEqual([c["id"] for c in self.client.get("/api/cases?status=frozen").json()], ["case-1"])
        self.assertEqual(self.client.patch("/api/cases/case-1", json={"status": "archived"}).json()["status"], "archived")
        self.assertEqual(self.client.get("/api/cases/nope").status_code, 404)

    def test_data_export_leaves_out_chats_and_secrets(self):
        self.assertTrue(set(fixture.FIXTURE_FILES) <= set(storage.REGISTERED))
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        (config.DATA_DIR / "companies.json").write_text('{"companies": []}', encoding="utf-8")
        (config.DATA_DIR / "push.json").write_text('{"secret": 1}', encoding="utf-8")
        data = self.client.get("/api/review/data").json()
        self.assertEqual(data, {"companies.json": {"companies": []}})

    # ---------------------------------------------------------------- draft and replay

    def test_draft_from_a_flagged_turn_then_replay_it(self):
        FakeOpenAI.script = [([], "Hello Sam.")]
        chat = registry.run("chat.send", {"message": "Hello"})
        FakeOpenAI.script = [([call("job-fetch_url", {"url": "https://jobs.example/1"})], ""), ([], "Nimbus, Data Lead.")]
        registry.run("chat.send", {"message": "Add https://jobs.example/1", "chat_id": chat["id"]})
        store.flush()
        flag = self.client.post("/api/flags", json={"turn_id": f"{chat['id']}:1", "category": "incomplete_task",
                                                    "note": "didn't create the application"}).json()

        def api(method, path, body=None):
            return self.client.request(method, path, json=body).json()
        cases_dir = DB_DIR / f"cases-{self.id().rsplit('.', 1)[-1]}"
        saved = runner.api, runner.CASES
        runner.api, runner.CASES = api, cases_dir
        try:
            runner.main(["draft", "--flag", str(flag["id"])])
        finally:
            runner.api, runner.CASES = saved
        case = json.loads(next(cases_dir.glob("*.json")).read_text(encoding="utf-8"))
        snap = case["snapshot"]
        self.assertEqual(case["id"], f"case-{flag['id']}-incomplete-task")
        self.assertEqual((snap["trigger"], snap["message"]), ("user_message", "Add https://jobs.example/1"))
        self.assertEqual(snap["history"], [{"role": "user", "text": "Hello"}, {"role": "assistant", "text": "Hello Sam."}])
        self.assertEqual([r["action"] for r in snap["recorded"]], ["job.fetch_url"])
        self.assertEqual(snap["source"]["reply"], "Nimbus, Data Lead.")
        self.assertEqual(snap["source"]["flags"][0]["note"], "didn't create the application")

        # replayed: the whole history goes to the model, the outside call answers what was recorded
        snap["recorded"] = [{"action": "job.fetch_url", "args": {"url": "https://jobs.example/1"},
                             "result": {"text": "Nimbus is hiring"}}]
        case["checks"] = [{"type": "called", "action": "job.fetch_url"}, {"type": "called", "action": "company.create"}]
        case["rubric"] = ["Did it create the application?"]
        FakeOpenAI.script = [([call("job-fetch_url", {"url": "https://jobs.example/1"})], ""), ([], "Fetched.")]
        original = registry.ACTIONS["job.fetch_url"].fn
        FakeOpenAI.sent = []
        result = worker.run(case)
        self.assertIs(registry.ACTIONS["job.fetch_url"].fn, original)  # put back after the run
        sent = FakeOpenAI.sent[0]["input"]
        self.assertEqual([m["content"] for m in sent], ["Hello", "Hello Sam.", "Add https://jobs.example/1"])
        self.assertIn("Nimbus is hiring", FakeOpenAI.sent[1]["input"][0]["output"])
        self.assertEqual((result["status"], result["reply"]), ("completed", "Fetched."))
        self.assertEqual([c["ok"] for c in result["checks"]], [True, False])
        self.assertEqual(result["rubric"], [{"question": "Did it create the application?", "verdict": None, "reason": ""}])
        self.assertEqual(runner.verdict(result), "fail")

    def test_a_case_runs_in_its_own_process_and_folder(self):
        os.environ["OPENAI_API_KEY"] = ""  # no AI here: the run stops at the first call, after setting up
        work = DB_DIR / "work"
        case_file = DB_DIR / "isolated.json"
        case_file.write_text(json.dumps({"id": "iso", "snapshot": {"message": "Hi", "fixture": {
            "companies.json": {"companies": []}, "../escape.json": {}}}}), encoding="utf-8")
        result = runner._run_one(case_file, 1, work)
        self.assertIn("LLMUnavailable", result["error"])
        self.assertTrue((work / "iso-1" / "data" / "companies.json").exists())
        self.assertFalse((work / "iso-1" / "escape.json").exists())
        self.assertFalse((config.DATA_DIR / "companies.json").exists())  # the test's own data folder: untouched

    def test_run_needs_an_agreed_cost(self):
        cases_dir = DB_DIR / "cost"
        cases_dir.mkdir(exist_ok=True)
        (cases_dir / "a.json").write_text(json.dumps({"id": "a", "status": "frozen", "snapshot": {
            "message": "Hi", "source": {"cost_usd": 0.03}}}), encoding="utf-8")
        saved, runner.CASES = runner.CASES, cases_dir
        try:
            with self.assertRaises(SystemExit) as stop:
                runner.main(["run", "--label", "baseline", "--repeat", "2"])
        finally:
            runner.CASES = saved
        self.assertIn("--yes", str(stop.exception))


if __name__ == "__main__":
    unittest.main()
