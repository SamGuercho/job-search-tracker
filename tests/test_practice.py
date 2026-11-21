"""Practice log, training profile, prep versions, and the history fed into new preps."""
import unittest

from fastapi.testclient import TestClient

from jobtracker import config, registry
from jobtracker.api import app
from jobtracker.models import InterviewPrep, PracticeAttempt
from jobtracker.services import companies, practice, prep
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



class CapabilityTests(unittest.TestCase):
    def setUp(self):
        reset()
        self.company = companies.create_company(name="Nimbus")
        prep.save(self.company.id, dict(RESULT, exercises=[dict(EXERCISE, capability="sql"), SECOND]), "test")
        self._ask = practice._ask_model

    def tearDown(self):
        practice._ask_model = self._ask

    def record(self, exercise, score, **kw):
        return registry.run("practice.record", {"company": "Nimbus", "exercise": exercise, "score": score, **kw})

    def test_capability_comes_from_the_exercise_or_its_words(self):
        self.assertEqual(self.record("1", 5)["capability"], "SQL")             # given by the prep, normalised
        self.assertEqual(self.record("2", 5)["capability"], "System design")  # kind "system design" beats "pipeline"
        self.assertEqual(self.record("ETL design", 5, topic="airflow pipelines")["capability"], "Data engineering")
        practice._ask_model = lambda items, known: ["Negotiation"]
        self.assertEqual(self.record("Salary talk", 6, topic="offers")["capability"], "Negotiation")  # model
        practice._ask_model = lambda items, known: [""]
        self.assertEqual(self.record("Something odd", 6)["capability"], "Other")

    def test_old_answers_are_sorted_once(self):
        with practice.store.transaction() as book:
            book.attempts += [PracticeAttempt(exercise="Explain p-values", topic="hypothesis tests", score=4),
                              PracticeAttempt(exercise="Pitch yourself", topic="intro", score=7)]
        practice._ask_model = lambda items, known: ["Behavioral"] * len(items)
        self.assertEqual(registry.run("practice.classify", {}), {"sorted": 2, "by_model": 1})
        caps = {a.exercise: a.capability for a in practice.store.load().attempts}
        self.assertEqual(caps, {"Explain p-values": "Statistics", "Pitch yourself": "Behavioral"})
        self.assertEqual(registry.run("practice.classify", {})["sorted"], 0)

    def test_progress_targets_and_order(self):
        for score in [3, 4, 5, 7, 8, 9]:
            self.record("1", score)
        self.record("2", 5)
        registry.run("practice.set_target", {"capability": "sql", "target": 9})
        registry.run("practice.set_target", {"capability": "Statistics", "target": 7})  # not practised yet
        caps = {c["capability"]: c for c in registry.run("practice.capabilities", {})}
        sql = caps["SQL"]
        self.assertEqual((sql["start"], sql["now"], sql["change"], sql["target"], sql["gap"]), (4, 8, 4, 9, 1))
        self.assertEqual([p["moving"] for p in sql["points"]][:3], [3, 3.5, 4])
        self.assertEqual(caps["Statistics"]["attempts"], 0)
        self.assertIsNone(caps["System design"]["change"])  # one answer: no trend yet
        self.assertEqual(registry.run("practice.profile", {})["most_improved"], "SQL")
        registry.run("practice.set_target", {"capability": "SQL"})  # removed
        self.assertIsNone({c["capability"]: c for c in practice.capabilities()}["SQL"]["target"])

    def test_brief_lists_capabilities_and_levels(self):
        self.record("1", 4)
        registry.run("practice.set_target", {"capability": "SQL", "target": 8})
        b = registry.run("prep.brief", {"company": "Nimbus"})
        self.assertIn("System design", b["instructions"])  # names to reuse
        self.assertIn("SQL: now 4/10, target 8/10", b["context"])


    def test_categories_of_unpractised_preps_are_known(self):
        prep.save("Nimbus", dict(RESULT, exercises=[dict(EXERCISE, capability="Forecasting")]), "new prep")
        self.assertIn("Forecasting", practice.known_capabilities())
        self.assertIn("Forecasting", registry.run("prep.brief", {"company": "Nimbus"})["instructions"])
        self.assertEqual(practice.known_capabilities().count("SQL"), 1)  # "sql" from the prep counts once

    def test_merge_moves_answers_target_and_prep_exercises(self):
        for name, score in [("Stats", 4), ("Statistics", 6)]:
            self.record("Explain p-values", score, capability=name)
        registry.run("practice.set_target", {"capability": "Stats", "target": 8})
        prep.save("Nimbus", dict(RESULT, exercises=[dict(EXERCISE, capability="Stats")]), "with stats")
        out = registry.run("practice.rename_capability", {"capability": "stats", "to": "statistics"})
        self.assertEqual(out, {"renamed": 1, "to": "Statistics", "merged": True})
        caps = {c["capability"]: c for c in practice.capabilities()}
        self.assertNotIn("Stats", caps)
        self.assertEqual((caps["Statistics"]["attempts"], caps["Statistics"]["target"]), (2, 8))
        self.assertEqual(prep.get("Nimbus").result.exercises[0].capability, "Statistics")
        with self.assertRaises(ValueError):
            registry.run("practice.rename_capability", {"capability": "Nope", "to": "SQL"})

    def test_rename_keeps_the_target_of_the_destination(self):
        self.record("1", 5, capability="SQL")
        self.record("q", 5, capability="Databases")
        registry.run("practice.set_target", {"capability": "SQL", "target": 9})
        registry.run("practice.set_target", {"capability": "Databases", "target": 6})
        registry.run("practice.rename_capability", {"capability": "Databases", "to": "SQL"})
        self.assertEqual(practice.store.load().targets, {"SQL": 9})


if __name__ == "__main__":
    unittest.main()
