"""Title variants: the title + summary at the top of a CV, the closest to each job, with a default."""
import unittest

from docx import Document

from jobtracker import config, registry
from jobtracker.models import MasterCV
from jobtracker.services import companies, cv_builder, headlines, master_cv
from tests.test_core import reset  # sets the temp data/output env vars first


def texts(result):
    return [p.text.lower() for p in Document(str(config.OUTPUT_DIR / result["docx"])).paragraphs]


class HeadlineTests(unittest.TestCase):
    def setUp(self):
        reset()
        self.default = headlines.default()
        registry.run("cv.headline.update", {"id": self.default.id, "title": "AI Engineer", "summary": "AI engineer who ships LLM products."})
        self.ds = registry.run("cv.headline.add", {"title": "Data Scientist", "summary": "Data scientist turning data into decisions.",
                                                   "keywords": ["experimentation"]})
        self.sci = registry.run("cv.headline.add", {"title": "Research Scientist", "summary": "Scientist with published research."})

    def test_old_cvs_become_one_default_variant(self):
        cv = MasterCV.model_validate({"profile": {"name": "Ann", "title": "Analyst", "summary": "I analyse."}})
        self.assertEqual([(h.title, h.summary, h.default) for h in cv.headlines], [("Analyst", "I analyse.", True)])

    def test_closest_title_ignoring_seniority_then_hints_then_default(self):
        staff = companies.create_company(name="Orbitex", position="Staff Data Scientist")
        self.assertEqual(headlines.choose(staff)["headline"].title, "Data Scientist")
        self.assertEqual(headlines.choose(staff)["how"], "closest")
        research = companies.create_company(name="Lab", position="Senior Research Scientist")
        self.assertEqual(headlines.choose(research)["headline"].title, "Research Scientist")
        pm = companies.create_company(name="Acme", position="Product Manager", job_description="Run experimentation programs")
        self.assertEqual(headlines.choose(pm)["headline"].title, "Data Scientist")  # by its hint word
        other = companies.create_company(name="Shop", position="Store Manager")
        pick = headlines.choose(other)
        self.assertEqual((pick["headline"].title, pick["how"]), ("AI Engineer", "default"))

    def test_pinning_and_overrides(self):
        c = companies.create_company(name="Orbitex", position="Staff Data Scientist")
        registry.run("company.set_cv_headline", {"company": c.id, "headline_id": self.sci["id"]})
        top = registry.run("cv.headline.for_application", {"company": c.id})
        # the pinned variant gives the summary; the title stays the job's
        self.assertEqual((top["title"], top["title_from"], top["how"]), ("Staff Data Scientist", "job", "pinned"))
        self.assertEqual(top["summary"], "Scientist with published research.")
        registry.run("company.set_cv_title", {"company": c.id, "title": "Data Scientist · ML"})
        top = registry.run("cv.headline.for_application", {"company": c.id})
        self.assertEqual((top["title"], top["summary"], top["title_tailored"]), ("Data Scientist · ML", "Scientist with published research.", True))
        registry.run("company.set_cv_headline", {"company": c.id, "headline_id": ""})  # back to automatic
        self.assertEqual(registry.run("cv.headline.for_application", {"company": c.id})["how"], "closest")
        registry.run("company.set_cv_title", {"company": c.id, "title": ""})  # back to the job's title
        top = registry.run("cv.headline.for_application", {"company": c.id})
        self.assertEqual((top["title"], top["title_from"], top["title_tailored"]), ("Staff Data Scientist", "job", False))

    def test_generated_cv_has_the_jobs_title_and_the_closest_variants_summary(self):
        c = companies.create_company(name="Orbitex", position="Staff  Data Scientist ")
        r = cv_builder.generate_cv(["SQL"], company_id=c.id, export_pdf=False)
        self.assertEqual((r["headline"]["title"], r["headline"]["title_from"]), ("Staff Data Scientist", "job"))
        self.assertEqual((r["headline"]["variant"], r["headline"]["how"]), ("Data Scientist", "closest"))
        self.assertIn("staff data scientist", texts(r))
        self.assertIn("data scientist turning data into decisions.", texts(r))
        no_position = companies.create_company(name="Lab")
        r = cv_builder.generate_cv(["SQL"], company_id=no_position.id, export_pdf=False)
        self.assertEqual((r["headline"]["title"], r["headline"]["title_from"]), ("AI Engineer", "variant"))
        general = cv_builder.generate_general(export_pdf=False)
        self.assertIn("ai engineer", texts(general))  # no job: the default

    def test_a_single_senior_variant_never_overrides_the_jobs_title(self):
        for h in headlines.variants()[1:]:
            registry.run("cv.headline.delete", {"id": h.id})
        registry.run("cv.headline.update", {"id": headlines.default().id, "title": "Staff Data Scientist"})
        c = companies.create_company(name="Frog", position="Data Scientist")
        r = cv_builder.generate_cv(["SQL"], company_id=c.id, export_pdf=False)
        self.assertEqual(r["headline"]["title"], "Data Scientist")
        self.assertNotIn("staff data scientist", texts(r))

    def test_a_summary_naming_another_role_gets_a_next_step(self):
        fit = headlines.summary_fit
        ai = "Senior AI Engineer with 6+ years building NLP systems."
        self.assertIn("not as the job's", fit(ai, "Data Scientist"))
        self.assertIn("seniority", fit(ai, "AI Engineer"))
        self.assertEqual(fit(ai, "Senior AI Engineer"), "")
        self.assertIn("not as the job's", fit("Data Scientist with 6+ years.", "Data Analyst – Growth"))
        self.assertEqual(fit("Data scientist focused on models.", "Staff Data Scientist"), "")
        self.assertEqual(fit("Machine learning engineer, 6 years.", "ML Engineer"), "")
        c = companies.create_company(name="Frog", position="Research Scientist")
        registry.run("company.set_cv_headline", {"company": c.id, "headline_id": self.default.id})  # the AI Engineer one
        r = cv_builder.generate_cv(["SQL"], company_id=c.id, export_pdf=False)
        self.assertFalse(r["headline"]["summary_fits_job"])
        self.assertIn("cv.summary.review", r["headline"]["next_step"])
        registry.run("company.set_cv_summary", {"company": c.id, "summary": "Research scientist with published work."})
        top = registry.run("cv.headline.for_application", {"company": c.id})
        self.assertTrue(top["summary_fits_job"])  # the application's own summary: nothing to suggest

    def test_editing_rules(self):
        self.assertTrue(all(registry.ACTIONS[a].confirm for a in ("cv.headline.add", "cv.headline.update", "cv.headline.delete")))
        registry.run("cv.headline.update", {"id": self.ds["id"], "make_default": True})
        self.assertEqual(headlines.default().title, "Data Scientist")
        self.assertEqual(master_cv.get_master().profile.title, "Data Scientist")  # the profile mirrors the default
        registry.run("cv.profile.update", {"fields": {"title": "Data Scientist (ML)"}})  # old way: edits the default
        self.assertEqual(headlines.default().title, "Data Scientist (ML)")
        for h in headlines.variants()[:-1]:
            registry.run("cv.headline.delete", {"id": h.id})
        with self.assertRaises(ValueError):
            registry.run("cv.headline.delete", {"id": headlines.variants()[0].id})  # at least one stays
        self.assertTrue(headlines.variants()[0].default)


if __name__ == "__main__":
    unittest.main()
