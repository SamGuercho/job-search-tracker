"""Courses: study hours, slots before the interview, and the teach/check cycle."""
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from jobtracker import registry
from jobtracker.models import StudyHours
from jobtracker.services import calendar, companies, learning, prep, profile
from tests.test_core import reset  # sets the temp data/output env vars first
from tests.test_prep import EXERCISE, RESULT

SECOND = dict(EXERCISE, title="Design a metrics pipeline", topic="data pipelines", difficulty="hard",
              estimated_min=50)
NOW = datetime.combine(date.today(), datetime.min.time()).replace(hour=7)
EVENINGS = {"work_start": "09:00", "work_end": "18:00", "study_start": "19:00", "study_end": "22:00",
            "weekend_days": [], "max_per_day_min": 90}


class FixedNow(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


def at(s):
    return datetime.fromisoformat(s)


class HoursTests(unittest.TestCase):
    def test_working_hours_are_cut_out_and_weekends_are_friday_saturday(self):
        h = StudyHours(work_start="09:00", work_end="18:00", study_start="08:00", study_end="22:00")
        sunday, friday = date(2026, 11, 22), date(2026, 11, 27)
        self.assertEqual([(s.hour, e.hour) for s, e in learning.windows(sunday, h)], [(8, 9), (18, 22)])
        self.assertEqual(learning.windows(friday, h), [])
        h.weekend_free = True
        self.assertEqual([(s.hour, e.hour) for s, e in learning.windows(friday, h)], [(10, 14)])

    def test_profile_keeps_the_hours_not_given(self):
        reset()
        profile.update(study={"study_start": "20:00", "study_end": "22:30"})
        profile.update(study={"weekend_free": True})
        s = profile.settings().study
        self.assertEqual((s.study_start, s.weekend_free, s.weekend_days), ("20:00", True, [4, 5]))


class CourseTests(unittest.TestCase):
    def setUp(self):
        reset()
        patcher = mock.patch.object(learning, "datetime", FixedNow)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.c = companies.create_company(name="Nimbus", position="Data Analyst")
        prep.save(self.c.id, dict(RESULT, exercises=[EXERCISE, SECOND]), "test")
        self.interview = (date.today() + timedelta(days=6)).isoformat()
        registry.run("process.add_step", {"company": "Nimbus", "name": "Technical interview", "state": "planned",
                                          "date": self.interview, "time": "10:00"})

    def hours(self, **kw):
        profile.update(study={**EVENINGS, **kw})

    def propose(self):
        self.hours()
        return registry.run("learning.propose", {"company": "Nimbus"})

    def test_needs_study_hours(self):
        with self.assertRaises(ValueError):
            registry.run("learning.propose", {"company": "Nimbus"})

    def test_slots_fit_hours_gaps_and_the_interview(self):
        r = self.propose()
        self.assertEqual([c["exercise"] for c in r["courses"]], ["Top customers by revenue", "Design a metrics pipeline"])
        self.assertEqual([c["estimated_min"] for c in r["courses"]], [45, 50])
        interview = at(f"{self.interview}T10:00")
        per_day, spans = {}, []
        for c in r["courses"]:
            teach, c1, c2 = c["sessions"]
            self.assertLess(at(teach["start"]).date(), interview.date())  # teach: the day before at the latest
            self.assertIn((at(c1["start"]).date() - at(teach["start"]).date()).days, (1, 2))
            self.assertIn((at(c2["start"]).date() - at(c1["start"]).date()).days, (3, 4))  # after check 1
            for s in c["sessions"]:
                start = at(s["start"])
                self.assertTrue("19:00" <= s["start"][11:] and start + timedelta(minutes=s["duration_min"]) <= start.replace(hour=22, minute=0))
                self.assertLessEqual(start + timedelta(minutes=s["duration_min"]), interview - timedelta(hours=2))
                per_day[start.date()] = per_day.get(start.date(), 0) + s["duration_min"]
                spans.append((start, start + timedelta(minutes=s["duration_min"])))
        self.assertTrue(all(m <= 90 for m in per_day.values()))
        spans.sort()
        self.assertTrue(all(a[1] <= b[0] for a, b in zip(spans, spans[1:], strict=False)))  # no overlap
        self.assertEqual(learning.agenda()["today"] + learning.agenda()["upcoming"], [])  # nothing booked yet

    def test_no_room_says_so(self):
        self.hours(study_start="19:00", study_end="19:30")
        r = registry.run("learning.propose", {"company": "Nimbus"})
        self.assertGreater(r["unplaced"], 0)
        self.assertIn("weekends are off", r["note"])

    def test_accept_books_and_feeds_the_calendar(self):
        self.propose()
        registry.run("learning.accept", {"company": "Nimbus"})
        self.assertEqual({c["state"] for c in learning.courses("Nimbus")}, {"scheduled"})
        self.assertIn("Learn: window functions", calendar.feed_ics().replace("\r\n ", ""))
        again = registry.run("learning.propose", {"company": "Nimbus"})  # already scheduled: nothing new
        self.assertEqual(again["courses"], [])
        self.assertEqual(len(again["skipped"]), 2)

    def test_teach_then_checks_that_cover_what_was_missed(self):
        self.propose()
        registry.run("learning.accept", {"company": "Nimbus"})
        r = registry.run("practice.record", {"company": "Nimbus", "exercise": "1", "score": 5, "missed": ["Handles ties"]})
        self.assertIn("Check 1", r["learning"]["next"])
        course = next(c for c in learning.courses("Nimbus") if c["exercise"] == "Top customers by revenue")
        check1 = course["sessions"][1]
        brief = registry.run("learning.session", {"session": check1["id"]})
        self.assertEqual(brief["focus"], ["Handles ties"])
        self.assertIn("NEW", brief["instructions"])
        self.assertIn("Handles ties", brief["instructions"])

        def check(session, score):
            return registry.run("practice.record", {"company": "Nimbus", "exercise": "Check · window functions",
                                                    "topic": "window functions", "score": score, "session": session})
        check(check1["id"], 8)
        r = check(course["sessions"][2]["id"], 5)  # weak check 2: a 3rd check
        self.assertIn("3rd check", r["learning"]["next"])
        sessions = r["learning"]["course"]["sessions"]
        self.assertEqual([s["label"] for s in sessions], ["Learn", "Check 1", "Check 2", "Check 3"])
        self.assertIsNotNone(sessions[3]["start"])
        r = check(sessions[3]["id"], 8)
        self.assertEqual((r["learning"]["course"]["state"], r["learning"]["course"]["outcome"]), ("done", "learned"))

    def test_a_drop_after_check_1_also_needs_a_3rd_check_and_two_good_checks_finish(self):
        self.propose()
        registry.run("learning.accept", {"company": "Nimbus"})
        course = learning.courses("Nimbus")[-1]
        ids = [s["id"] for s in course["sessions"]]
        learning.complete(ids[0], 6)
        learning.complete(ids[1], 9)
        self.assertIn("dropped from 9 to 7", learning.complete(ids[2], 7)["next"])

        other = learning.courses("Nimbus")[0]
        ids = [s["id"] for s in other["sessions"]]
        learning.complete(ids[0], 6)
        learning.complete(ids[1], 7)
        r = learning.complete(ids[2], 8)
        self.assertEqual(r["course"]["outcome"], "learned")

    def test_a_new_notion_suggests_checks(self):
        self.hours()
        record = lambda score: registry.run("practice.record", {"company": "Nimbus", "exercise": "Bayes warm-up",
                                                                "topic": "Bayes rule", "score": score})
        r = record(4)["learning"]
        self.assertEqual(r["suggested"]["reason"], "new notion")
        self.assertIsNone(record(5)["learning"])  # already suggested
        registry.run("learning.decline", {"courses": [r["suggested"]["id"]]})
        r = record(8)["learning"]  # first good score after low ones: maybe luck
        self.assertEqual(r["suggested"]["reason"], "first high score")
        booked = registry.run("learning.accept", {"courses": [r["suggested"]["id"]]})["accepted"][0]
        self.assertEqual(booked["state"], "scheduled")
        self.assertTrue(all(s["start"] for s in booked["sessions"][1:]))

    def test_missed_sessions_move_to_the_next_free_slot(self):
        self.propose()
        registry.run("learning.accept", {"company": "Nimbus"})
        first = min((s for c in learning.courses("Nimbus") for s in c["sessions"]), key=lambda s: s["start"])
        moved = learning.reflow(now=at(first["start"]) + timedelta(hours=2))
        self.assertEqual(len(moved), 1)
        self.assertGreater(moved[0]["start"], first["start"])
        self.assertEqual(moved[0]["moved"], 1)

    def test_move_to_a_given_time(self):
        self.propose()
        registry.run("learning.accept", {"company": "Nimbus"})
        teach = learning.courses("Nimbus")[-1]["sessions"][0]
        when = f"{(date.today() + timedelta(days=1)).isoformat()}T07:30"
        r = registry.run("learning.move", {"session": teach["id"], "start": when})
        self.assertEqual(r["session"]["start"], when)


if __name__ == "__main__":
    unittest.main()
