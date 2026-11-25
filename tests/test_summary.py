"""Per-application CV summary + AI relevance review (LLM stubbed)."""
import os
import unittest
from contextlib import closing

import openai
from docx import Document
from pydantic import ValidationError

from jobtracker import config, registry
from jobtracker.services import companies, cv_builder, headlines, llm, summary
from jobtracker.telemetry import metrics, store
from tests.test_assistant import FakeOpenAI, call
from tests.test_core import reset  # sets the temp data/output env vars first


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

    def test_the_role_decides_relevance_and_a_wrong_role_suggestion_is_retried(self):
        seen = []

        def answers(*suggestions):
            queue = list(suggestions)

            def structured(instructions, text, schema):
                seen.append((instructions, text))
                return schema.model_validate({**REVIEW, "verdict": "relevant", "score": 90, "suggestion": queue.pop(0)})
            return structured
        companies.update_company(self.c.id, position="Data Scientist")
        summary.llm.structured = answers("AI Engineer with strong SQL.", "Data Scientist with strong SQL and Python.")
        r = registry.run("cv.summary.review", {"company": "Acme", "summary": "Senior AI Engineer building LLM apps."})
        self.assertEqual((r["verdict"], r["score"], r["summary_fits_title"]), ("not relevant", 40, False))
        self.assertEqual((r["cv_title"], r["suggestion"]), ("Data Scientist", "Data Scientist with strong SQL and Python."))
        self.assertIn("CV WRITING GUIDE", seen[0][0])
        self.assertIn("CV TITLE: Data Scientist", seen[0][1])
        self.assertIn("YOUR PREVIOUS SUGGESTION WAS WRONG", seen[1][1])  # one retry, told why

        seen.clear()
        summary.llm.structured = answers("AI Engineer with SQL.", "Machine learning engineer with SQL.")
        r = registry.run("cv.summary.review", {"company": "Acme"})
        self.assertEqual(r["suggestion"], "")  # still the wrong role after the retry: never offered
        self.assertIn("Data Scientist", r["warnings"][0])
        self.assertEqual(len(seen), 2)

    def test_a_new_cv_with_a_wrong_role_summary_comes_with_an_approval_to_fix_it(self):
        registry.run("cv.headline.update", {"id": headlines.default().id, "title": "AI Engineer",
                                            "summary": "Senior AI Engineer shipping LLM products."})

        def structured(instructions, text, schema):
            if schema is summary._Review:
                return schema.model_validate({**REVIEW, "suggestion": "Data Scientist with strong SQL and Python."})
            raise llm.LLMUnavailable("not in this test")  # keyword matching falls back to its rules
        summary.llm.structured = structured
        os.environ["OPENAI_API_KEY"] = "test"
        saved = openai.OpenAI, config.TELEMETRY, config.TELEMETRY_DB
        openai.OpenAI, config.TELEMETRY = FakeOpenAI, True
        config.TELEMETRY_DB = config.DATA_DIR.parent / "summary-telemetry.db"
        try:
            direct = registry.run("workflow.new_application", {"name": "Frog", "position": "Data Scientist",
                                                               "job_description": "We need SQL and Python.",
                                                               "generate_cv": True})
            self.assertEqual(direct["proposals"][0]["action"], "company.set_cv_summary")
            self.assertEqual(direct["proposals"][0]["args"]["summary"], "Data Scientist with strong SQL and Python.")
            self.assertIn("proposals", direct["cv"]["headline"]["next_step"])

            FakeOpenAI.sent = []
            FakeOpenAI.script = [([call("workflow-new_application", {"name": "Toad", "position": "Data Scientist",
                                                                     "job_description": "SQL and Python."})], ""),
                                 ([], "CV made; a Data Scientist summary waits for your approval.")]
            chat = registry.run("chat.send", {"message": "Add this job"})
            pending = [m["tool"] for m in chat["messages"] if m["tool"] and m["tool"]["status"] == "pending"]
            self.assertEqual([t["action"] for t in pending], ["company.set_cv_summary"])
            seen = FakeOpenAI.sent[-1]["input"][0]["output"]
            self.assertIn("proposed_for_approval", seen)
            self.assertNotIn('"proposals"', seen)
            store.flush()
            with closing(store.connect(config.TELEMETRY_DB)) as conn:
                events = store.turn_events(conn, f"{chat['id']}:0")
            tools = sorted((s for s in metrics.spans(events).values() if s.kind == "tool"), key=lambda s: s.start_ns)
            self.assertEqual([(t.start["action"], t.status) for t in tools],
                             [("workflow.new_application", "done"), ("cv.summary.review", "done"),
                              ("company.set_cv_summary", "pending_approval")])
            self.assertTrue(metrics.turn_summary(events)["consistent"])  # nested spans keep the timing checks
            self.assertEqual(chat["messages"][-1]["text"], "CV made; a Data Scientist summary waits for your approval.")
        finally:
            store.flush()
            openai.OpenAI, config.TELEMETRY, config.TELEMETRY_DB = saved
            os.environ["OPENAI_API_KEY"] = ""

    def test_a_review_that_finds_a_misfit_proposes_its_rewrite_once(self):
        companies.update_company(self.c.id, position="Data Scientist")

        verdict = {"value": "relevant", "score": 92, "missing": []}

        def structured(instructions, text, schema):
            if schema is summary._Review:
                return schema.model_validate({**REVIEW, "verdict": verdict["value"], "score": verdict["score"],
                                              "missing": verdict["missing"],
                                              "suggestion": "Data Scientist with strong SQL and Python."})
            raise llm.LLMUnavailable("not in this test")
        summary.llm.structured = structured
        fits = registry.run("cv.summary.review", {"company": "Acme", "summary": "Data Scientist with SQL."})
        self.assertNotIn("proposals", fits)  # relevant, the right role, nothing left to put forward: nothing to change
        verdict["missing"] = REVIEW["missing"]  # relevant, but SQL (in the CV) isn't put forward: rewrite it
        emphasis = registry.run("cv.summary.review", {"company": "Acme", "summary": "Data Scientist with Python."})
        self.assertIn("SQL", emphasis["proposals"][0]["why"])
        verdict.update(value="partly relevant", score=55)
        misfit = registry.run("cv.summary.review", {"company": "Acme", "summary": "Senior AI Engineer."})
        self.assertEqual(misfit["proposals"][0]["args"], {"company": self.c.id,
                                                          "summary": "Data Scientist with strong SQL and Python."})

        os.environ["OPENAI_API_KEY"] = "test"
        saved = openai.OpenAI
        openai.OpenAI = FakeOpenAI
        try:
            FakeOpenAI.sent = []
            title = {"company": self.c.id, "title": "Data Scientist"}
            FakeOpenAI.script = [([call("cv-summary-review", {"company": self.c.id}, "r1"),
                                   call("company-set_cv_title", title, "t1"), call("company-set_cv_title", title, "t2")], ""),
                                 ([], "Both wait for your approval.")]
            chat = registry.run("chat.send", {"message": "Fix the title and the summary"})
        finally:
            openai.OpenAI = saved
            os.environ["OPENAI_API_KEY"] = ""
        pending = [m["tool"]["action"] for m in chat["messages"] if m["tool"] and m["tool"]["status"] == "pending"]
        self.assertEqual(sorted(pending), ["company.set_cv_summary", "company.set_cv_title"])  # the twin title: once

    def test_every_application_cv_reviews_its_summary_until_one_is_approved(self):
        companies.update_company(self.c.id, position="Data Scientist")
        registry.run("cv.headline.update", {"id": headlines.default().id, "title": "Data Scientist",
                                            "summary": "Data Scientist with Python \u2014 from ML to LLMs."})
        calls = []

        def structured(instructions, text, schema):
            if schema is summary._Review:
                calls.append(text)
                return schema.model_validate({**REVIEW, "verdict": "relevant", "score": 70,
                                              "suggestion": "Data Scientist with SQL \u2014 and Python."})
            raise llm.LLMUnavailable("not in this test")
        summary.llm.structured = structured
        os.environ["OPENAI_API_KEY"] = "test"
        try:
            r = registry.run("cv.generate", {"keywords": ["SQL"], "company_id": self.c.id, "export_pdf": False})
            self.assertEqual(len(calls), 1)  # the role fits, yet it was reviewed against the job
            self.assertEqual(r["summary_review"]["emphasize"], ["SQL"])
            self.assertEqual(r["proposals"][0]["args"]["summary"], "Data Scientist with SQL, and Python.")  # no long dash
            text = "\n".join(p.text for p in Document(str(config.OUTPUT_DIR / r["docx"])).paragraphs)
            self.assertNotIn("\u2014", text)
            self.assertNotIn("\u2013", text)  # dates too
            self.assertIn("Data Scientist with Python, from ML to LLMs.", text)

            registry.run("company.set_cv_summary", r["proposals"][0]["args"])  # approved
            again = registry.run("cv.generate", {"keywords": ["SQL"], "company_id": self.c.id, "export_pdf": False})
            self.assertEqual(len(calls), 1)  # adapted and approved: not reviewed again
            self.assertNotIn("proposals", again)
            other = companies.create_company(name="NoJD", position="Data Scientist")
            registry.run("cv.generate", {"keywords": ["SQL"], "company_id": other.id, "export_pdf": False})
            self.assertEqual(len(calls), 1)  # no job description: nothing to review against
        finally:
            os.environ["OPENAI_API_KEY"] = ""

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

    def test_tailored_title_is_used_only_for_that_application(self):
        master_title = registry.run("cv.master.get", {})["profile"]["title"]
        registry.run("company.set_cv_title", {"company": "Acme", "title": "  Staff   Data Scientist "})
        self.assertEqual(companies.get_company(self.c.id).cv_title, "Staff Data Scientist")
        r = cv_builder.generate_cv(["SQL"], company_id=self.c.id, export_pdf=False)
        texts = [p.text.lower() for p in Document(str(config.OUTPUT_DIR / r["docx"])).paragraphs]
        self.assertIn("staff data scientist", texts)
        other = cv_builder.generate_cv(["SQL"], company_name="Other", export_pdf=False)
        texts = [p.text.lower() for p in Document(str(config.OUTPUT_DIR / other["docx"])).paragraphs]
        self.assertNotIn("staff data scientist", texts)
        if master_title:
            self.assertIn(master_title.lower(), texts)
        self.assertTrue(registry.ACTIONS["company.set_cv_title"].confirm)  # CV text: the assistant asks first
        with self.assertRaises(ValidationError):
            registry.run("company.update", {"id": self.c.id, "fields": {"cv_title": "sneaky"}})
        registry.run("company.set_cv_title", {"company": "Acme", "title": ""})
        self.assertEqual(companies.get_company(self.c.id).cv_title, "")

    def test_summary_changes_need_approval_and_not_via_update(self):
        self.assertTrue(registry.ACTIONS["company.set_cv_summary"].confirm)
        self.assertTrue(registry.ACTIONS["cv.profile.update"].confirm)
        self.assertFalse(registry.ACTIONS["cv.summary.review"].confirm)
        with self.assertRaises(ValidationError):
            registry.run("company.update", {"id": self.c.id, "fields": {"cv_summary": "sneaky"}})

    def test_needs_a_job_description(self):
        companies.create_company(name="Blank")
        with self.assertRaises(ValueError):
            registry.run("cv.summary.review", {"company": "Blank"})


if __name__ == "__main__":
    unittest.main()
