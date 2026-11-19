"""Core behaviour tests. Run: .venv/bin/python -m unittest discover tests"""
import shutil
import unittest

from jobtracker import actions, config, registry  # noqa: E402,F401
from jobtracker.services import companies, cv_builder, master_cv, pdf  # noqa: E402
from tests import TMP, assert_isolated  # tests/__init__.py set up the throwaway folders first


def reset():
    """Fresh, empty test folders. Refuses to continue if the app points anywhere else."""
    assert_isolated()
    shutil.rmtree(TMP, ignore_errors=True)


class KeywordTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_extract_known_keywords_from_description(self):
        found = master_cv.extract_keywords("Strong SQL and Python. SQL is a must. Looker is a plus.")
        self.assertEqual(found[0], "SQL")  # most frequent first
        self.assertIn("Python", found)
        self.assertIn("Looker", found)

    def test_whole_word_match_with_symbols(self):
        self.assertTrue(master_cv.keyword_pattern("C++").search("I write C++ daily"))
        self.assertFalse(master_cv.keyword_pattern("SQL").search("NoSQLish"))
        self.assertTrue(master_cv.keyword_pattern("Time Series").search("time-series models"))

    def test_bullet_matches_only_words_in_text(self):
        b = master_cv.Bullet(lead="Copilot:", text="Built dashboards in Tableau for LLMs", keywords=["Data Visualization"])
        self.assertEqual(master_cv.bullet_matches(b, ["tableau", "Data Visualization", "LLM", "copilot", "SQL"]),
                         ["tableau", "LLM", "copilot"])


class SelectionTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_every_achievement_kept_unmatched_ones_flagged(self):
        cv = cv_builder.select_bullets(["Tableau"])
        total = sum(len(e.bullets) for e in master_cv.get_master().experiences)
        kept = [b for e in cv.experiences for b in e.bullets]
        self.assertEqual(len(kept), total)  # a shallow job description still gets a full CV
        self.assertTrue(all(b.fallback == (not b.matched) for b in kept))
        acme = next(e for e in cv.experiences if e.company == "Acme Corp")
        self.assertTrue(all(b.fallback for b in acme.bullets))

    def test_unmatched_bullets_are_cut_first(self):
        cv = cv_builder.select_bullets(["Tableau"])
        self.assertTrue(cv_builder._remove_weakest(cv).fallback)

    def test_roles_with_no_match_can_be_dropped(self):
        cv = cv_builder.select_bullets(["Tableau"], keep_unmatched_roles=False)
        self.assertNotIn("Acme Corp", [e.company for e in cv.experiences])

    def test_trimming_respects_budget_and_pins(self):
        exp = master_cv.get_master().experiences[0]
        pinned = master_cv.add_bullet(exp.id, "Pinned achievement " * 5, ["Python"], pinned=True)
        for i in range(40):
            master_cv.add_bullet(exp.id, f"Long Python bullet number {i} " * 6, ["Python"])
        cv = cv_builder.fit_estimate(cv_builder.select_bullets(["Python"]), budget=0.97)
        from jobtracker.cv_templates import get_template
        self.assertLessEqual(get_template(None).estimate_pages(cv), 0.97)
        self.assertTrue(cv.removed)
        kept_ids = {b.id for e in cv.experiences for b in e.bullets}
        self.assertIn(pinned.id, kept_ids)


class GenerationTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_generate_writes_docx_in_company_folder_and_links_it(self):
        company = companies.create_company(name="ACME & Sons")
        r = registry.run("cv.generate", {"keywords": ["SQL"], "company_id": company.id})
        self.assertTrue((config.OUTPUT_DIR / r["docx"]).exists())
        self.assertEqual(r["docx"], "ACME & Sons/Your_Name_CV.docx")
        self.assertEqual(companies.get_company(company.id).documents[0].docx, r["docx"])

    def test_templates_render_lead_group_and_languages(self):
        from docx import Document
        exp = master_cv.get_master().experiences[0]
        master_cv.add_bullet(exp.id, "Shipped a coding assistant.", ["Copilot"], lead="Copilot:", group="Atlas Copilot")
        master_cv.set_languages(["French (Native)"])
        for template in ("elegant",):
            r = cv_builder.generate_cv(["Copilot"], company_name=f"T {template}", template=template)
            doc = Document(str(config.OUTPUT_DIR / r["docx"]))
            texts = [p.text for p in doc.paragraphs]
            self.assertIn("Atlas Copilot", texts)
            bullet = next(p for p in doc.paragraphs if p.text.startswith("Copilot:"))
            self.assertTrue(bullet.runs[0].bold)
            self.assertTrue(any("French (Native)" in t for t in texts), template)

    def test_existing_cv_is_archived_not_overwritten(self):
        first = cv_builder.generate_cv(["SQL"], company_name="Again")
        cv_builder.generate_cv(["Python"], company_name="Again")
        archived = list((config.OUTPUT_DIR / "Again" / "archive").glob("Your_Name_CV_*.docx"))
        self.assertEqual(len(archived), 1)
        self.assertTrue((config.OUTPUT_DIR / first["docx"]).exists())

    def test_pdf_loop_removes_bullets_until_one_page(self):
        pages = iter([3, 2, 1])
        originals = pdf.available_engine, pdf.convert, pdf.page_count
        pdf.available_engine = lambda: "fake"
        pdf.convert = lambda d, p: p.write_bytes(b"%PDF") or p
        pdf.page_count = lambda p: next(pages)
        try:
            r = cv_builder.generate_cv(["SQL", "Python"], company_name="Fake")
        finally:
            pdf.available_engine, pdf.convert, pdf.page_count = originals
        self.assertEqual(r["pages"], 1)
        self.assertEqual(len(r["removed"]), 2)


class ActionTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_update_changes_only_given_fields(self):
        c = registry.run("company.create", {"name": "X", "position": "Analyst", "location": "Paris"})
        u = registry.run("company.update", {"id": c["id"], "fields": {"location": "Lyon"}})
        self.assertEqual((u["position"], u["location"]), ("Analyst", "Lyon"))

    def test_workflow_new_application(self):
        r = registry.run("workflow.new_application", {
            "name": "Agent Co", "position": "Data Analyst",
            "job_description": "We use SQL, dbt and Snowflake.", "generate_cv": False})
        self.assertEqual(set(r["keywords"]), {"SQL", "dbt", "Snowflake"})
        self.assertIn("gaps", r)
        self.assertEqual(len(registry.run("company.list", {"query": "Agent"})), 1)

    def test_every_action_has_schema_and_description(self):
        for a in registry.manifest():
            self.assertTrue(a["description"], a["name"])
            self.assertEqual(a["input_schema"]["type"], "object")


if __name__ == "__main__":
    unittest.main()
