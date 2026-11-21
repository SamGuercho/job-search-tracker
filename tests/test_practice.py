"""Practice log, training profile, prep versions, and the history fed into new preps."""
import unittest

from fastapi.testclient import TestClient

from jobtracker import config, registry
from jobtracker.api import app
from jobtracker.models import InterviewPrep
from jobtracker.services import companies, prep
from tests.test_core import reset  # sets the temp data/output env vars first
from tests.test_prep import EXERCISE, RESULT
from tests.test_users import FRIEND, OWNER, set_cv_email

SECOND = dict(EXERCISE, title="Design a metrics pipeline", kind="system design", topic="data pipelines",
              difficulty="hard")


class PracticeTests(unittest.TestCase):
    def setUp(self):
        reset()
        self.company = companies.create_company(name="Nimbus", position="Data Analyst")
        prep.save(self.company.id, dict(RESULT, exercises=[EXERCISE, SECOND]), "test")

    def record(self, exercise, score, **kw):
        return registry.run("practice.record", {"company": "Nimbus", "exercise": exercise, "score": score, **kw})

    def test_record_finds_the_exercise_by_number_or_title(self):
        a = self.record("2", 4, missed=["No partitioning", ""], feedback="Think about late events")
        self.assertEqual((a["exercise"], a["topic"], a["kind"]), ("Design a metrics pipeline", "data pipelines", "system design"))
        self.assertEqual(a["missed"], ["No partitioning"])
        b = self.record("top customers", 12)
        self.assertEqual((b["exercise"], b["score"]), ("Top customers by revenue", 10))  # partial title, clamped
        c = self.record("Explain p-values", 5, topic="statistics", kind="theory")  # not in the prep
        self.assertEqual((c["exercise"], c["topic"]), ("Explain p-values", "statistics"))

    def test_profile_says_what_to_review(self):
        self.record("1", 3, missed=["Ties"])
        self.record("1", 8)
        self.record("2", 5, missed=["Backfills"])
        p = registry.run("practice.profile", {})
        self.assertEqual(p["totals"], {"attempts": 3, "exercises": 2, "average": 5.3})
        self.assertEqual([e["exercise"] for e in p["to_review"]], ["Design a metrics pipeline"])  # last score of #1 is 8
        sql = next(t for t in p["topics"] if t["topic"] == "window functions")
        self.assertEqual((sql["average"], sql["trend"]), (5.5, 5))
        self.assertEqual([t["topic"] for t in p["weak_topics"]], ["data pipelines", "window functions"])
        self.assertEqual(len(registry.run("practice.history", {"company": "Nimbus"})), 3)

    def test_next_prep_brief_includes_the_history(self):
        self.assertNotIn("practice history", registry.run("prep.brief", {"company": "Nimbus"})["context"])
        self.record("2", 4, missed=["Backfills"])
        context = registry.run("prep.brief", {"company": "Nimbus"})["context"]
        self.assertIn("do not repeat", context)
        self.assertIn("Design a metrics pipeline", context)
        self.assertIn("Backfills", context)


class PrepVersionTests(unittest.TestCase):
    def setUp(self):
        reset()
        self.company = companies.create_company(name="Nimbus")

    def test_versions_are_kept_and_the_latest_is_shown(self):
        first = prep.save("Nimbus", RESULT, "first")
        second = prep.save("Nimbus", dict(RESULT, company_summary="Updated"), "second")
        shown = registry.run("prep.get", {"company": "Nimbus"})
        self.assertEqual(shown["id"], second.id)
        self.assertEqual([v["id"] for v in shown["versions"]], [first.id, second.id])
        old = registry.run("prep.get", {"company": "Nimbus", "version": first.id})
        self.assertEqual(old["result"]["company_summary"], RESULT["company_summary"])

    def test_a_failed_run_does_not_hide_the_last_prep(self):
        done = prep.save("Nimbus", RESULT, "ok")
        prep._store(InterviewPrep(company_id=self.company.id, status="error", error="boom"))
        shown = registry.run("prep.get", {"company": "Nimbus"})
        self.assertEqual(shown["id"], done.id)
        self.assertEqual(shown["run"]["error"], "boom")
        prep.save("Nimbus", RESULT, "again")  # a success clears the failed run
        self.assertIsNone(registry.run("prep.get", {"company": "Nimbus"})["run"])

    def test_only_the_last_versions_are_kept(self):
        for i in range(prep.MAX_VERSIONS + 2):
            prep.save("Nimbus", RESULT, f"v{i}")
        self.assertEqual(len(prep.versions("Nimbus")), prep.MAX_VERSIONS)
        self.assertEqual(prep.get("Nimbus").by, f"v{prep.MAX_VERSIONS + 1}")


class PracticeIsolationTests(unittest.TestCase):
    def setUp(self):
        reset()
        set_cv_email(OWNER)
        config.TAILSCALE_USERS = True
        self.client = TestClient(app)

    def tearDown(self):
        config.TAILSCALE_USERS, config.OWNER = False, ""

    def act(self, login, action, payload=None):
        return self.client.post(f"/api/actions/{action}", json=payload or {}, headers={"Tailscale-User-Login": login})

    def test_each_person_has_their_own_profile(self):
        self.act(FRIEND, "company.create", {"name": "Nimbus"})
        self.act(FRIEND, "practice.record", {"company": "Nimbus", "exercise": "SQL warm-up", "score": 6, "topic": "SQL"})
        self.assertEqual(self.act(FRIEND, "practice.profile").json()["result"]["totals"]["attempts"], 1)
        self.assertEqual(self.act(OWNER, "practice.profile").json()["result"]["totals"]["attempts"], 0)


if __name__ == "__main__":
    unittest.main()
