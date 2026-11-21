"""Interview prep: brief, validation, storage, premium gate, and the Codex runner with a fake `codex`."""
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import ValidationError

from jobtracker import config, registry, users
from jobtracker.api import app
from jobtracker.models import InterviewPrep
from jobtracker.services import companies, prep
from tests.test_core import reset  # sets the temp data/output env vars first
from tests.test_users import FRIEND, OWNER, set_cv_email

EXERCISE = {"title": "Top customers by revenue", "kind": "SQL", "difficulty": "medium", "topic": "window functions",
            "why": "Their product is analytics", "prompt": "Write a query…", "solution": "```sql\nSELECT 1\n```",
            "rubric": [{"point": "Uses a window function", "points": 6}, {"point": "Handles ties", "points": 4}],
            "strong_answer": "Clear, correct, explains ties", "common_mistakes": ["Forgets ties"]}
RESULT = {"company_summary": "Nimbus builds analytics.", "interview_process": "HR, technical, onsite",
          "focus_topics": ["SQL", "A/B tests"], "candidate_angle": "Strong SQL", "exercises": [EXERCISE],
          "questions_to_ask": ["How is the team organised?"], "sources": ["https://nimbus.example"]}

FAKE_CODEX = """#!{python}
import json, sys, pathlib
args = sys.argv[1:]
pathlib.Path({log!r}).write_text(json.dumps(args))
if args[:2] == ["login", "status"]:
    print("Logged in using ChatGPT"); sys.exit(0)
if {fail}:
    print("boom", file=sys.stderr); sys.exit(3)
prompt = sys.stdin.read()
assert "Nimbus" in prompt and "Candidate's CV" in prompt
out = args[args.index("--output-last-message") + 1]
pathlib.Path(out).write_text({answer!r})
"""


def fake_codex(tmp: Path, fail=False, answer=None) -> Path:
    path, log = tmp / "codex", tmp / "codex-args.json"
    path.write_text(FAKE_CODEX.format(python=sys.executable, log=str(log), fail=fail,
                                      answer=json.dumps(answer or RESULT)))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def wait_done(company_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        p = prep.get(company_id)
        if p and p.status != "running":
            return p
        time.sleep(0.05)
    raise AssertionError("prep still running")


class PrepTests(unittest.TestCase):
    def setUp(self):
        reset()
        self.tmp = Path(tempfile.mkdtemp())
        self._bin = config.CODEX_BIN
        self.company = companies.create_company(name="Nimbus", position="Data Analyst", website="https://nimbus.example",
                                                job_description="SQL and experimentation.")

    def tearDown(self):
        config.CODEX_BIN = self._bin
        config.TAILSCALE_USERS, config.OWNER, config.PREMIUM = False, "", set()

    def test_schema_is_strict_and_keeps_every_field(self):
        schema = prep.strict_schema()
        exercise = schema["$defs"]["Exercise"]
        self.assertIn("title", exercise["properties"])
        self.assertEqual(set(exercise["required"]), set(exercise["properties"]))
        self.assertFalse(exercise["additionalProperties"])
        self.assertFalse(schema["additionalProperties"])

    def test_brief_has_instructions_context_and_schema(self):
        b = registry.run("prep.brief", {"company": "Nimbus", "exercises": 3})
        self.assertIn("3 exercises", b["instructions"])
        self.assertIn("https://nimbus.example", b["instructions"])
        self.assertIn("SQL and experimentation.", b["context"])
        self.assertIn("Candidate's CV", b["context"])
        self.assertIn("exercises", b["output_schema"]["properties"])

    def test_save_validates_stores_and_writes_the_file(self):
        saved = registry.run("prep.save", {"company": "Nimbus", "result": RESULT})
        self.assertEqual(saved["status"], "done")
        text = (users.output_dir() / saved["file"]).read_text(encoding="utf-8")
        self.assertIn("Top customers by revenue", text)
        self.assertIn("Uses a window function (6)", text)
        self.assertEqual(registry.run("prep.get", {"company": self.company.id})["result"]["focus_topics"], ["SQL", "A/B tests"])
        with self.assertRaises(ValidationError):
            registry.run("prep.save", {"company": "Nimbus", "result": {"company_summary": "incomplete"}})

    def test_button_runs_codex_in_the_background(self):
        config.CODEX_BIN = str(fake_codex(self.tmp))
        started = registry.run("prep.start", {"company": "Nimbus"})
        self.assertEqual(started["status"], "running")
        done = wait_done(self.company.id)
        self.assertEqual(done.status, "done", done.error)
        self.assertEqual(done.result.exercises[0].title, "Top customers by revenue")
        args = json.loads((self.tmp / "codex-args.json").read_text())
        self.assertEqual(args[:2], ["--search", "exec"])
        self.assertIn(config.PREP_MODEL, args)
        self.assertIn("--output-schema", args)

    def test_codex_failure_is_recorded(self):
        config.CODEX_BIN = str(fake_codex(self.tmp, fail=True))
        registry.run("prep.start", {"company": "Nimbus"})
        done = wait_done(self.company.id)
        self.assertEqual(done.status, "error")
        self.assertIn("boom", done.error)

    def test_codex_missing_or_signed_out(self):
        config.CODEX_BIN = str(self.tmp / "nope")
        self.assertFalse(registry.run("prep.status", {})["available"])
        with self.assertRaises(prep.PrepUnavailable):
            registry.run("prep.start", {"company": "Nimbus"})

    def test_interrupted_prep_is_reported(self):
        prep._store(InterviewPrep(company_id=self.company.id, status="running", requested_at="2020-01-01T00:00:00"))
        self.assertEqual(prep.get(self.company.id).status, "error")


class PremiumTests(unittest.TestCase):
    def setUp(self):
        reset()
        set_cv_email(OWNER)
        config.TAILSCALE_USERS, config.PREMIUM = True, {OWNER}
        self.client = TestClient(app)
        self.tmp = Path(tempfile.mkdtemp())
        self._bin, config.CODEX_BIN = config.CODEX_BIN, str(fake_codex(self.tmp))

    def tearDown(self):
        config.CODEX_BIN = self._bin
        config.TAILSCALE_USERS, config.OWNER, config.PREMIUM = False, "", set()

    def act(self, login, action, payload=None):
        return self.client.post(f"/api/actions/{action}", json=payload or {}, headers={"Tailscale-User-Login": login})

    def test_only_premium_logins_get_the_button(self):
        self.assertTrue(self.act(OWNER, "app.info").json()["result"]["premium"])
        self.assertFalse(self.act(FRIEND, "app.info").json()["result"]["premium"])
        self.act(FRIEND, "company.create", {"name": "Nimbus"})
        refused = self.act(FRIEND, "prep.start", {"company": "Nimbus"})
        self.assertEqual(refused.status_code, 403)
        self.assertFalse(self.act(FRIEND, "prep.status").json()["result"]["available"])

    def test_premium_prep_lands_in_the_requesters_data(self):
        config.PREMIUM = {OWNER, FRIEND}
        created = self.act(FRIEND, "company.create", {"name": "Nimbus"}).json()["result"]
        self.assertEqual(self.act(FRIEND, "prep.start", {"company": "Nimbus"}).status_code, 200)
        with users.acting_as(FRIEND):
            self.assertEqual(wait_done(created["id"]).status, "done")
        self.assertIsNotNone(self.act(FRIEND, "prep.get", {"company": "Nimbus"}).json()["result"])
        self.assertTrue(any(config.USERS_DIR.rglob("Interview_Prep.md")))  # in the friend's own folder


if __name__ == "__main__":
    os.environ.setdefault("JOBTRACKER_NO_DOTENV", "1")
    unittest.main()
