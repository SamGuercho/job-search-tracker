"""Salary/work model/source fields, saved job descriptions, step types, analytics."""
import unittest

from pydantic import ValidationError

from jobtracker import config, registry
from jobtracker.process import Node
from jobtracker.services import companies
from tests.test_core import reset  # sets the temp data/output env vars first


class FieldTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_new_fields_round_trip_and_validate(self):
        c = registry.run("company.create", {"name": "Nimbus", "work_model": "Hybrid", "source_type": "Job board",
                                            "source_detail": "LinkedIn", "salary_expectation": "38k ILS",
                                            "salary_posted": "30-40k", "job_url": "https://nimbus.example/job"})
        self.assertEqual((c["work_model"], c["source_detail"], c["salary_expectation"]), ("Hybrid", "LinkedIn", "38k ILS"))
        with self.assertRaises(ValidationError):
            registry.run("company.update", {"id": c["id"], "fields": {"work_model": "Sometimes"}})


class JobDescriptionFileTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_saved_in_company_folder_and_archived_on_change(self):
        c = registry.run("company.create", {"name": "Sensi AI", "position": "DS", "job_url": "https://x.io/job",
                                            "job_description": "We need Python."})
        path = config.OUTPUT_DIR / c["job_description_file"]
        self.assertEqual(c["job_description_file"], "Sensi AI/Job_Description.md")
        text = path.read_text()
        self.assertIn("We need Python.", text)
        self.assertIn("https://x.io/job", text)
        registry.run("company.update", {"id": c["id"], "fields": {"notes": "unrelated change"}})
        self.assertFalse((path.parent / "archive").exists())          # unchanged: not rewritten
        registry.run("company.update", {"id": c["id"], "fields": {"job_description": "We need Rust."}})
        self.assertIn("We need Rust.", path.read_text())
        self.assertEqual(len(list((path.parent / "archive").glob("Job_Description_*.md"))), 1)

    def test_no_description_no_file(self):
        c = registry.run("company.create", {"name": "Empty"})
        self.assertIsNone(c["job_description_file"])


class StepTypeTests(unittest.TestCase):
    def test_inferred_and_explicit_types(self):
        self.assertEqual(Node(name="Take-home test").type(), "technical")
        self.assertEqual(Node(name="First HR interview").type(), "hr")
        self.assertEqual(Node(name="Chat with the CTO").type(), "interview")
        self.assertEqual(Node(name="Something", category="technical").type(), "technical")

    def test_offer_type_sets_offer_status(self):
        reset()
        c = companies.create_company(name="X")
        companies.add_step(c.id, "Contract", after="Reference check", state="done")
        companies.update_step(c.id, "Applied", state="done")
        self.assertNotEqual(companies.get_company(c.id).status, "Offer")
        companies.update_step(c.id, "Contract", category="offer")
        self.assertEqual(companies.get_company(c.id).status, "Offer")


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        reset()
        for c in companies.list_companies():  # start without the sample companies
            companies.delete_company(c.id)
        for name in ("A", "B", "C", "D"):
            companies.create_company(name=name, source_type="Job board" if name in "AB" else "Referral / network")
        for name in ("A", "B", "C"):
            companies.update_step(name, "Applied", state="done", date="2026-11-01")
        companies.update_step("A", "Response received", state="done", date="2026-11-05")
        companies.add_step("A", "Technical interview", after="First HR interview", state="done", date="2026-11-10")
        companies.update_step("B", companies.resolve("B").process.closed.id, state="done", date="2026-11-08")

    def test_summary(self):
        a = registry.run("analytics.summary")
        t = a["totals"]
        self.assertEqual((t["applications"], t["applied"], t["not_applied"]), (4, 3, 1))
        self.assertEqual((t["responses"], t["technical_interviews"], t["closed"]), (1, 1, 1))
        self.assertEqual(a["timing"]["days_to_response"]["median"], 4)
        self.assertEqual(a["stopped_at"], [{"stage": "Applied", "count": 1}])
        funnel = {f["key"]: f["count"] for f in a["funnel"]}
        self.assertEqual((funnel["applied"], funnel["response"], funnel["technical"]), (3, 1, 1))
        by_source = {r["group"]: r for r in a["by_source"]}
        self.assertEqual(by_source["Job board"]["applied"], 2)
        self.assertEqual(a["weekly_applications"][0]["count"], 3)


if __name__ == "__main__":
    unittest.main()


class ApiKeyTests(unittest.TestCase):
    """Pasting a key into .env on Windows (Notepad) or with quotes must still work."""

    def tearDown(self):
        import os
        os.environ["OPENAI_API_KEY"] = ""

    def test_key_is_cleaned_before_use(self):
        import os

        from jobtracker.services import llm
        for raw in ("sk-abc123\r", '  "sk-abc123"  ', "'sk-abc123'", "“sk-abc123”", "OPENAI_API_KEY=sk-abc123"):
            os.environ["OPENAI_API_KEY"] = raw
            self.assertEqual(llm.api_key(), "sk-abc123", repr(raw))
        os.environ["OPENAI_API_KEY"] = "  \r "
        self.assertFalse(llm.available())
