"""Profile (greeting name, LinkedIn) and referral tracking on "To apply"."""
import unittest
from datetime import date, timedelta

from jobtracker import registry
from jobtracker.services import companies, master_cv, rules
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


if __name__ == "__main__":
    unittest.main()
