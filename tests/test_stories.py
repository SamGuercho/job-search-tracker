"""Story bank: stories in STAR form, coverage of common questions, and how story questions differ from technical
exercises in preps, practice and the study plan."""
import unittest
from datetime import date, timedelta
from unittest import mock

from jobtracker import registry
from jobtracker.models import Exercise
from jobtracker.services import companies, learning, master_cv, prep, profile, stories
from tests.test_core import reset  # sets the temp data/output env vars first
from tests.test_learning import EVENINGS, FixedNow
from tests.test_prep import EXERCISE, RESULT

STORY_Q = dict(EXERCISE, title="Disagreeing with a manager", category="story", kind="behavioral",
               capability="Behavioral", topic="Disagreement", competency="Conflict", stories=["Search migration"],
               solution="Use the migration story: the pushback, the benchmark you ran, the result.",
               estimated_min=0)


def first_bullet():
    e = master_cv.get_master().experiences[0]
    return e.bullets[0].id


class StoryBankTests(unittest.TestCase):
    def setUp(self):
        reset()

    def add(self, **kw):
        body = {"title": "Search migration", "situation": "Slow search", "task": "Make it fast",
                "action": "I benchmarked Elasticsearch", "result": "Queries 10x faster",
                "competencies": ["conflict", "Influence", "Conflict"], **kw}
        return registry.run("story.add", body)

    def test_add_normalises_competencies_and_reports_missing_parts(self):
        s = self.add(result="")
        self.assertEqual(s["competencies"], ["Conflict", "Influence"])  # catalog spelling, no duplicate
        self.assertEqual(s["missing"], ["result"])
        with self.assertRaises(ValueError):
            self.add()  # same title
        with self.assertRaises(ValueError):
            registry.run("story.add", {"title": "  "})

    def test_achievements_must_exist_in_the_master_cv(self):
        bullet = first_bullet()
        s = self.add(achievements=[bullet, bullet])
        self.assertEqual(s["achievements"], [bullet])
        self.assertEqual(s["achievement_texts"][0]["id"], bullet)
        with self.assertRaises(ValueError):
            registry.run("story.update", {"story": s["id"], "achievements": ["nope"]})

    def test_update_changes_only_given_fields_and_finds_by_title(self):
        self.add()
        s = registry.run("story.update", {"story": "search migration", "result": "Queries 12x faster"})
        self.assertEqual(s["result"], "Queries 12x faster")
        self.assertEqual(s["action"], "I benchmarked Elasticsearch")
        self.assertEqual(registry.run("story.get", {"story": "Search"})["id"], s["id"])  # unique part of the title
        registry.run("story.delete", {"story": s["id"]})
        self.assertEqual(registry.run("story.list", {})["stories"], [])

    def test_coverage_and_gaps(self):
        self.add(competencies=["Conflict", "Mentoring"])
        bank = registry.run("story.list", {})
        rows = {r["competency"]: r for r in bank["coverage"]}
        self.assertEqual(rows["Conflict"]["stories"], ["Search migration"])
        self.assertEqual(rows["Mentoring"]["stories"], ["Search migration"])  # a custom competency is listed too
        self.assertNotIn("Conflict", bank["gaps"])
        self.assertIn("Failure", bank["gaps"])
        self.assertNotIn("Mentoring", bank["gaps"])

    def test_delete_needs_approval(self):
        self.assertTrue(registry.ACTIONS["story.delete"].confirm)
        self.assertFalse(registry.ACTIONS["story.add"].confirm)


class PrepSplitTests(unittest.TestCase):
    def setUp(self):
        reset()
        self.c = companies.create_company(name="Nimbus", position="Data Analyst", job_description="SQL.")

    def test_older_behavioral_exercise_is_a_story_question(self):
        old = {k: v for k, v in EXERCISE.items()}
        self.assertEqual(Exercise.model_validate(dict(old, kind="Behavioral")).category, "story")
        self.assertEqual(Exercise.model_validate(old).category, "technical")
        self.assertEqual(Exercise.model_validate(dict(old, kind="behavioral", category="technical")).category, "technical")

    def test_schema_requires_category_and_story_fields(self):
        ex = prep.strict_schema()["$defs"]["Exercise"]
        for name in ("category", "competency", "stories"):
            self.assertIn(name, ex["required"])
        self.assertEqual(ex["properties"]["category"]["enum"], ["technical", "story"])

    def test_brief_gives_the_story_bank(self):
        self.assertIn("none written yet", registry.run("prep.brief", {"company": "Nimbus"})["context"])
        stories.add("Search migration", situation="Slow search", competencies=["Conflict"])
        context = registry.run("prep.brief", {"company": "Nimbus"})["context"]
        self.assertIn("“Search migration” (answers: Conflict)", context)
        self.assertIn("Situation: Slow search", context)

    def test_markdown_lists_story_questions_apart(self):
        saved = prep.save(self.c.id, dict(RESULT, exercises=[EXERCISE, STORY_Q]), "test")
        text = prep.markdown("Nimbus", "", saved.result)
        technical, told = text.split("## Story questions")
        self.assertIn("Top customers by revenue", technical)
        self.assertIn("Disagreeing with a manager (Conflict)", told)
        self.assertIn("**Your story:** “Search migration”", told)


class PracticeAndStudyTests(unittest.TestCase):
    def setUp(self):
        reset()
        patcher = mock.patch.object(learning, "datetime", FixedNow)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.c = companies.create_company(name="Nimbus", position="Data Analyst")
        prep.save(self.c.id, dict(RESULT, exercises=[EXERCISE, STORY_Q]), "test")
        registry.run("process.add_step", {"company": "Nimbus", "name": "Technical interview", "state": "planned",
                                          "date": (date.today() + timedelta(days=6)).isoformat(), "time": "10:00"})
        self.story = stories.add("Search migration", situation="Slow search", competencies=["Conflict"])

    def test_story_questions_are_never_scheduled(self):
        profile.update(study=EVENINGS)
        r = registry.run("learning.propose", {"company": "Nimbus"})
        self.assertEqual([c["exercise"] for c in r["courses"]], ["Top customers by revenue"])
        self.assertEqual(r["skipped"], [])

    def test_a_told_story_is_recorded_on_the_story_without_checks(self):
        r = registry.run("practice.record", {"company": "Nimbus", "exercise": "2", "score": 4,
                                             "missed": ["No result"], "story": "Search migration"})
        self.assertEqual(r["story_id"], self.story.id)
        self.assertIsNone(r["learning"])  # a low first score on a story is not a "new notion" to study
        s = registry.run("story.get", {"story": self.story.id})
        self.assertEqual((s["practice"]["count"], s["practice"]["last_score"]), (1, 4))
        self.assertEqual(s["practice"]["last_missed"], ["No result"])
        # an ad-hoc behavioral question told with a story is logged as behavioral too
        r = registry.run("practice.record", {"company": "Nimbus", "exercise": "Why did you leave?", "score": 6,
                                             "story": self.story.id})
        self.assertEqual(r["kind"], "behavioral")
        with self.assertRaises(ValueError):
            registry.run("practice.record", {"company": "Nimbus", "exercise": "2", "score": 5, "story": "nope"})

    def test_a_technical_answer_still_suggests_checks(self):
        r = registry.run("practice.record", {"company": "Nimbus", "exercise": "1", "score": 4})
        self.assertIsNotNone(r["learning"])


if __name__ == "__main__":
    unittest.main()
