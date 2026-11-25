"""Job description matching (AI pass stubbed)."""
import unittest

from jobtracker import registry
from jobtracker.services import jd_match, master_cv
from tests.test_core import reset  # sets the temp data/output env vars first

JD = """Build AI agents using retrieval-augmented generation and large language models.
Strong Python required. Kubernetes is a plus. Experience with vector databases."""


class JDMatchTests(unittest.TestCase):
    def setUp(self):
        reset()
        exp = master_cv.get_master().experiences[0]
        master_cv.add_bullet(exp.id, "Built an agentic RAG assistant on LLMs with Pinecone.", ["RAG"])

    def run_match(self, use_ai=False):
        return registry.run("cv.keywords.match_job", {"text": JD, "use_ai": use_ai})

    def test_exact_and_synonyms_map_to_cv_wording(self):
        r = self.run_match()
        by_term = {m["jd_term"].lower(): m["keywords"] for m in r["matches"]}
        self.assertIn("RAG", by_term["retrieval-augmented generation"])
        self.assertIn("LLM", by_term["large language model"])
        self.assertIn("Pinecone", by_term["vector database"])
        self.assertIn("Python", by_term["python"])

    def test_gaps_are_real_only(self):
        gaps = [g["jd_term"] for g in self.run_match()["gaps"]]
        self.assertNotIn("Agents", gaps)  # covered by "AI Agents"

    def test_ai_terms_are_restricted_to_cv(self):
        original, original_available = jd_match.llm.structured, jd_match.llm.available
        jd_match.llm.available = lambda: True
        jd_match.llm.structured = lambda i, t, schema: schema.model_validate({"requirements": [
            {"requirement": "semantic search", "must_have": True, "cv_terms": ["Pinecone", "Elastic Cloud"]},
            {"requirement": "Go", "must_have": False, "cv_terms": []}]})
        try:
            r = self.run_match(use_ai=True)
        finally:
            jd_match.llm.structured, jd_match.llm.available = original, original_available
        m = next(m for m in r["matches"] if m["jd_term"] == "semantic search")
        self.assertEqual(m["keywords"], ["Pinecone"])  # invented term dropped
        self.assertTrue(m["must_have"])
        self.assertIn("Go", [g["jd_term"] for g in r["gaps"]])

    def test_suggest_fixes_typos(self):
        self.assertIn("Python", registry.run("cv.keywords.suggest", {"word": "pyhton"}))


class EducationTests(unittest.TestCase):
    """The seed CV has an MSc Data Science and a BSc Mathematics."""

    def setUp(self):
        reset()

    def match(self, text, use_ai=False):
        return registry.run("cv.keywords.match_job", {"text": text, "use_ai": use_ai})

    def test_degree_wordings_and_false_friends(self):
        self.assertEqual(jd_match.degree_mentions("BS/MS in Computer Science"), {1: "BS", 2: "MS"})
        self.assertEqual(jd_match.degree_mentions("M.Sc. or Ph.D. in statistics"), {2: "M.Sc.", 3: "Ph.D."})
        self.assertEqual(jd_match.degree_mentions("Master's degree required"), {2: "Master's"})
        for text in ("p95 under 200 ms", "MS SQL Server", "Boston, MA 02110", "Scrum master", "master data"):
            self.assertEqual(jd_match.degree_mentions(text), {}, text)

    def test_a_degree_the_cv_has_is_covered_by_it(self):
        r = self.match("M.Sc. in a quantitative field. Python.")
        by_term = {m["jd_term"]: m for m in r["matches"]}
        self.assertEqual(by_term["M.Sc."]["keywords"], ["MSc Data Science"])
        self.assertTrue(by_term["M.Sc."]["in_education"])
        self.assertNotIn("M.Sc.", [g["jd_term"] for g in r["gaps"]])

    def test_a_higher_degree_covers_a_lower_one_but_not_the_reverse(self):
        r = self.match("Bachelor's degree required; PhD preferred.")
        covered = {m["jd_term"]: m["keywords"] for m in r["matches"]}
        self.assertEqual(covered["Bachelor's"], ["BSc Mathematics"])  # the degree at that level when there is one
        self.assertIn("PhD", [g["jd_term"] for g in r["gaps"]])

    def test_education_words_count_as_written_in_the_cv(self):
        cv = jd_match._CV()
        self.assertTrue(cv.has("Data Science"))  # in the degree, not in a bullet or skill
        self.assertIn("MSc Data Science", cv.terms())  # the AI can map requirements to the degree

    def test_ai_degree_requirement_is_met_by_the_degree(self):
        original, original_available = jd_match.llm.structured, jd_match.llm.available
        jd_match.llm.available = lambda: True
        jd_match.llm.structured = lambda i, t, schema: schema.model_validate({"requirements": [
            {"requirement": "Master's degree in a quantitative field", "must_have": True, "cv_terms": []}]})
        try:
            r = self.match("A Master's degree in a quantitative field is required.", use_ai=True)
        finally:
            jd_match.llm.structured, jd_match.llm.available = original, original_available
        self.assertEqual(r["gaps"], [])
        self.assertIn("MSc Data Science", r["required"])


if __name__ == "__main__":
    unittest.main()
