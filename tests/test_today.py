"""Today: interviews with prep status, follow-ups due, what's waiting, what came back, what to review."""
import unittest
from datetime import date, timedelta

from jobtracker import registry
from jobtracker.models import Recording
from jobtracker.services import companies, practice, prep, recordings
from tests.test_core import reset  # sets the temp data/output env vars first
from tests.test_prep import RESULT


class TodayTests(unittest.TestCase):
    def setUp(self):
        reset()
        self.nimbus = companies.create_company(name="Nimbus", position="Data Analyst")
        self.quiet = companies.create_company(name="Quiet Co", position="Analyst")
        registry.run("process.set_state", {"company": "Quiet Co", "step": "Applied", "state": "done"})
        companies.update_company(self.quiet.id, last_update=(date.today() - timedelta(days=30)).isoformat())

    def test_interviews_come_with_prep_and_practice(self):
        soon = (date.today() + timedelta(days=2)).isoformat()
        later = (date.today() + timedelta(days=30)).isoformat()
        registry.run("process.add_step", {"company": "Nimbus", "name": "Technical interview", "state": "planned",
                                          "date": soon, "time": "14:30", "location": "https://meet.example/x"})
        registry.run("process.add_step", {"company": "Nimbus", "name": "Onsite", "state": "planned", "date": later})
        prep.save("Nimbus", RESULT, "test")
        registry.run("practice.record", {"company": "Nimbus", "exercise": "1", "score": 6})
        t = registry.run("today.get", {})
        mine = [i for i in t["interviews"] if i["company"] == "Nimbus"]
        self.assertEqual([i["step"] for i in mine], ["Technical interview"])  # the one in 30 days is beyond 14 days
        i = mine[0]
        self.assertEqual((i["days"], i["time"], i["location"]), (2, "14:30", "https://meet.example/x"))
        self.assertEqual((i["prep"]["ready"], i["prep"]["exercises"], i["practice_average"]), (True, 1, 6.0))
        self.assertGreaterEqual(t["week"]["interviews_next_7_days"], 1)

    def test_follow_ups_waiting_recent_and_review(self):
        recordings._save(Recording(company_id=self.nimbus.id, file="a.m4a", status="choose_speaker", step_name="HR call"))
        prep.save("Nimbus", RESULT, "test")
        registry.run("practice.record", {"company": "Nimbus", "exercise": "1", "score": 3})
        t = registry.run("today.get", {})
        self.assertIn("Quiet Co", [f["company"] for f in t["follow_ups"]])
        self.assertEqual(t["follow_ups"][0]["days"], max(f["days"] for f in t["follow_ups"]))  # longest silence first
        self.assertIn(("Nimbus", "Pick your voice"), [(w["company"], w["what"]) for w in t["waiting"]])
        self.assertIn("Interview prep ready", [r["what"] for r in t["recent"]])
        self.assertEqual(t["practice"]["to_review"][0]["exercise"], "Top customers by revenue")
        self.assertEqual(t["practice"]["answers_this_week"], len(practice.store.load().attempts))

    def test_deleted_application_disappears(self):
        recordings._save(Recording(company_id=self.nimbus.id, file="a.m4a", status="choose_speaker"))
        companies.delete_company(self.nimbus.id)
        self.assertNotIn("Nimbus", [w["company"] for w in registry.run("today.get", {})["waiting"]])


if __name__ == "__main__":
    unittest.main()
