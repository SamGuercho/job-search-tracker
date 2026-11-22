"""Profile (greeting name, LinkedIn) and referral tracking on "To apply"."""
import unittest
from datetime import date, timedelta

from jobtracker import registry
from jobtracker.models import Chat
from jobtracker.services import assistant, companies, master_cv, prep, profile, rules
from tests.test_core import reset  # sets the temp data/output env vars first


class ProfileTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_profile_uses_the_cv_and_a_preferred_first_name(self):
        registry.run("profile.update", {"name": "Samuel Cohen", "linkedin": "linkedin.com/in/samuel"})
        p = registry.run("profile.get", {})
        self.assertEqual((p["first_name"], p["linkedin"]), ("Samuel", "https://linkedin.com/in/samuel"))
        self.assertEqual(master_cv.get_master().profile.name, "Samuel Cohen")  # one place: the CV header
        registry.run("profile.update", {"preferred_name": "Sam"})
        self.assertEqual(registry.run("profile.get", {})["first_name"], "Sam")
        me = registry.run("today.get", {})["me"]
        self.assertEqual(me, {"first_name": "Sam", "linkedin": "https://linkedin.com/in/samuel"})

    def test_referral_status_on_to_apply(self):
        for c in companies.list_companies():
            companies.delete_company(c.id)
        companies.create_company(name="NoRef")
        c = companies.create_company(name="Orbitex", referee="Dana")
        status = lambda: {a["company"]: (a["referral"], a["asked_days"]) for a in rules.to_apply(companies.list_companies())}  # noqa: E731
        self.assertEqual(status(), {"NoRef": ("none", None), "Orbitex": ("to_ask", None)})
        with self.assertRaises(ValueError):
            registry.run("company.referral", {"company": "NoRef"})  # asked whom?
        registry.run("company.referral", {"company": "Orbitex"})
        self.assertEqual(status()["Orbitex"], ("asked", 0))
        self.assertIn("Asked Dana for a referral", companies.get_company(c.id).notes)
        companies.update_company(c.id, referral_asked_at=(date.today() - timedelta(days=3)).isoformat())
        self.assertEqual(status()["Orbitex"], ("remind", 3))
        registry.run("company.referral", {"company": "NoRef", "referee": "Avi", "asked": False})
        self.assertEqual(status()["NoRef"], ("to_ask", None))

    def test_what_you_look_for_flags_jobs_that_dont_fit(self):
        registry.run("profile.update", {"target_roles": ["Data Scientist", "AI Engineer", "data scientist"],
                                        "locations": ["Tel Aviv"], "work_models": ["Hybrid", "Remote"]})
        self.assertEqual(profile.settings().target_roles, ["Data Scientist", "AI Engineer"])  # duplicates dropped
        fits = lambda **f: profile.fit(companies.create_company(name="X", **f))  # noqa: E731
        self.assertEqual(fits(position="Staff Data Scientist", location="Tel Aviv-Yafo", work_model="Hybrid"), [])
        self.assertEqual(fits(position="Senior AI Engineer"), [])
        warnings = fits(position="Product Manager", location="Haifa", work_model="On-site")
        self.assertEqual(len(warnings), 3)
        self.assertIn("not one of your target roles", warnings[0])
        self.assertEqual(fits(position="Data Scientist", location="Berlin", work_model="Remote"), [])  # remote: anywhere
        self.assertIn("fit", rules.to_apply(companies.list_companies())[0])

    def test_salary_goals_language_and_assistant_context(self):
        registry.run("profile.update", {"salary_expectation": "38k ILS/month", "interview_language": "Hebrew",
                                        "goals": {"applications": 5}, "notice_period": "1 month"})
        self.assertEqual(companies.create_company(name="New").salary_expectation, "38k ILS/month")
        self.assertEqual(companies.create_company(name="Own", salary_expectation="40k").salary_expectation, "40k")
        registry.run("profile.update", {"goals": {"practice_answers": 10}})  # merged with the applications goal
        goals = registry.run("today.get", {})["goals"]
        self.assertEqual((goals["applications"], goals["practice_answers"]), ({"done": 0, "goal": 5}, {"done": 0, "goal": 10}))
        self.assertIn("Write all the text in Hebrew", prep.brief("New")["instructions"])
        self.assertIn("Write all the text in English", prep.brief("New", language="English")["instructions"])
        text = assistant._instructions(Chat())
        self.assertIn("salary expectation: 38k ILS/month", text)
        self.assertIn("notice period: 1 month", text)
        registry.run("process.set_state", {"company": "New", "step": "Applied", "state": "done"})  # no date: today
        self.assertEqual(registry.run("today.get", {})["goals"]["applications"]["done"], 1)
        registry.run("profile.update", {"goals": {"applications": 0, "practice_answers": 0}})
        self.assertIsNone(registry.run("today.get", {})["goals"])


if __name__ == "__main__":
    unittest.main()
