"""The matching evaluation's scoring (no model calls) and its answer keys."""
import json
import unittest

from jobtracker.evals import matching
from jobtracker.models import MasterCV
from tests.test_core import reset  # sets the temp data/output env vars first

JOB = {"id": "j", "cvs": ["a"], "jd": "",
       "keywords": [{"term": "dashboards", "aliases": ["dashboard development"], "required": True,
                     "status": {"a": "covered"}},
                    {"term": "Hebrew", "required": True, "status": {"a": "covered"}},
                    {"term": "Looker", "required": False, "status": {"a": "gap"}},
                    {"term": "A/B testing", "required": True, "status": {"a": "variant"}}],
       "forbidden": ["Data Analyst", "curiosity"]}


def row(term, keywords=(), reword=False):
    return {"jd_term": term, "keywords": list(keywords), "reword": reword}


class ScoringTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_wordings_of_the_same_keyword(self):
        self.assertTrue(matching.same("dashboard development", "dashboards"))
        self.assertTrue(matching.same("PowerBI", "Power BI"))
        self.assertTrue(matching.same("SQL skills", "SQL"))
        self.assertFalse(matching.same("Tableau", "Looker"))
        self.assertFalse(matching.same("data", "data analysis"))

    def test_a_run_is_scored_against_the_answer_key(self):
        result = {"matches": [row("dashboard development", ["dashboards"]), row("Looker", ["Tableau"], reword=True)],
                  "reword": [row("Looker", ["Tableau"], reword=True)],
                  "gaps": [row("Hebrew"), row("Data Analyst"), row("Kubernetes")]}
        s = matching.score_run(JOB, "a", result)
        by = {k["term"]: k for k in s["keywords"]}
        self.assertEqual((by["dashboards"]["got"], by["Hebrew"]["got"], by["Looker"]["got"]),
                         ("covered", "gap", "variant"))
        self.assertFalse(by["A/B testing"]["found"])
        self.assertEqual((s["forbidden"], s["extras"]), (["Data Analyst"], ["Kubernetes"]))

        summary = matching.summarize([s])
        self.assertEqual(summary["recall_required"], round(2 / 3, 3))  # A/B testing missed
        self.assertEqual(summary["recall_optional"], 1.0)
        self.assertEqual((summary["false_gap"], summary["false_cover"], summary["forbidden_hits"]), (1, 1, 1))
        self.assertEqual(summary["precision"], round(3 / 5, 3))       # 5 rows; a forbidden one and an extra
        self.assertEqual(summary["confusion"]["variant -> missing"], 1)

    def test_stability_compares_repeats(self):
        a = {"job": "j", "cv": "a", "extracted": ["SQL", "Python"]}
        b = {"job": "j", "cv": "a", "extracted": ["SQL", "Excel"]}
        self.assertEqual(matching.stability([a, b]), round(1 / 3, 3))

    def test_the_answer_keys_are_well_formed(self):
        data = json.loads(matching.CASES.read_text(encoding="utf-8"))
        for cv in data["cvs"].values():
            MasterCV.model_validate(cv)
        for job in data["jobs"]:
            self.assertTrue(set(job["cvs"]) <= set(data["cvs"]), job["id"])
            for k in job["keywords"]:
                self.assertTrue(set(k["status"]) <= set(job["cvs"]), (job["id"], k["term"]))
                self.assertTrue(set(k["status"].values()) <= set(matching.STATUSES), (job["id"], k["term"]))
