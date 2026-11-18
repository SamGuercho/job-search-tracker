"""CV import tests (the LLM is replaced by a stub)."""
import unittest

from tests.test_core import TMP, reset  # sets the temp data/output env vars first
from jobtracker import registry
from jobtracker.services import cv_builder, cv_import, llm, master_cv


def fake_structured(instructions, text, schema):
    assert "Existing keyword vocabulary" in instructions
    return schema.model_validate({
        "profile": {"name": "Ada Lovelace", "title": "Analyst", "email": "ada@example.com", "phone": "",
                    "location": "", "linkedin": "", "website": "", "summary": "Mathematician."},
        "experiences": [
            {"company": "Acme Corp", "role": "Senior Data Analyst", "location": "Paris", "start": "2022", "end": "Present",
             "bullets": [{"group": "", "lead": "", "text": "Built forecasting models in R.", "keywords": ["R", "Forecasting", "GenAI"]}]},
            {"company": "Engines Ltd", "role": "Analyst", "location": "London", "start": "1842", "end": "1843",
             "bullets": [{"group": "Engine", "lead": "Notes:", "text": "Wrote the first algorithm.", "keywords": ["Algorithms"]}]}],
        "education": [{"school": "Home", "degree": "Mathematics", "location": "", "start": "", "end": "", "details": ""}],
        "skills": [{"name": "Data", "items": ["SQL", "R"]}],
        "languages": ["English (Native)"],
    })


class ImportTests(unittest.TestCase):
    def setUp(self):
        reset()
        self._orig = llm.structured
        cv_import.llm.structured = fake_structured

    def tearDown(self):
        cv_import.llm.structured = self._orig

    def test_extract_text_from_generated_docx(self):
        r = cv_builder.generate_cv(["SQL"], company_name="Roundtrip")
        text = cv_import.extract_text(TMP / "out" / r["docx"])
        self.assertIn("EXPERIENCE", text)
        self.assertIn("Acme Corp", text)

    def test_extract_text_keeps_bullets_and_bold_labels(self):
        exp = master_cv.get_master().experiences[0]
        master_cv.add_bullet(exp.id, "Shipped a coding assistant.", ["Copilot"], lead="Copilot:")
        r = cv_builder.generate_cv(["Copilot"], company_name="Labels")
        text = cv_import.extract_text(TMP / "out" / r["docx"])
        self.assertIn("• **Copilot:** Shipped a coding assistant.", text)
        self.assertNotIn("**WORK EXPERIENCE**", text)  # fully bold headings are not labels

    def test_parse_returns_unsaved_draft(self):
        r = registry.run("cv.import.parse", {"text": "some cv"})
        self.assertEqual(r["stats"]["experiences"], 2)
        self.assertEqual(master_cv.get_master().profile.name, "Your Name")

    def test_keywords_not_in_text_are_dropped(self):
        draft = registry.run("cv.import.parse", {"text": "cv"})["draft"]
        kws = draft["experiences"][0]["bullets"][0]["keywords"]
        self.assertIn("Forecasting", kws)
        self.assertNotIn("GenAI", kws)

    def test_merge_adds_new_and_keeps_existing(self):
        draft = registry.run("cv.import.parse", {"text": "cv"})["draft"]
        cv = registry.run("cv.import.apply", {"draft": draft, "mode": "merge"})
        self.assertEqual(cv["profile"]["name"], "Your Name")  # not overwritten
        acme = next(e for e in cv["experiences"] if e["company"] == "Acme Corp")
        self.assertIn("Built forecasting models in R.", [b["text"] for b in acme["bullets"]])
        self.assertTrue(any(e["company"] == "Engines Ltd" for e in cv["experiences"]))
        data = next(g for g in cv["skills"] if g["name"] == "Data")
        self.assertEqual(data["items"].count("R"), 1)
        self.assertEqual(data["items"].count("SQL"), 1)

    def test_replace_overwrites(self):
        draft = registry.run("cv.import.parse", {"text": "cv"})["draft"]
        cv = registry.run("cv.import.apply", {"draft": draft, "mode": "replace"})
        self.assertEqual(cv["profile"]["name"], "Ada Lovelace")
        self.assertEqual([e["company"] for e in cv["experiences"]], ["Acme Corp", "Engines Ltd"])

    def test_missing_key_is_reported(self):
        cv_import.llm.structured = self._orig
        import os
        key = os.environ.pop("OPENAI_API_KEY", None)
        try:
            with self.assertRaises(llm.LLMUnavailable):
                registry.run("cv.import.parse", {"text": "cv"})
        finally:
            if key:
                os.environ["OPENAI_API_KEY"] = key


if __name__ == "__main__":
    unittest.main()
