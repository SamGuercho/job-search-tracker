"""CV match score: requirements of the job vs the text of the generated CV."""
import unittest

from tests.test_core import reset  # sets the temp data/output env vars first
from jobtracker import registry  # noqa: E402
from jobtracker.models import JobMatch, Requirement  # noqa: E402
from jobtracker.services import companies, cv_builder, cv_score, master_cv  # noqa: E402

JD = "We need strong SQL and Python. Kubernetes is a plus."


class ScoreTests(unittest.TestCase):
    def test_required_weigh_double_and_cut_vs_missing(self):
        job = JobMatch(text_hash="x", requirements=[
            Requirement(term="Python", keywords=["Python"], required=True),
            Requirement(term="vector databases", keywords=["Pinecone"]),
            Requirement(term="Go"),
        ])
        s = cv_score.score(job, "Built APIs in python.")
        self.assertEqual([r.term for r in s.covered], ["Python"])
        self.assertEqual([r.term for r in s.cut], ["vector databases"])
        self.assertEqual([r.term for r in s.missing], ["Go"])
        self.assertEqual((s.score, s.best), (50, 75))  # 2/4 covered, 3/4 possible

    def test_no_requirements_no_score(self):
        self.assertIsNone(cv_score.score(JobMatch(text_hash="x"), "anything"))


class GeneratedCVTests(unittest.TestCase):
    def setUp(self):
        reset()
        exp = master_cv.get_master().experiences[0]
        master_cv.add_bullet(exp.id, "Wrote SQL pipelines feeding the Python models.", ["SQL", "Python"])
        self.c = companies.create_company(name="Orbitex", job_description=JD)

    def test_generation_scores_the_cv_and_keeps_the_requirements(self):
        r = cv_builder.generate_cv(["SQL", "Python"], company_id=self.c.id, export_pdf=False)
        self.assertIsNotNone(r["match"])
        self.assertIn("SQL", [x["term"] for x in r["match"]["covered"]])
        company = companies.get_company(self.c.id)
        self.assertEqual(company.documents[0].match.score, r["match"]["score"])
        self.assertEqual(company.job_match.text_hash, cv_score.text_hash(JD))

    def test_score_action_and_new_description(self):
        cv_builder.generate_cv(["SQL"], company_id=self.c.id, export_pdf=False)
        companies.update_company(self.c.id, job_description="Looking for COBOL and SQL.")
        r = registry.run("cv.match_score", {"company": "Orbitex"})
        self.assertEqual(companies.get_company(self.c.id).job_match.text_hash,
                         cv_score.text_hash("Looking for COBOL and SQL."))
        self.assertIn("SQL", [x["term"] for x in r["match"]["covered"]])

    def test_needs_a_cv(self):
        with self.assertRaises(ValueError):
            registry.run("cv.match_score", {"company": "Orbitex"})


if __name__ == "__main__":
    unittest.main()
