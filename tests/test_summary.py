"""Per-application CV summary + AI relevance review (LLM stubbed)."""
import unittest

from docx import Document

from tests.test_core import reset  # sets the temp data/output env vars first
from jobtracker import config, registry
from jobtracker.services import companies, cv_builder, summary


def fake(review):
    return lambda instructions, text, schema: schema.model_validate(review)


REVIEW = {"verdict": "partly relevant", "score": 55, "strengths": ["Python"],
          "missing": [{"requirement": "SQL", "in_cv": True, "evidence": "Wrote complex SQL pipelines"},
                      {"requirement": "Kubernetes", "in_cv": False, "evidence": ""}],
          "suggestion": "Data analyst with strong SQL and Python.", "explanation": "Leads with SQL."}


class SummaryTests(unittest.TestCase):
    def setUp(self):
        reset()
        self.c = companies.create_company(name="Acme", position="Analyst", job_description="We need SQL and Kubernetes.")
        self.orig = summary.llm.structured

    def tearDown(self):
        summary.llm.structured = self.orig

    def test_review_splits_missing_and_saves_nothing(self):
        summary.llm.structured = fake(REVIEW)
        r = registry.run("cv.summary.review", {"company": "Acme"})
        self.assertEqual(r["missing_but_in_cv"][0]["requirement"], "SQL")
        self.assertEqual(r["gaps"], ["Kubernetes"])
        self.assertEqual(r["suggestion"], "Data analyst with strong SQL and Python.")
        self.assertEqual(companies.get_company(self.c.id).cv_summary, "")

    def test_suggestion_with_invented_number_is_rejected(self):
        summary.llm.structured = fake({**REVIEW, "suggestion": "Analyst with 12 years of SQL."})
        r = registry.run("cv.summary.review", {"company": "Acme"})
        self.assertEqual(r["suggestion"], "")
        self.assertIn("12", r["warnings"][0])

    def test_tailored_summary_is_used_only_for_that_application(self):
        registry.run("company.set_cv_summary", {"company": "Acme", "summary": "Tailored for Acme."})
        r = cv_builder.generate_cv(["SQL"], company_id=self.c.id)
        texts = [p.text for p in Document(str(config.OUTPUT_DIR / r["docx"])).paragraphs]
        self.assertIn("Tailored for Acme.", texts)
        other = cv_builder.generate_cv(["SQL"], company_name="Other")
        texts = [p.text for p in Document(str(config.OUTPUT_DIR / other["docx"])).paragraphs]
        self.assertNotIn("Tailored for Acme.", texts)
        registry.run("company.set_cv_summary", {"company": "Acme", "summary": ""})
        self.assertEqual(companies.get_company(self.c.id).cv_summary, "")

    def test_summary_changes_need_approval_and_not_via_update(self):
        self.assertTrue(registry.ACTIONS["company.set_cv_summary"].confirm)
        self.assertTrue(registry.ACTIONS["cv.profile.update"].confirm)
        self.assertFalse(registry.ACTIONS["cv.summary.review"].confirm)
        with self.assertRaises(Exception):
            registry.run("company.update", {"id": self.c.id, "fields": {"cv_summary": "sneaky"}})

    def test_needs_a_job_description(self):
        companies.create_company(name="Blank")
        with self.assertRaises(ValueError):
            registry.run("cv.summary.review", {"company": "Blank"})


if __name__ == "__main__":
    unittest.main()
