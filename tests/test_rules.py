"""Today's coaching rules: to apply, follow-ups and "probably no answer" by stage, readiness, debriefs."""
import unittest
from datetime import date, timedelta
from types import SimpleNamespace as NS

from jobtracker import registry
from jobtracker.services import analytics, companies, prep, rules
from tests.test_core import reset  # sets the temp data/output env vars first

TODAY = date.today()


def ago(days: int) -> str:
    return (TODAY - timedelta(days=days)).isoformat()


class RuleTests(unittest.TestCase):
    def setUp(self):
        reset()
        for c in companies.list_companies():  # start without the example data
            companies.delete_company(c.id)

    def app(self, name, applied_days_ago=None, quiet=None, by="me", **fields):
        c = companies.create_company(name=name, position="Data Scientist", **fields)
        if applied_days_ago is not None:
            registry.run("process.set_state", {"company": c.id, "step": "Applied", "state": "done",
                                               "date": ago(applied_days_ago)})
        if quiet is not None:
            companies.update_company(c.id, last_update=ago(quiet), last_update_by=by)
        return companies.get_company(c.id)

    def test_to_apply_oldest_first_with_age_levels(self):
        for name, days in (("Fresh", 1), ("Week", 8), ("Old", 25), ("Mid", 4)):
            c = self.app(name)
            companies.update_company(c.id, created_at=ago(days))
        self.app("Done", applied_days_ago=2)
        items = rules.to_apply(companies.list_companies())
        self.assertEqual([(i["company"], i["level"], i["stale"]) for i in items],
                         [("Old", "red", True), ("Week", "red", False), ("Mid", "amber", False), ("Fresh", "new", False)])
        self.assertFalse(items[0]["cv_ready"])
        self.assertTrue(items[0]["applied_step_id"])
        registry.run("today.snooze", {"company": "Old"})  # "keep it": no more "still interested?" for a while
        self.assertFalse(rules.to_apply(companies.list_companies())[0]["stale"])

    def test_after_applying(self):
        self.app("NoContact", applied_days_ago=10, quiet=10)
        self.app("WithContact", applied_days_ago=10, quiet=10, referee="Dana")
        self.app("Silent", applied_days_ago=31, quiet=31)
        self.app("Recent", applied_days_ago=3, quiet=3, referee="Dana")
        n = rules.nudges(companies.list_companies())
        self.assertEqual([f["company"] for f in n["follow_ups"]], ["WithContact"])
        self.assertIn("Dana", n["follow_ups"][0]["why"])
        self.assertEqual([(x["company"], x["days"]) for x in n["no_answer"]], [("Silent", 31)])

    def test_after_an_interview(self):
        for name, quiet, by in (("Waiting", 8, "them"), ("Chased", 22, "me"), ("JustChased", 10, "me"), ("Fresh", 3, "them")):
            c = self.app(name, applied_days_ago=40)
            registry.run("process.set_state", {"company": c.id, "step": "First HR interview", "state": "done", "date": ago(quiet)})
            companies.update_company(c.id, last_update=ago(quiet), last_update_by=by)
        scheduled = self.app("Scheduled", applied_days_ago=40, quiet=30)
        registry.run("process.add_step", {"company": scheduled.id, "name": "Technical interview", "state": "planned",
                                          "date": (TODAY + timedelta(days=3)).isoformat()})
        companies.update_company(scheduled.id, last_update=ago(30))
        n = rules.nudges(companies.list_companies())
        self.assertEqual([f["company"] for f in n["follow_ups"]], ["JustChased", "Waiting"])
        self.assertEqual([x["company"] for x in n["no_answer"]], ["Chased"])  # 21+ days after your follow-up
        registry.run("today.snooze", {"company": "Chased", "days": 14})
        self.assertEqual(rules.nudges(companies.list_companies())["no_answer"], [])

    def test_closing_as_no_answer_counts_apart(self):
        self.app("Silent", applied_days_ago=31, quiet=31)
        registry.run("process.close", {"company": "Silent", "reason": rules.NO_ANSWER})
        self.assertEqual(analytics.summary()["totals"]["no_answer"], 1)
        self.assertEqual(rules.nudges(companies.list_companies())["no_answer"], [])

    def test_readiness_and_train_first(self):
        exercises = [NS(title=t, capability=cap, topic="") for t, cap in (("A", "SQL"), ("B", "SQL"), ("C", "Statistics"), ("D", "Coding"))]
        attempts = [NS(company_id="x", exercise="A", score=8, at="1"), NS(company_id="x", exercise="B", score=4, at="2"),
                    NS(company_id="x", exercise="B", score=6, at="3"), NS(company_id="y", exercise="C", score=10, at="4")]
        r = rules.readiness("x", attempts, exercises, needs_work=["Statistics"])
        self.assertEqual((r["score"], r["practised"], r["total"], r["average"], r["level"]), (35, 2, 4, 7.0, "low"))
        self.assertEqual(r["weak"], ["Statistics", "SQL"])
        self.assertEqual(rules.readiness("z", attempts, [])["score"], 0)

        def interview(company, days, score):
            return {"company_id": company, "company": company, "step": "Technical", "days": days, "prep": {},
                    "readiness": {"score": score}}
        pick = rules.train_first([interview("Soon ready", 1, 85), interview("Later", 10, 0), interview("Soon", 2, 30)])
        self.assertEqual(pick["company"], "Soon")  # 70 missing over 3 days beats 100 over 11; 85 is ready
        self.assertIsNone(rules.train_first([interview("Ready", 1, 90)]))

    def test_debriefs_after_an_interview(self):
        c = self.app("Nimbus", applied_days_ago=20)
        registry.run("process.add_step", {"company": c.id, "name": "Technical interview", "state": "planned", "date": ago(1)})
        registry.run("process.add_step", {"company": c.id, "name": "HR call", "state": "done", "date": ago(2), "notes": "went well"})
        registry.run("process.add_step", {"company": c.id, "name": "Old interview", "state": "done", "date": ago(6)})
        items = rules.debriefs(companies.list_companies(), set())
        self.assertEqual([(i["step"], i["days_ago"]) for i in items], [("Technical interview", 1)])
        step = items[0]["step_id"]
        self.assertEqual(rules.debriefs(companies.list_companies(), {step}), [])  # a recording covers it

    def test_scheduling_a_technical_interview_starts_the_prep_once(self):
        started = []
        orig = prep.codex_status, prep.start
        prep.codex_status, prep.start = (lambda: {"available": True}), (lambda ref, *a, **k: started.append(ref))
        try:
            c = self.app("Nimbus", applied_days_ago=5)
            registry.run("process.add_step", {"company": c.id, "name": "HR call", "state": "planned",
                                              "date": (TODAY + timedelta(days=2)).isoformat()})
            self.assertFalse(prep.auto_start(companies.get_company(c.id)))  # not technical
            registry.run("process.add_step", {"company": c.id, "name": "Technical interview", "state": "planned",
                                              "date": (TODAY + timedelta(days=4)).isoformat()})
            for t in __import__("threading").enumerate():
                if t.name.startswith("auto-prep-"):
                    t.join(5)
            self.assertEqual(started, [c.id])
        finally:
            prep.codex_status, prep.start = orig


if __name__ == "__main__":
    unittest.main()
