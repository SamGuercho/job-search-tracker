"""Job description matching (AI pass stubbed)."""
import unittest

from tests.test_core import reset  # sets the temp data/output env vars first
from jobtracker import registry
from jobtracker.services import jd_match, master_cv

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


if __name__ == "__main__":
    unittest.main()
